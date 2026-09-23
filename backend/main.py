#!/usr/bin/env python3
"""
加密貨幣篩選終端 — 本機伺服器兼 CoinGecko 代理

用途：把 CoinGecko 的請求改由 Python 在伺服器端發出，瀏覽器只跟 localhost 溝通。
      跨域、沙箱限制、file:// 來源問題一次消失，API Key 也不會出現在瀏覽器裡。

用法：
    把本檔與 crypto-screener.html 放在同一資料夾，然後執行

    python3 screener_server.py
    python3 screener_server.py --key 你的DemoKey        # 建議，額度 30 次/分
    python3 screener_server.py --key 你的ProKey --pro   # 付費方案
    python3 screener_server.py --port 9000
    python3 screener_server.py --lan          # 開放同一個 Wi-Fi 的手機連入

背景預抓（解決第一輪掃不完的問題）：

    python3 screener_server.py --key KEY --prefetch 300

    伺服器會在背景持續把前 300 檔的 90 日資料抓進快取並存到磁碟，
    額度 24 小時都在用，不是只有你盯著畫面時才用。
    瀏覽器之後的掃描直接命中快取，幾乎不需等待，重啟伺服器也不會失效。

提醒通知（在頁面的提醒設定勾選「由本機伺服器轉發」即可使用）：

    python3 screener_server.py --key KEY \
        --tg-token 123:ABC --tg-chat 987654321 \
        --discord https://discord.com/api/webhooks/... \
        --smtp-host smtp.gmail.com --smtp-user you@gmail.com \
        --smtp-pass 應用程式密碼 --mail-to you@gmail.com

    權杖只留在這支程式裡，不會傳到瀏覽器。

    瀏覽器開 http://localhost:8787
    頁面上的「透過本機代理」會自動勾選，直接按重新整理即可。

只用標準函式庫，不需要安裝任何套件。
"""

import argparse
import email.message
import hashlib
import http.server
import socket
import json
import math
import os
import socketserver
import smtplib
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
# 前端建置結果的位置。開發時是 ../frontend/dist，Docker 映像裡是 backend/static
# 第二個服務（例如模擬網）可以設 CG_UPSTREAM=https://正式網服務網址，
# 把 CoinGecko 請求與深度資料時間戳都轉給它：共用快取與月額度，一份額度兩個服務。
CG_UPSTREAM = os.environ.get("CG_UPSTREAM", "").strip().rstrip("/")

STATIC_DIR = os.environ.get("STATIC_DIR") or next(
    (p for p in (os.path.join(HERE, "static"), os.path.join(HERE, "..", "frontend", "dist"))
     if os.path.exists(os.path.join(p, "index.html"))), os.path.join(HERE, "static"))
PUBLIC_BASE = "https://api.coingecko.com/api/v3"
PRO_BASE = "https://pro-api.coingecko.com/api/v3"
# 鏈上資料來源：熱度用 GeckoTerminal，合約安全用 GoPlus
UPSTREAMS = {
    "/api/v3": None,                                    # CoinGecko，另有金鑰處理
    "/api/gt": "https://api.geckoterminal.com/api/v2",
    "/api/gp": "https://api.gopluslabs.io/api/v1",
    "/api/bn": "https://fapi.binance.com",              # Binance USDT 永續合約，公開端點免金鑰
}

CACHE_TTL = 45.0          # 行情快取秒數，重新整理不會重複打 API
STALE_GRACE = 600.0       # 過期後仍可先送舊資料的寬限秒數（背景同時更新）
OHLC_TTL = 1800.0         # 90 日 K 線快取半小時，這種資料不會秒變

START_TS = time.time()
LIVE_BLOCKED = []          # 正式網被拒絕啟動的原因；空代表正常
QUOTA = {"exhausted": False, "ts": 0}
# CoinGecko 回 429 之後的讓路（2026-09-23）：背景工作（滾動補抓、監控補歷史、瀏覽器深掃）在冷卻期間直接讓開、不打上游，
# 把每分鐘額度留給行情榜與監控這種前景請求；以前補掃在 429 後原地重試，前景請求排在後面一起被 429，一等就是二三十秒。
CG_COOL = {"until": 0.0, "hits": 0}
CG_COOL_SEC = 60.0


def cg_cooling():
    return time.time() < CG_COOL["until"]
_cache = {}
_cache_lock = threading.Lock()
# 快取預設放在專案資料夾外，避免執行時在原始碼目錄長出上百 MB 檔案，
# 也避免被建置流程掃描。部署時建議掛 Volume 並設 CACHE_DIR。
CACHE_DIR = os.environ.get("CACHE_DIR") or os.path.join(
    os.environ.get("TMPDIR", "/tmp"), "screener-cache")
DISK_TTL = 12 * 3600.0          # 深度資料寫入磁碟，重啟後仍可用


def _disk_path(key: str) -> str:
    return os.path.join(CACHE_DIR, hashlib.sha1(key.encode()).hexdigest() + ".json")


def cache_peek(key: str):
    """不管 TTL，取出快取內容與年齡（秒）。找不到回 (None, None)。"""
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
    if hit:
        return hit[1], now - hit[0]
    p = _disk_path(key)
    if os.path.exists(p):
        try:
            with open(p, "rb") as f:
                blob = json.loads(f.read())
            body = blob["body"].encode()
            with _cache_lock:
                _cache[key] = (blob["ts"], body)
            return body, now - blob["ts"]
        except Exception:
            pass
    return None, None


_revalidating = set()
_reval_lock = threading.Lock()


def revalidate_async(path_qs, prefix, key):
    """背景更新快取。同一個 key 同時只跑一輪，避免重複打上游。"""
    with _reval_lock:
        if key in _revalidating:
            return
        _revalidating.add(key)

    def run():
        try:
            st, body = fetch_upstream(path_qs, prefix)
            if st == 200:
                cache_put(key, body)
        except Exception:
            pass
        finally:
            with _reval_lock:
                _revalidating.discard(key)

    threading.Thread(target=run, daemon=True).start()


def cache_get(key: str, ttl: float):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    p = _disk_path(key)
    if os.path.exists(p):
        try:
            with open(p, "rb") as f:
                blob = json.loads(f.read())
            if now - blob["ts"] < max(ttl, DISK_TTL if "market_chart" in key else ttl):
                body = blob["body"].encode()
                with _cache_lock:
                    _cache[key] = (blob["ts"], body)
                return body
        except Exception:
            pass
    return None


def cache_put(key: str, body: bytes):
    with _cache_lock:
        _cache[key] = (time.time(), body)
    if "market_chart" not in key and "token_security" not in key:
        return                                   # 只有昂貴的深度資料值得落地
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_disk_path(key), "w") as f:
            json.dump({"ts": time.time(), "key": key, "body": body.decode()}, f)
    except Exception:
        pass
_gate_lock = threading.Lock()
_chain_lock = threading.Lock()
_last_call = [0.0]
_last_chain = [0.0]
_bn_lock = threading.Lock()
_last_bn = [0.0]

CFG = {"key": "", "pro": False, "gap": 2.4}
NOTIFY = {"tg_token": "", "tg_chat": "", "discord": "",
          "smtp_host": "", "smtp_port": 587, "smtp_user": "", "smtp_pass": "", "mail_to": ""}




# ════════════════════════════════════════════════════════════
#  Telegram 機器人配對
#  網頁產生一次性代碼 → 使用者在 Telegram 送出 /start 代碼
#  → 伺服器用 getUpdates 比對後記住 chat_id
#  全程不需要使用者自己查 chat id，權杖也不會經過瀏覽器儲存
# ════════════════════════════════════════════════════════════
_tg = {"token": "", "bot": "", "chats": [], "offset": 0}


# ── 推播錯誤區與設定檔錯誤（BINANCE_LESSONS 第 8 條 r39、r40、r41）──────
# 推播失敗、沒有任何推播管道、設定檔讀寫失敗，都寫進這裡：只寫清單與日誌，不拿鎖、不再推播
# （推播本身壞掉時再推播會遞迴；拿鎖可能死鎖，r37）。/api/trade/status 與自檢都列出來。
NOTIFY_ERR = {"errors": [], "unconfigured": False}
CONF_ERR = {}                 # 設定檔名 → {"error", "loadFailed", "backup", "saveFails"}


def _notify_err(msg):
    NOTIFY_ERR["errors"].append({"ts": int(time.time() * 1000), "msg": str(msg)[:200]})
    del NOTIFY_ERR["errors"][:-50]
    sys.stderr.write(f"  ! 推播失敗：{str(msg)[:160]}\n")


def notify_channels():
    """目前設定了哪些推播管道（名稱清單）。"""
    out = []
    if _tg["token"] and _tg["chats"]:
        out.append("Telegram")
    if NOTIFY["discord"]:
        out.append("Discord")
    if NOTIFY["smtp_host"] and NOTIFY["mail_to"]:
        out.append("電子郵件")
    return out


def _conf_load(label, path, apply_fn):
    """讀設定檔（telegram.json、monitor.json）。r40：讀取失敗不能靜靜回到預設——
    Telegram 設定讀不到時，之後所有告警完全沒送出也沒有紀錄。檔案不存在才是「沒有設定」。
    失敗時壞檔複製一份（原檔留著）、記錯誤區、之後存檔不覆寫原檔（寫到 .unloaded）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            raise ValueError(f"內容不是物件：{type(d).__name__}")
    except FileNotFoundError:
        CONF_ERR.pop(label, None)
        return True
    except Exception as e:
        backup = None
        try:
            import shutil
            backup = f"{path}.bad-{int(time.time())}"
            shutil.copyfile(path, backup)
        except Exception:
            backup = None
        CONF_ERR[label] = {"error": f"{type(e).__name__}: {str(e)[:150]}", "loadFailed": True,
                           "backup": backup, "saveFails": 0}
        _notify_err(f"{label} 讀不到（{type(e).__name__}: {str(e)[:100]}），壞檔另存 {backup or '失敗'}；"
                    f"這段期間的變更寫到 .unloaded，不覆寫原檔")
        return False
    apply_fn(d)
    CONF_ERR.pop(label, None)
    return True


def _conf_save(label, path, data):
    """寫設定檔：先寫暫存檔再換名；讀取失敗期間寫 .unloaded。失敗照節奏推播、恢復時通知（r41）。"""
    ce = CONF_ERR.get(label) or {}
    target = f"{path}.unloaded" if ce.get("loadFailed") else path
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{target}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, target)
    except Exception as e:
        ce = CONF_ERR.setdefault(label, {"loadFailed": False, "saveFails": 0})
        ce["saveFails"] = ce.get("saveFails", 0) + 1
        ce["saveError"] = f"{type(e).__name__}: {str(e)[:150]}"
        n = ce["saveFails"]
        sys.stderr.write(f"  ! {label} 存檔失敗（第 {n} 次）：{ce['saveError']}\n")
        due = trader.alert_due(n) if trader else n in (1, 5, 30)
        if due:
            push_all(f"⚠ 設定存檔失敗（第 {n} 次）", f"{label}：{ce['saveError']}\n網頁上的變更目前只在記憶體裡，重啟就會不見。")
        return False
    n = ce.get("saveFails", 0)
    if n:
        ce["saveFails"] = 0
        ce.pop("saveError", None)
        if not ce.get("loadFailed"):
            CONF_ERR.pop(label, None)
        push_all("設定存檔恢復", f"{label}（已補上：先前存檔失敗 {n} 次）")
    return True
_tg_lock = threading.Lock()
_pending = {}          # code -> {"ts": float, "chat": dict|None}


def tg_file():
    return os.path.join(CACHE_DIR, "telegram.json")


def tg_load():
    _conf_load("telegram.json", tg_file(), _tg.update)
    if not _tg["token"] and NOTIFY["tg_token"]:
        _tg["token"] = NOTIFY["tg_token"]          # 環境變數設定的權杖
    if NOTIFY["tg_chat"] and not any(c["id"] == NOTIFY["tg_chat"] for c in _tg["chats"]):
        _tg["chats"].append({"id": NOTIFY["tg_chat"], "name": "環境變數設定", "ts": time.time()})


def tg_save():
    return _conf_save("telegram.json", tg_file(), dict(_tg))


def tg_api(method, params=None, timeout=30):
    if not _tg["token"]:
        raise RuntimeError("尚未設定 Bot Token")
    url = f"https://api.telegram.org/bot{_tg['token']}/{method}"
    data = urllib.parse.urlencode(params or {}).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def tg_admin_ok(payload):
    need = os.environ.get("ADMIN_KEY", "").strip()
    return (not need) or str(payload.get("admin", "")).strip() == need


def pair_poller(deadline):
    """在配對有效期間輪詢 getUpdates，找出送來對應代碼的聊天室"""
    while time.time() < deadline:
        if not any(v["chat"] is None for v in _pending.values()):
            return
        try:
            j = tg_api("getUpdates", {"offset": _tg["offset"], "timeout": 20}, timeout=35)
        except Exception:
            time.sleep(3)
            continue
        for u in j.get("result", []):
            _tg["offset"] = max(_tg["offset"], u.get("update_id", 0) + 1)
            msg = u.get("message") or {}
            text = (msg.get("text") or "").strip()
            chat = msg.get("chat") or {}
            if not text.startswith("/start"):
                continue
            parts = text.split(maxsplit=1)
            code = parts[1].strip().upper() if len(parts) > 1 else ""
            info = _pending.get(code)
            if not info or info["chat"] is not None:
                continue
            entry = {"id": str(chat.get("id")),
                     "name": chat.get("first_name") or chat.get("title") or "Telegram",
                     "ts": time.time()}
            info["chat"] = entry
            with _tg_lock:
                if not any(c["id"] == entry["id"] for c in _tg["chats"]):
                    _tg["chats"].append(entry)
                tg_save()
            try:
                tg_api("sendMessage", {"chat_id": entry["id"],
                                       "text": "配對成功，之後的訊號提醒會送到這裡。"})
            except Exception:
                pass
        tg_save()


def tg_handle(path, payload):
    """回傳 (status_code, dict)"""
    if path == "/api/tg/status":
        return 200, {"hasToken": bool(_tg["token"]), "bot": _tg["bot"],
                     "chats": _tg["chats"], "adminRequired": bool(os.environ.get("ADMIN_KEY", "").strip())}

    if path == "/api/tg/setup":
        if not tg_admin_ok(payload):
            return 403, {"error": "admin_key_required"}
        token = str(payload.get("token", "")).strip()
        if not token:
            return 400, {"error": "empty_token"}
        old = _tg["token"]
        _tg["token"] = token
        try:
            me = tg_api("getMe", timeout=15)
            if not me.get("ok"):
                raise RuntimeError("getMe 失敗")
            _tg["bot"] = me["result"].get("username", "")
            try:
                tg_api("deleteWebhook", timeout=15)      # 確保 getUpdates 可用
            except Exception:
                pass
            tg_save()
            return 200, {"ok": True, "bot": _tg["bot"]}
        except Exception as e:
            _tg["token"] = old
            return 400, {"error": "invalid_token", "detail": str(e)}

    if path == "/api/tg/pair":
        if not _tg["token"]:
            return 400, {"error": "no_token"}
        code = "".join(__import__("random").choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
        _pending[code] = {"ts": time.time(), "chat": None}
        for k in [k for k, v in _pending.items() if time.time() - v["ts"] > 900]:
            _pending.pop(k, None)
        threading.Thread(target=pair_poller, args=(time.time() + 600,), daemon=True).start()
        return 200, {"code": code, "bot": _tg["bot"],
                     "link": f"https://t.me/{_tg['bot']}?start={code}", "expiresIn": 600}

    if path == "/api/tg/pair/status":
        code = str(payload.get("code", "")).strip().upper()
        info = _pending.get(code)
        if not info:
            return 200, {"state": "expired"}
        if info["chat"]:
            return 200, {"state": "paired", "chat": info["chat"]}
        return 200, {"state": "waiting", "left": int(900 - (time.time() - info["ts"]))}

    if path == "/api/tg/unpair":
        if not tg_admin_ok(payload):
            return 403, {"error": "admin_key_required"}
        cid = str(payload.get("id", "")).strip()
        if not cid:
            return 400, {"error": "沒有指定要解除的聊天室，什麼都沒改"}        # r47、r48：空的不回成功
        if cid not in [str(c.get("id")) for c in _tg["chats"]]:
            return 404, {"error": f"沒有這個聊天室：{cid}"}
        with _tg_lock:
            _tg["chats"] = [c for c in _tg["chats"] if c["id"] != cid]
            tg_save()
        return 200, {"ok": True, "chats": _tg["chats"]}

    return 404, {"error": "not_found"}




# ════════════════════════════════════════════════════════════
#  背景訊號監控
#  把前端的評分引擎搬到伺服器，網頁關掉後仍持續運作。
#  觀察清單與門檻由網頁同步過來，存在磁碟。
# ════════════════════════════════════════════════════════════
try:
    import engine
except Exception:                       # 缺少 engine.py 時只停用監控，其他功能照常
    engine = None

try:
    import trader                       # 模擬單模組；缺少時只停用交易頁
    import preflight                    # 交易所相容性自檢（BINANCE_LESSONS 對應的檢查）
except Exception:
    trader = None
    preflight = None


def _preflight_notify(add):
    """自檢項目（第 8 條 r40、r41）：推播管道沒設定、推播失敗、設定檔讀寫失敗都要列出來。"""
    ch = notify_channels()
    errs = NOTIFY_ERR["errors"]
    if not ch:
        add("推播管道", "fail", "沒有設定任何推播管道（Telegram／Discord／電子郵件）：所有告警只寫在日誌", 8)
    elif errs:
        last = errs[-1]
        add("推播管道", "warn", f"{'、'.join(ch)}；最近 {len(errs)} 筆推播失敗，最後一筆：{last['msg'][:120]}", 8)
    else:
        add("推播管道", "ok", "、".join(ch), 8)
    for label, ce in CONF_ERR.items():
        if ce.get("loadFailed"):
            add(f"設定檔 {label}", "fail", f"讀不到：{ce.get('error')}；壞檔另存 {ce.get('backup') or '—'}，"
                                           f"這段期間的變更寫到 .unloaded", 8)
        if ce.get("saveFails"):
            add(f"設定檔 {label}", "warn", f"存檔連續失敗 {ce['saveFails']} 次：{ce.get('saveError')}", 8)


if preflight is not None:
    # 重新載入模組（測試）時不重複登記
    preflight.EXTRA[:] = [f for f in preflight.EXTRA if getattr(f, "__name__", "") != "_preflight_notify"]
    preflight.EXTRA.append(_preflight_notify)

MON = {"on": False, "watch": [], "cfg": None, "states": {}, "scope": "watch", "topN": 100,
       "history": [], "lastRun": None, "lastCount": 0, "lastError": None,
       "histTTL": 12, "maxRefresh": 8, "lastRefreshed": 0, "callsToday": 0}
_mon_lock = threading.Lock()


def mon_file():
    return os.path.join(CACHE_DIR, "monitor.json")


def mon_load():
    _conf_load("monitor.json", mon_file(), MON.update)


def mon_save():
    return _conf_save("monitor.json", mon_file(),
                      {k: MON[k] for k in ("on", "watch", "cfg", "states", "history",
                                           "lastRun", "lastCount", "scope", "topN",
                                           "histTTL", "maxRefresh", "callsToday")})


def mon_fetch_json(path):
    st, body = fetch_upstream(path)
    if st != 200:
        raise RuntimeError(f"上游回應 {st}")
    return json.loads(body)



def fmt_money(v):
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1:
        return f"{v:,.2f}"
    return f"{v:.6g}"



STAGE_LABEL = {
    "disabled": "未下單",
    "score": "未下單　分數不足",
    "nodata": "未下單　缺少資料",
    "gate": "未下單　條件不符",
    "stop": "未下單　停損無法計算",
    "risk": "未下單　風險額度限制",
    "holding": "未下單　已持有此幣",
    "cooldown": "未下單　冷卻中",
    "failed": "下單失敗",
    "opened": "已下單",
    "filled_issue": "已成交　後續步驟出錯（部位在，詳見另一則告警）",
    "uncertain": "結果不明　回應逾時，可能已成交（對帳會確認）",
}


def trade_outcome_text(o):
    """把下單判斷結果寫成一段文字，附在訊號通知後面。"""
    if not o:
        return "──────\n未下單　原因不明"
    stage = o.get("stage")
    head = STAGE_LABEL.get(stage, "未下單")
    lines = ["──────", head]
    if o.get("why"):
        for w in str(o["why"]).split("；"):
            lines.append(f"· {w}")
    if stage == "opened":
        r = o.get("result") or {}
        lines.append(f"· {r.get('symbol')} {r.get('qty')} 單位，詳情見下一則")
    if o.get("note") and stage != "opened":
        lines.append(f"（另有提醒：{o['note']}）")
    elif o.get("note"):
        lines.append(f"注意　{o['note']}")
    return "\n".join(lines)


def notify_trade_open(r):
    """開倉通知。把這筆的風險講清楚，讓你在手機上就能判斷要不要手動介入。"""
    sz = r.get("sizing") or {}
    ex = r.get("exits") or {}
    side_txt = "▲ 做多" if r["side"] == "LONG" else "▼ 做空"
    lines = [
        f"{side_txt}　{r['symbol']}",
        f"進場　{fmt_money(r['entry'])}",
        f"數量　{r['qty']}　名目 {fmt_money(sz.get('notional'))} U",
        f"停損　{fmt_money(r['stop'])}　風險 {fmt_money(sz.get('riskAmt'))} U（1R）",
        f"目標　{fmt_money(ex.get('tp1'))}（2R 出一半）",
        f"移動停利　{fmt_money(ex.get('trailActivate'))} 啟動，回撤 {ex.get('trailCallback')}%",
    ]
    if r.get("note"):
        lines.append(f"依據　{r['note']}")
    if r.get("warnings"):
        lines.append("注意　" + "；".join(r["warnings"]))
    net = "正式網" if (trader and trader.CFG["live"]) else "模擬網"
    b, _ = trader.account_balance() if trader else (None, None)
    if b:
        lines.append(f"帳戶　權益 {b['equity']:,.2f} U　可用 {b['avail']:,.2f} U")
    lines.append(f"（{net}）")
    return "自動開倉", "\n".join(lines)


def notify_trade_close(t):
    """平倉通知。R 倍數是重點，金額只是附帶。"""
    # 第 8 條 r26：每個欄位都可能是 None（結帳時資料缺欄位會把損益記為未知，r24）。
    # 一律用 .get() 取、數字先確認型別再格式化——通知組不出來，使用者就收不到平倉通知
    pnl, rm = t.get("pnl"), t.get("rMultiple")
    num = (int, float)
    if isinstance(pnl, num):
        win = pnl > 0
        head, mark = ("獲利平倉", "＋") if win else ("虧損平倉", "－")
        pnl_s = f"{mark}{fmt_money(abs(pnl))} U"
    else:
        head, pnl_s = "平倉（損益未知）", "未知（結帳時資料不完整，請到幣安核對）"
    side = t.get("side")
    lines = [
        f"{'▲' if side == 'LONG' else '▼'} {t.get('symbol') or '?'}　{'做多' if side == 'LONG' else '做空' if side == 'SHORT' else '方向未知'}",
        f"進場　{fmt_money(t.get('entry') if isinstance(t.get('entry'), num) else None)}",
        f"出場　{fmt_money(t.get('exit') if isinstance(t.get('exit'), num) else None)}",
        f"損益　{pnl_s}" + (f"　{rm:+.2f}R" if isinstance(rm, num) else ""),
        f"原因　{t.get('reason') or '—'}",
    ]
    op, cl = t.get("opened"), t.get("closed")
    held = (cl - op) / 60000.0 if isinstance(op, num) and isinstance(cl, num) else 0
    if held > 0:
        lines.append(f"持有　{int(held // 60)} 小時 {int(held % 60)} 分")
    for p_ in t.get("partials") or []:
        q_, px_, pp_ = p_.get("qty"), p_.get("px"), p_.get("pnl")
        lines.append(f"其中　{q_ if q_ is not None else '?'} 先在 {fmt_money(px_ if isinstance(px_, num) else None)} 出場"
                     f"（{p_.get('how') or '部分出場'}，{f'{pp_:+.2f}' if isinstance(pp_, num) else '?'} U）")

    if trader:
        p = trader.performance()
        if p.get("count"):
            lines.append("")
            lines.append(f"累計 {p['count']} 筆　勝率 {p.get('winRate')}%　"
                         f"賺賠比 {p.get('payoff') or '—'}　期望值 {p.get('expectancyR')}R"
                         + (f"　（另有 {p['unknown']} 筆損益未知，未計入）" if p.get("unknown") else ""))
            a = trader.AUTO
            lines.append(f"今日 {a['opened']} 筆　已實現 {a['closedR']:+.2f}R（{a.get('closedUsd', 0.0):+.0f} U）"
                         + (f"　另有 {a['unknownToday']} 筆損益未知（未計入）" if a.get("unknownToday") else ""))
            b, _ = trader.account_balance(max_age=0)
            if b:
                lines.append(f"帳戶　權益 {b['equity']:,.2f} U　可用 {b['avail']:,.2f} U"
                             + (f"　未實現 {b['upnl']:+,.2f} U" if abs(b['upnl']) > 0.01 else ""))
            if a.get("blocked"):
                lines.append(f"⚠ {a['blocked']}")
    return head, "\n".join(lines)


NOTIFY_PREFIX = os.environ.get("NOTIFY_PREFIX", "").strip()


def push_all(title, text):
    if NOTIFY_PREFIX:
        title = f"{NOTIFY_PREFIX} {title}"
    for fn in (notify_telegram, notify_discord, notify_email):
        try:
            fn(title, text)
        except Exception as e:
            # 以前 except: pass——推播失敗完全沒有訊息。寫錯誤區（不再推播，避免遞迴；也不經過任何鎖）
            _notify_err(f"{getattr(fn, '__name__', fn)}：{type(e).__name__}: {str(e)[:120]}")
    # r40：一個管道都沒設定時，以前每則告警都靜靜消失。記一次（恢復設定後重新計）
    if notify_channels():
        NOTIFY_ERR["unconfigured"] = False
    elif not NOTIFY_ERR["unconfigured"]:
        NOTIFY_ERR["unconfigured"] = True
        _notify_err(f"沒有設定任何推播管道（Telegram／Discord／電子郵件），告警只寫在日誌：{title}")


def mon_run_once():
    """跑一輪：抓行情 → 取歷史 → 評分 → 判斷訊號 → 發通知"""
    if not (engine and MON["cfg"]):
        return 0
    if MON["scope"] != "top" and not MON["watch"]:
        return 0
    # 監控範圍：觀察清單，或市值前 N 檔
    if MON["scope"] == "top":
        n = max(10, min(250, int(MON.get("topN") or 100)))
        markets = mon_fetch_json(
            f"/coins/markets?vs_currency=usd&order=market_cap_desc&per_page={n}&page=1"
            "&sparkline=false&price_change_percentage=24h,7d,30d")[:n]
    else:
        ids = MON["watch"]
        if not ids:
            return 0
        markets = mon_fetch_json(
            "/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=1"
            "&sparkline=false&price_change_percentage=24h,7d,30d&ids=" + ",".join(ids[:250]))
    rows = []
    refreshed = [0]                 # 本輪實際向上游補抓的檔數
    for c in markets:
        path = f"/coins/{c['id']}/market_chart?vs_currency=usd&days=90"
        key = "/api/v3" + path
        # 90 天歷史一天內不會有意義的變化，快取沿用到 TTL 到期為止。
        # 量能倍數是用「快取基準量 ÷ 即時成交量」現算的，所以訊號仍然即時。
        body = cache_get(key, MON.get("histTTL", 12) * 3600)
        if body is None:
            if refreshed[0] >= MON.get("maxRefresh", 8):
                continue                      # 單輪補抓上限，避免一次把額度用光
            st, body = fetch_upstream(path, background=True)      # 補歷史是背景工作，冷卻期間讓路
            if st != 200:
                continue
            cache_put(key, body)
            refreshed[0] += 1
        try:
            chart = json.loads(body)
        except Exception:
            continue
        base = {
            "id": c["id"], "sym": (c.get("symbol") or "").upper(), "name": c.get("name", ""),
            "price": c.get("current_price"), "vol": c.get("total_volume") or 0,
            "mcap": c.get("market_cap") or 0,
            "m24": c.get("price_change_percentage_24h_in_currency"),
            "m7": c.get("price_change_percentage_7d_in_currency"),
            "m30": c.get("price_change_percentage_30d_in_currency"),
        }
        base["turn"] = (base["vol"] / base["mcap"] * 100) if base["mcap"] else None
        try:
            rows.append(engine.analyze(base, engine.extract_scan(chart, base["vol"])))
        except Exception:
            continue

    now_ms = time.time() * 1000
    events, states = engine.evaluate(rows, MON["cfg"], MON["states"], now_ms)

    # 每項條件的通過率：看不到空頭訊號時，能知道是哪一條卡住
    tally = {"bull": {}, "bear": {}, "n": 0}
    for r in rows:
        if not r.get("scanned"):
            continue
        tally["n"] += 1
        for side, fn in (("bull", engine.bull_gate), ("bear", engine.bear_gate)):
            try:
                _, checks, _ = fn(r, MON["cfg"][side])
            except Exception:
                continue
            for name, ok, _ in checks:
                d = tally[side].setdefault(name, {"pass": 0, "fail": 0})
                d["pass" if ok else "fail"] += 1
    MON["gateStats"] = tally
    with _mon_lock:
        MON["states"] = states
        MON["lastRun"] = now_ms
        MON["lastCount"] = len(rows)
        MON["lastRefreshed"] = refreshed[0]
        MON["callsToday"] = MON.get("callsToday", 0) + 1 + refreshed[0]
        if events:
            MON["history"] = (events + MON["history"])[:400]
        mon_save()

    # 先跑下單判斷，再把結果併進訊號通知，
    # 這樣一則訊息就能回答「有沒有下單、為什麼」。
    by_id = {r["id"]: r for r in rows}
    for e in events:
        title, text = engine.alert_text(e)
        try:
            outcome = auto_try_trade(e, by_id.get(e.get("id")))
        except Exception as ex:
            outcome = {"stage": "failed", "why": f"執行時發生錯誤：{str(ex)[:80]}"}

        text += "\n" + trade_outcome_text(outcome)
        push_all(title, text)

        if outcome.get("stage") == "opened":
            t2, x2 = notify_trade_open(outcome["result"])
            push_all(t2, x2)
            sys.stderr.write(f"  $ 自動開倉 {outcome['result']['symbol']}\n")
        else:
            sys.stderr.write(f"  ~ {e.get('sym')} 未下單（{outcome.get('stage')}）："
                             f"{outcome.get('why')}\n")
    if events:
        sys.stderr.write(f"  ! 背景監控觸發 {len(events)} 則："
                         f"{'、'.join(x['sym'] + '/' + x['side'] for x in events)}\n")
    return len(events)



def auto_try_trade(ev, row):
    """把一則訊號轉成實際下單。

    回傳 dict：
      stage  哪一關結束的（disabled/score/gate/stop/risk/failed/opened）
      why    人看得懂的原因，會一起推播
      note   補充（警告事項等）
      result 成功時的下單結果
    刻意每一關都回傳原因，否則你在手機上只會看到訊號、
    不知道為什麼沒下單。
    """
    if not trader:
        return {"stage": "disabled", "why": "下單模組未載入"}
    if not trader.AUTO["on"]:
        return {"stage": "disabled", "why": "自動下單未啟用"}
    if not row:
        return {"stage": "disabled", "why": "本輪沒有這檔的完整掃描資料"}

    bear = ev.get("side") == "bear"
    sym = (row.get("sym") or "").upper()
    if not sym:
        return {"stage": "disabled", "why": "取不到幣別代號"}

    # 分數優先用訊號事件裡的值：那正是通知上顯示的「評分」，
    # 與 rows 的欄位名不同（rows 用 radar / bearRadar），先前讀錯導致永遠是 None。
    score = ev.get("score")
    if score is None:
        score = row.get("bear") if bear else row.get("radar")   # 空頭分數存在 row["bear"]

    # 持有反向部位：記錄這一刻的 R，並依設定決定是否收緊
    held = trader.STATE["positions"].get(sym + "USDT")
    if held and held["side"] != ("SHORT" if bear else "LONG"):
        try:
            live = trader.live_positions(max_age=5).get(sym + "USDT") or {}
            r_amt = ((held.get("exits") or {}).get("R") or 0) * (held.get("qty") or 0)
            held_r = round(live["pnl"] / r_amt, 2) if (live.get("pnl") is not None and r_amt) else None
            # 先看這則反向訊號能不能過閘門（不下單，只判斷）
            dv_c = None
            try:
                dv_c = fetch_deriv_server(sym, row.get("m24"))
            except Exception:
                pass
            blocks_c, _ = engine.trade_gate({"score": score, "stage": row.get("stage"), "rvol7": row.get("rvol7")}, dv_c, bear)
            gate_ok = (score is not None and score >= trader.AUTO["minScore"] and not blocks_c)
            trader.conflict_record(sym + "USDT", held["side"], held_r, "SHORT" if bear else "LONG", score, gate_ok)
            note = f"持有中的{'多' if held['side'] == 'LONG' else '空'}單收到反向訊號（目前 {held_r:+.2f}R）" if held_r is not None else "持有中的部位收到反向訊號"
            if gate_ok and trader.CFG.get("conflictTighten") and live.get("mark"):
                # 成功與失敗的通知都在移損函式裡產生（第 8 條 r10），這裡不另外推播
                ev2 = trader.move_to_breakeven(held, live["mark"], force=True,
                                               reason=f"持有中的{'多' if held['side'] == 'LONG' else '空'}單收到通過閘門的反向訊號")
                if ev2 and ev2.get("ok"):
                    note += "，已將停損移至成本"
                elif ev2 and ev2.get("retry"):
                    note += "，移損到成本這次沒成功、背景會重試"
            return {"stage": "holding", "why": note + "；反向訊號不反手，已記錄供事後比對"}
        except Exception as e:
            sys.stderr.write(f"  ~ 反向訊號記錄失敗 {sym}: {e}\n")
    if score is None:
        return {"stage": "nodata",
                "why": "取不到這檔的評分（掃描資料可能尚未涵蓋），本輪不下單"}
    if score < trader.AUTO["minScore"]:
        return {"stage": "score",
                "why": f"分數 {engine._fmt1(score)} 未達自動下單門檻 {trader.AUTO['minScore']}"}

    dv = None
    dv_err = None
    try:
        dv = fetch_deriv_server(sym, row.get("m24"))     # m24 由行情端點合併進 row
        if dv is None:
            dv_err = "幣安沒有這檔永續合約，或合約資料取不到"
    except Exception as e:
        dv_err = f"合約資料讀取失敗（{str(e)[:60]}）"

    detail = {"score": score, "stage": row.get("stage"), "rvol7": row.get("rvol7")}
    blocks, warns = engine.trade_gate(detail, dv, bear)
    if blocks:
        return {"stage": "gate", "why": "；".join(blocks), "note": "；".join(warns) or None}

    # 停損用百分比距離，由 stopMode 決定來源（均線／ATR／取近），
    # 實際價位在下單時套到幣安的標記價上。
    price = row.get("price") or ev.get("price")
    pct, sd = engine.stop_pct(row, bear, mode=trader.CFG.get("stopMode", "tighter"),
                              atr_mult=trader.CFG.get("stopAtrMult", 1.5),
                              max_pct=trader.CFG.get("maxStopPct", 12.0),
                              min_pct=trader.CFG.get("minStopPct", 1.5))
    if pct is None or price is None:
        return {"stage": "stop", "why": "算不出合理的停損距離，不送無停損的單"}
    stop = price * (1 + pct / 100.0) if bear else price * (1 - pct / 100.0)

    # 幣安沒有合約就不可能下單，這裡才擋，讓前面的原因先被看到
    if dv is None and dv_err:
        ok_sym, _, msg, _ = trader.check_tradable(sym)
        if not ok_sym:
            return {"stage": "gate", "why": msg}

    src = {"ma": "均線", "atr": "ATR"}.get(sd.get("used"), "?")
    note = f"{'空頭' if bear else '多頭'}雷達 {engine._fmt1(score)} 分，停損 {pct:.1f}%（{src}）"
    if warns:
        note += "（" + "；".join(warns) + "）"

    r = trader.auto_open(sym, "SHORT" if bear else "LONG", price, float(stop), note=note, stop_pct=pct)
    if r.get("ok"):
        return {"stage": "opened", "why": None, "result": r, "note": "；".join(warns) or None}
    if r.get("filled"):
        return {"stage": "filled_issue", "why": r.get("error")}
    if r.get("uncertain"):
        return {"stage": "uncertain", "why": r.get("error")}
    if r.get("skipped"):
        why = r.get("error") or ""
        stage = "holding" if "已有部位" in why else "cooldown" if "冷卻" in why else "risk"
        # 被持倉上限擋掉的，記進影子追蹤，之後回頭看它們的表現
        if "持倉已達" in why:
            try:
                trader.missed_record(sym, row.get("id"), "SHORT" if bear else "LONG",
                                     price, pct, score, why)
            except Exception:
                pass
        return {"stage": stage, "why": why}
    return {"stage": "failed", "why": r.get("error")}


def fetch_deriv_server(base, chg24):
    """伺服器端取衍生品資料，欄位與前端 fetchDeriv 一致。"""
    sym = base.upper() + "USDT"
    q = f"symbol={sym}&period=1h&limit=30"
    def g(p):
        st, b = fetch_upstream(p, "/api/bn")
        return json.loads(b) if st == 200 else None
    oi = g(f"/futures/data/openInterestHist?{q}")
    ls = g(f"/futures/data/globalLongShortAccountRatio?{q}")
    tk = g(f"/futures/data/takerlongshortRatio?{q}")
    pm = g(f"/fapi/v1/premiumIndex?symbol={sym}")
    if not oi and not pm:
        return None
    return engine.deriv_analyze({
        "oi": [float(x["sumOpenInterestValue"]) for x in (oi or [])],
        "ls": [float(x["longShortRatio"]) for x in (ls or [])],
        "taker": [float(x["buySellRatio"]) for x in (tk or [])],
        "funding": float(pm["lastFundingRate"]) if pm else None,
        "fundingHist": [],
        "chg24": chg24,
    })



def hourly_prices(cid):
    """給影子追蹤用：從快取（或補抓）取 90 天逐時價格。"""
    key = _deep_key(cid)
    body, _ = cache_peek(key)
    if body is None:
        st, body = fetch_upstream(f"/coins/{cid}/market_chart?vs_currency=usd&days=90")
        if st != 200:
            return None
        cache_put(key, body)
    try:
        return [(int(t), float(p)) for t, p in json.loads(body).get("prices", [])]
    except Exception:
        return None


def _step(name, fn):
    """背景迴圈的一個步驟：各自接住例外，一步出錯不影響其他步驟（第 8 條 r17）。"""
    try:
        out = fn()
    except Exception as e:
        sys.stderr.write(f"  ! 部位監看［{name}］失敗：{type(e).__name__}: {str(e)[:100]}\n")
        _step_errors[name] = _step_errors.get(name, 0) + 1
        n = _step_errors[name]
        if trader and trader.alert_due(n):
            push_all(f"⚠ 部位監看［{name}］出錯（第 {n} 次）",
                     f"{type(e).__name__}: {str(e)[:150]}\n其他步驟（停損守衛、待平倉重試等）照常執行。")
        return None
    # 第 8 條 r19：恢復時歸零並通知，否則下次出錯會接著舊的次數數
    n = _step_errors.pop(name, 0)
    if n:
        push_all(f"部位監看［{name}］恢復", f"已補上：先前出錯 {n} 次。")
    return out


_step_errors = {}


def position_round(every_s=20):
    """背景迴圈的一輪。以前整輪包在同一個 try 裡：對帳或移損丟一次例外，
    這一輪的停損守衛就不跑、告警也送不出去；持續性的錯誤會讓守衛永遠不跑。
    現在每一步各自隔離，告警在最後一定送出。"""
    if not trader:
        return
    trader.CFG["positionPoll"] = every_s
    if trader.STATE.get("loadError"):
        _step("重試讀取狀態檔", trader.retry_load_state)      # 第 8 條 r38：讀不到時每輪重試
    try:
        if trader.STATE["positions"] or trader.STATE.get("pending"):
            before = len(trader.STATE["trades"])
            _step("對帳", trader.sync_positions)

            # 通知也是一步：它出錯不能讓後面的守衛與待平倉重試（出場）跳過（第 8 條 r20）
            def _notify_closes():
                for t in trader.STATE["trades"][before:]:
                    ti, tx = notify_trade_close(t)
                    push_all(ti, tx)
            _step("平倉通知", _notify_closes)

            def _notify_adopted():
                try:
                    for a in list(trader.ADOPTED):
                        push_all("⚠ 認領未記帳的部位",
                                 f"{a['symbol']} 送單後程式沒記到帳，對帳時在交易所找到並接手。\n"
                                 f"數量 {a['qty']:g}　進場 {a['entry']:g}　停損 {a['stop']:g}"
                                 f"（{'已補掛' if a['stopOk'] else '補掛失敗，守衛會再試'}）\n"
                                 f"只補掛了停損，停利與移動停利沒有掛，請留意。")
                finally:
                    trader.ADOPTED.clear()
            _step("認領通知", _notify_adopted)

            # 主動管理：到 1R 把停損移到成本（失敗記下想要的停損、每輪重試，第 8 條）
            for ev in _step("移損", trader.manage_positions) or []:
                if ev.get("ok"):
                    sys.stderr.write(f"  $ {ev['symbol']} 停損移至成本 {ev['new']:g}\n")
                elif ev.get("skipped"):
                    pass                                # 查不到／部位已沒了：不算失敗，下輪再看
                elif not ev.get("exited"):
                    sys.stderr.write(f"  ! {ev['symbol']} 移損失敗（第 {ev.get('attempt')} 次）：{ev.get('why')}\n")

            # 確認停損還在：每個部位每輪都跑，不受上面步驟影響
            for ev in _step("停損守衛", trader.guard_positions) or []:
                if ev["action"] == "alert":
                    sys.stderr.write(f"  ! {ev['symbol']} 補掛停損失敗（第 {ev['attempt']} 次）：{ev.get('why')}\n")

        # 平倉沒平掉的部位：每輪重試（第 8 條 r13）。待平倉期間只有這條路會送平倉單（r14）
        if any(p.get("pendingClose") for p in trader.STATE["positions"].values()):
            _step("待平倉重試", trader.retry_pending_closes)

        # 平倉後撤不掉的條件單：每輪重試（第 13 條）
        if trader.STATE.get("leftovers"):
            _step("殘留單重試", trader.retry_leftovers)
    finally:
        # 統一送出：所有在 trader 裡產生的告警與恢復——前面任何一步出錯都一定送
        for a in trader.drain_alerts():
            push_all(a["title"], a["text"])


_tick_errors = [0]


def position_tick(every_s=20):
    """position_round 的外層（第 8 條 r25、r26）：各步驟已各自 try，但 position_round 本身出錯
    （例如某筆部位資料不是字典）以前只寫錯誤區、不推播。現在照節奏推播、恢復時通知；執行緒不會因此結束。"""
    try:
        position_round(every_s)
    except Exception as e:
        _tick_errors[0] += 1
        n = _tick_errors[0]
        sys.stderr.write(f"  ! 部位監看失敗（第 {n} 次）：{type(e).__name__}: {str(e)[:100]}\n")
        if (trader.alert_due(n) if trader else n in (1, 5, 30)):
            push_all(f"⚠ 部位監看整輪出錯（第 {n} 次）",
                     f"{type(e).__name__}: {str(e)[:150]}\n這一輪的對帳、守衛、待平倉重試可能都沒跑，下一輪會再試。")
        return
    n = _tick_errors[0]
    _tick_errors[0] = 0
    if n:
        push_all("部位監看恢復", f"已補上：先前整輪出錯 {n} 次。")


def position_worker(every_s=20):
    """獨立的部位監看執行緒：每輪呼叫 position_round。

    停損停利是掛在幣安上的，觸發不需要我們輪詢；這裡只是盡快「發現」它成交了，
    好記錄績效與推播，並確認停損還在。每 20 秒一次只用掉不到 1% 的權重。
    """
    while True:
        position_tick(every_s)
        try:
            busy = trader and (trader.STATE["positions"] or trader.STATE.get("pending") or trader.STATE.get("leftovers"))
        except Exception:
            busy = True
        time.sleep(every_s if busy else 60)



_mon_errors = [0]


def monitor_round():
    """背景監控的一輪（會自動下單）。第 8 條 r23：背景執行緒裡的例外不會回到主迴圈，
    以前只記在 MON["lastError"] 與錯誤區、不推播。現在出錯照節奏推播、恢復時通知。"""
    try:
        if MON["on"]:
            mon_run_once()          # 額度用盡會自動降級為無金鑰，仍可續跑
        if trader:
            try:
                trader.missed_evaluate(hourly_prices)
            except Exception as e:
                sys.stderr.write(f"  ~ 影子追蹤評估失敗：{e}\n")
        MON["lastError"] = None
        n = _mon_errors[0]
        _mon_errors[0] = 0
        if n:
            push_all("背景監控恢復", f"已補上：先前出錯 {n} 次。")
    except Exception as e:
        MON["lastError"] = str(e)
        _mon_errors[0] += 1
        n = _mon_errors[0]
        sys.stderr.write(f"  ! 背景監控失敗（第 {n} 次）：{type(e).__name__}: {e}\n")
        if (trader.alert_due(n) if trader else n in (1, 5, 30)):
            push_all(f"⚠ 背景監控出錯（第 {n} 次）",
                     f"{type(e).__name__}: {str(e)[:150]}\n這一輪的訊號掃描與自動下單沒有跑完，下一輪會再試。")


def monitor_worker(interval_min):
    time.sleep(8)
    while True:
        monitor_round()
        time.sleep(max(60, interval_min * 60))



def validate_fields(payload, spec, meta=("adminKey", "admin")):
    """整批驗證（BINANCE_LESSONS 第 8 條 r40、r41）：回傳 (要套用的值, 不合法的欄位說明)。
    有任何一個不合法，呼叫端就整批不套用、回報是哪幾個欄位。以前一邊迴圈一邊套用：
    值轉型失敗就 continue 跳過、超出範圍就悄悄夾到邊界，其他欄位照套、畫面顯示成功。
    spec：欄位 → (類型, 下限或選項, 上限)；類型 float／int／bool／enum。欄位名稱與值都要驗。"""
    kw, bad = {}, []
    for k, v in (payload or {}).items():
        if k in meta:
            continue
        if k not in spec:
            bad.append(f"{k}（不認得的欄位）")
            continue
        kind, lo, hi = spec[k]
        if kind == "enum":
            if isinstance(v, str) and v in lo:
                kw[k] = v
            else:
                bad.append(f"{k}={v!r}（只能是 {'／'.join(lo)}）")
            continue
        if kind == "bool":
            if v in (True, False, 0, 1, "0", "1", "true", "false"):
                kw[k] = v in (True, 1, "1", "true")
            else:
                bad.append(f"{k}={v!r}（只能是 0／1）")
            continue
        if isinstance(v, bool) or v is None:
            bad.append(f"{k}={v!r}（要是數字）")
            continue
        try:
            x = float(v)
        except (TypeError, ValueError):
            bad.append(f"{k}={v!r}（要是數字）")
            continue
        if not math.isfinite(x):
            bad.append(f"{k}={v!r}（要是有限的數字）")
            continue
        if kind == "int" and x != int(x):
            bad.append(f"{k}={v!r}（要是整數）")
            continue
        if not (lo <= x <= hi):
            bad.append(f"{k}={v!r}（範圍 {lo:g}～{hi:g}）")
            continue
        kw[k] = int(x) if kind == "int" else x
    return kw, bad


TRADE_CFG_SPEC = {
    "riskPct": ("float", 0.1, 5.0), "maxPositions": ("int", 1, 20), "leverage": ("int", 1, 20),
    "stopAtrMult": ("float", 0.5, 5.0), "tp1R": ("float", 1.0, 10.0), "tp1Portion": ("float", 0.0, 1.0),
    "trailCallback": ("float", 0.1, 10.0), "trailActivateR": ("float", 0.5, 10.0),
    "trailR": ("float", 0.0, 3.0), "breakevenR": ("float", 0.0, 5.0), "guardClose": ("bool", 0, 1),
    "maxStopPct": ("float", 3.0, 25.0), "minStopPct": ("float", 0.5, 5.0), "conflictTighten": ("bool", 0, 1),
    "usablePct": ("float", 20.0, 100.0), "useTier": ("bool", 0, 1),
    "stopMode": ("enum", ("ma", "atr", "tighter"), None),
}
AUTO_SPEC = {
    "on": ("bool", 0, 1), "maxPerDay": ("int", 1, 50), "cooldownMin": ("int", 0, 1440),
    "cooldownWinMin": ("int", 0, 1440), "minScore": ("int", 0, 100), "dailyLossR": ("float", -20.0, 20.0),
}
MON_SPEC = {
    "watch": ("list", None, None), "scope": ("enum", ("top", "watch"), None), "topN": ("int", 10, 250),
    "histTTL": ("float", 2, 72), "maxRefresh": ("int", 0, 50), "cfg": ("dict", None, None), "on": ("bool", 0, 1),
}


def _no_fields(payload, meta=("adminKey", "admin")):
    """第 8 條 r45：一個要改的欄位都沒有 → 回錯誤（以前回成功，其實什麼都沒改）。"""
    if not [k for k in (payload or {}) if k not in meta]:
        return 400, {"ok": False, "error": "沒有要改的欄位，什麼都沒改"}
    return None


def _invalid(bad):
    return 400, {"ok": False, "invalid": bad,
                 "error": "設定沒有套用（有不合法的值，整批不套用）：" + "、".join(bad)}


ENGINE_WAIT = 10.0          # 網頁交易操作等引擎鎖的上限（秒），拿不到就回「背景正在處理」（第 8 條 r48）
TRADE_UNLOCKED = ("/api/trade/check", "/api/trade/preflight")   # 只查交易所、不動帳本，不必等背景那輪


def _trade_locked(path, payload):
    """網頁交易操作拿引擎鎖再做（第 8 條 r48）：背景的對帳／出場管理／守衛正在查交易所時，
    手動平倉、改設定不能插進去。等 ENGINE_WAIT 秒拿不到就回 503，不讓請求一直掛著。
    狀態頁只等 2 秒，拿不到就不拿鎖讀一份（讀的途中帳本被改動就重讀），並標記 engineBusy。"""
    if trader is None or path in TRADE_UNLOCKED:
        return trade_handle(path, payload)
    status = path == "/api/trade/status"
    try:
        with trader.engine_wait(2.0 if status else ENGINE_WAIT), trader.engine_section():
            return trade_handle(path, payload)
    except trader.EngineBusy:
        if not status:
            return 503, {"ok": False, "busy": True,
                         "error": "背景正在處理部位（對帳、移損或守衛），這次操作沒有執行，請稍後再試"}
    for i in range(3):
        try:
            code, body = trade_handle(path, payload)
            if isinstance(body, dict):
                body["engineBusy"] = True
            return code, body
        except RuntimeError:                  # 讀的途中帳本被背景改動（dictionary changed size…）
            time.sleep(0.2)
    return 503, {"ok": False, "busy": True, "error": "背景正在處理部位，狀態稍後更新"}


def trade_handle_safe(path, payload):
    """網頁交易操作（手動開倉、平倉、撤孤兒單…）的外層：第 8 條 r23。
    請求處理也是另一條執行緒，例外穿出去時網頁只看到連線中斷、沒有推播。"""
    try:
        return _trade_locked(path, payload)
    except Exception as e:
        sys.stderr.write(f"  ! 網頁交易操作 {path} 出錯：{type(e).__name__}: {e}\n")
        push_all("⚠ 網頁交易操作出錯", f"{path}\n{type(e).__name__}: {str(e)[:150]}")
        return 500, {"ok": False, "error": f"{type(e).__name__}: {str(e)[:150]}"}


def trade_handle(path, payload):
    """模擬單相關端點。金鑰只留在伺服器，前端永遠拿不到。"""
    if trader is None:
        return 500, {"error": "trader_module_missing"}

    if path == "/api/trade/status":
        st = trader.status()
        st["adminRequired"] = bool(os.environ.get("ADMIN_KEY", "").strip())
        st["diskMB"] = cache_disk_mb()
        st["liveBlocked"] = list(LIVE_BLOCKED)
        st["signalSource"] = {
            "monitorOn": MON["on"],
            "monitorSynced": bool(MON["cfg"]),
            "lastRun": MON.get("lastRun"),
            "scope": MON.get("scope"),
            "lastPush": MON.get("lastPushTs"),
        }
        st["cgCooldown"] = {"active": cg_cooling(), "until": int(CG_COOL["until"] * 1000), "hits": CG_COOL["hits"]}
        st["poll"] = float(os.environ.get("POSITION_POLL", 20))
        st["readiness"] = live_readiness()
        st["notify"] = {"channels": notify_channels(), "errors": NOTIFY_ERR["errors"][-10:],
                        "confErrors": {k: dict(v) for k, v in CONF_ERR.items()}}
        return 200, st

    if path == "/api/trade/check":
        base = str(payload.get("symbol", "")).strip()
        if not base:
            return 400, {"error": "missing_symbol"}
        ok, sym, msg, info = trader.check_tradable(base)
        out = {"ok": ok, "symbol": sym, "message": msg}
        if info:
            out["filters"] = info
            px = trader.mark_price(sym) if ok else None
            out["markPrice"] = px
        return 200, out

    if path == "/api/trade/preflight":
        if not preflight:
            return 503, {"error": "自檢模組未載入"}
        # 60 秒內重複呼叫回傳上次結果：自檢會打一次權重 40 的查詢，
        # 這個 GET 不需要管理金鑰，不能讓它被反覆觸發而把 IP 打到限流（第 6 條）
        last = preflight.LAST.get("at") or 0
        cached = time.time() * 1000 - last < 60000 and preflight.LAST["results"]
        res = preflight.LAST["results"] if cached else preflight.run()
        return 200, {"at": preflight.LAST["at"], "version": preflight.VERSION,
                     "results": res, "cached": bool(cached)}

    need = os.environ.get("ADMIN_KEY", "").strip()
    if need and str(payload.get("adminKey", "")) != need:
        return 403, {"error": "admin_key_required"}

    if path == "/api/trade/cancel_orphan":
        r = trader.cancel_orphan(str(payload.get("symbol", "")), str(payload.get("algoId", "")))
        if preflight and r.get("ok"):
            preflight.LAST["at"] = 0          # 下次自檢重新查，不用快取
        return (200 if r.get("ok") else 400), r

    if path == "/api/trade/config":
        if _no_fields(payload):
            return _no_fields(payload)
        kw, bad = validate_fields(payload, TRADE_CFG_SPEC)
        if bad:
            return _invalid(bad)
        out = {"ok": True, "cfg": trader.configure(**kw)}
        if trader.STATE.get("loadError"):
            out["warning"] = ("持倉紀錄還沒載入：這次的設定只寫到旁邊的 .unloaded，"
                              "狀態檔讀到之後會以狀態檔裡的設定為準")
        return 200, out

    if path == "/api/trade/exclude":
        tid = str(payload.get("id", ""))
        ok = trader.set_excluded(tid, payload.get("excluded", True))
        return (200 if ok else 404), {"ok": ok, "perf": trader.performance()}

    if path == "/api/trade/cleanup":
        dry = str(payload.get("dry", "")) in ("1", "true", "True")
        st = cache_cleanup(dry_run=dry)
        if not dry:
            LAST_CLEAN["by"] = "manual"
        st["diskMB"] = cache_disk_mb()
        st["maxAgeH"] = CACHE_MAX_AGE_H
        st["lastAuto"] = (int((time.time() - LAST_CLEAN["ts"]) / 60)
                          if LAST_CLEAN["ts"] else None)
        st["lastBy"] = LAST_CLEAN["by"]
        return 200, st

    if path == "/api/trade/auto":
        # r40：以前 on 先套用、後面的 int() 丟例外——自動下單已經打開，其他欄位沒套用，網頁看到 500
        if _no_fields(payload):
            return _no_fields(payload)
        kw, bad = validate_fields(payload, AUTO_SPEC)
        if "dailyLossR" in kw and kw["dailyLossR"] == 0:
            bad.append("dailyLossR=0（當日停損上限不能是 0）")
        if bad:
            return _invalid(bad)
        if "on" in kw:
            trader.AUTO["on"] = kw.pop("on")
            if trader.AUTO["on"]:
                trader.AUTO["blocked"] = None      # 手動重啟時解除當日封鎖
        if "dailyLossR" in kw:
            kw["dailyLossR"] = -abs(kw["dailyLossR"])
        trader.AUTO.update(kw)
        trader.save_state()
        return 200, {"ok": True, "auto": trader.status()["auto"]}

    if path == "/api/trade/enable":
        trader.STATE["enabled"] = bool(payload.get("on"))
        trader.save_state()
        return 200, {"enabled": trader.STATE["enabled"]}

    if path == "/api/trade/open":
        base = str(payload.get("symbol", "")).strip()
        side = "SHORT" if str(payload.get("side", "LONG")).upper() == "SHORT" else "LONG"
        stop = payload.get("stop")
        entry = payload.get("entry")
        if not base or stop is None:
            return 400, {"error": "need_symbol_and_stop"}
        sp = payload.get("stopPct")
        r = trader.open_position(base, side, entry, float(stop), note=str(payload.get("note", "")),
                                 stop_pct=(float(sp) if sp is not None else None))
        return (200 if r.get("ok") else 400), r

    if path == "/api/trade/close":
        sym = str(payload.get("symbol", "")).strip().upper()
        return 200, trader.close_position(sym, str(payload.get("reason", "手動平倉")))

    if path == "/api/trade/sync":
        return 200, trader.sync_positions()

    return 404, {"error": "not_found"}


def mon_handle(path, payload):
    if path == "/api/monitor/status":
        return 200, {"on": MON["on"], "watch": MON["watch"], "hasCfg": bool(MON["cfg"]),
                     "lastRun": MON["lastRun"], "lastCount": MON["lastCount"],
                     "lastError": MON["lastError"], "engine": bool(engine),
                     "historyCount": len(MON["history"]),
                     "lastRefreshed": MON.get("lastRefreshed", 0),
                     "gateStats": MON.get("gateStats"),
                     "callsToday": MON.get("callsToday", 0),
                     "histTTL": MON.get("histTTL", 12), "maxRefresh": MON.get("maxRefresh", 8),
                     "scope": MON["scope"], "topN": MON["topN"],
                     "adminRequired": bool(os.environ.get("ADMIN_KEY", "").strip())}
    if path == "/api/monitor/config":
        if not tg_admin_ok(payload):
            return 403, {"error": "admin_key_required"}
        # r40、r41：先整批驗證，全部合法才一次套用（以前一邊套用一邊轉型，中途出錯留下半套）
        if _no_fields(payload):
            return _no_fields(payload)
        spec = {k: v for k, v in MON_SPEC.items() if v[0] not in ("list", "dict")}
        kw, bad = validate_fields({k: v for k, v in payload.items() if k not in ("watch", "cfg")}, spec)
        if "watch" in payload:
            if isinstance(payload["watch"], list):
                kw["watch"] = [str(x) for x in payload["watch"]][:250]
            else:
                bad.append(f"watch（要是清單，收到 {type(payload['watch']).__name__}）")
        if "cfg" in payload:
            c = payload["cfg"]
            # r48：空的 cfg 會讓背景監控靜靜停掉（mon_run_once 看到空設定就不跑）、缺 bull／bear 會讓評分整輪丟例外——
            # 任何 bug 讓設定沒帶到都等於「清空全部」。要清掉監控用 on=0，不是送空的 cfg
            if not isinstance(c, dict):
                bad.append(f"cfg（要是物件，收到 {type(c).__name__}）")
            elif not all(isinstance(c.get(s), dict) and c.get(s) for s in ("bull", "bear")):
                bad.append("cfg（要有非空的 bull 與 bear 兩組條件；要停用監控請關閉開關）")
            else:
                kw["cfg"] = c
        if bad:
            return _invalid(bad)
        with _mon_lock:
            MON.update(kw)
            saved = mon_save()
        out = {"ok": True, "on": MON["on"], "watch": len(MON["watch"]),
               "scope": MON["scope"], "topN": MON["topN"]}
        if not saved:
            out["warning"] = "設定已套用，但存檔失敗，重啟後會不見（已記錄並推播）"
        return 200, out
    if path == "/api/monitor/history":
        return 200, {"history": MON["history"][:200]}
    if path == "/api/monitor/run":
        try:
            n = mon_run_once()
            return 200, {"ok": True, "fired": n, "checked": MON["lastCount"]}
        except Exception as e:
            return 500, {"error": str(e)}
    return 404, {"error": "not_found"}



_egress = {"ip": None, "ts": 0}


def egress_ip():
    """伺服器的對外 IP。用來設定幣安 API 的 IP 白名單：
    金鑰即使外洩，不在白名單的 IP 也用不了。"""
    if _egress["ip"] and time.time() - _egress["ts"] < 3600:
        return _egress["ip"]
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
            with urllib.request.urlopen(req, timeout=6) as r:
                ip = r.read().decode().strip()
                if ip and len(ip) < 64:
                    _egress["ip"] = ip
                    _egress["ts"] = time.time()
                    return ip
        except Exception:
            continue
    return None



def live_readiness():
    """正式網準備清單：每一項自動檢查目前狀態。
    放在系統裡而不是文件裡，因為文件會忘，頁面每次打開都看得到。"""
    items = []
    ak = os.environ.get("ADMIN_KEY", "").strip()
    items.append({"key": "admin", "label": "ADMIN_KEY 長度 16 字元以上",
                  "ok": len(ak) >= 16,
                  "hint": "目前未設或太短。在 Zeabur 變數設一組隨機字串。" if len(ak) < 16 else None})
    has_bn = bool(trader and trader.CFG["key"] and trader.CFG["secret"])
    items.append({"key": "bnkey", "label": "幣安金鑰已設定（正式網要換成主網子帳戶的新金鑰）",
                  "ok": has_bn, "hint": None if has_bn else "BN_KEY／BN_SECRET 未設"})
    rp = trader.CFG["riskPct"] if trader else None
    items.append({"key": "risk", "label": "單筆風險 ≤ 1%（首次上線建議 0.25）",
                  "ok": rp is not None and rp <= 1.0,
                  "hint": f"目前 {rp}%" if rp is not None and rp > 1.0 else None})
    lv = trader.CFG["leverage"] if trader else None
    items.append({"key": "lev", "label": "槓桿 ≤ 5x", "ok": lv is not None and lv <= 5,
                  "hint": f"目前 {lv}x" if lv is not None and lv > 5 else None})
    ip = _egress["ip"]
    items.append({"key": "ip", "label": "已知伺服器出口 IP（填入幣安 IP 白名單）",
                  "ok": bool(ip), "hint": f"出口 IP：{ip}" if ip else "按「執行連線診斷」取得"})
    tg = bool(_tg.get("chats"))
    items.append({"key": "tg", "label": "Telegram 已配對（平倉通知不能漏）",
                  "ok": tg, "hint": None if tg else "到提醒設定頁完成配對"})
    n = len([t for t in (trader.STATE["trades"] if trader else []) if not t.get("excluded")])
    items.append({"key": "sample", "label": "模擬網累積 30 筆以上策略交易",
                  "ok": n >= 30, "hint": f"目前 {n} 筆"})
    p = trader.performance() if trader else {}
    ev = p.get("expectancyR")
    items.append({"key": "edge", "label": "模擬網期望值為正、賺賠比 ≥ 1.5",
                  "ok": (ev or 0) > 0 and (p.get("payoff") or 0) >= 1.5,
                  "hint": f"目前期望值 {ev}R、賺賠比 {p.get('payoff')}" if p.get("count") else "尚無資料"})
    done = sum(1 for i in items if i["ok"])
    return {"items": items, "done": done, "total": len(items),
            "live": bool(trader and trader.CFG["live"]),
            "steps": [
                "幣安建子帳戶，只轉入願意讓機器人管的資金",
                "建新 API 金鑰：只勾合約交易、不勾提幣、設 IP 白名單",
                "Zeabur 變數：更新 BN_KEY、BN_SECRET，新設 ALLOW_LIVE=1",
                "Dockerfile 的 CMD 改為 python main.py --live",
                "下載並清空 trader.json（模擬網紀錄不要混進真錢績效）",
                "重新部署，看啟動日誌出現「正式網（真實資金）」",
                "第一天：每日上限 1 筆、分數門檻 75，確認三張條件單都在幣安 App 看得到",
            ]}


def live_checklist():
    """距離接入正式網還差什麼。與啟動時的硬性檢查同一套條件，
    差別是這裡只回報、不擋，讓你在模擬網階段就能看到準備進度。"""
    ak = os.environ.get("ADMIN_KEY", "").strip()
    items = [
        ("ADMIN_KEY 至少 16 字元", len(ak) >= 16),
        ("BN_KEY／BN_SECRET 已設定", bool(trader and trader.CFG["key"] and trader.CFG["secret"])),
        ("RISK_PCT ≤ 1", bool(trader) and trader.CFG["riskPct"] <= 1.0),
        ("LEVERAGE ≤ 5", bool(trader) and trader.CFG["leverage"] <= 5),
        ("ALLOW_LIVE=1", os.environ.get("ALLOW_LIVE", "") == "1"),
        ("EXTRA_ARGS 含 --live", "--live" in sys.argv),
        ("Telegram 已配對", bool(_tg.get("chats"))),
    ]
    # 需要金鑰才查得到的兩項：查不到就標「未知」而不是失敗
    if trader and trader.CFG["key"] and trader.CFG["secret"]:
        try:
            eq, _ = trader.account_equity()
            items.append((f"合約錢包有餘額（目前 {eq:.0f} U）" if eq is not None else "合約錢包餘額（查不到）",
                          bool(eq and eq > 0)))
        except Exception:
            items.append(("合約錢包餘額（查詢失敗）", False))
        try:
            mx = trader.max_leverage("BTCUSDT")
            if mx:
                items.append((f"帳戶可用槓桿上限 {mx}x（設定值 {trader.CFG['leverage']}x）",
                              mx >= trader.CFG["leverage"]))
        except Exception:
            pass
        try:
            pm = trader.position_mode()
            items.append((f"持倉模式已偵測：{'雙向' if pm == 'hedge' else '單向'}（兩種都支援，自動適配）", True)
                         if pm else ("持倉模式（查不到）", False))
        except Exception:
            items.append(("持倉模式（查詢失敗）", False))
    return [{"item": a, "ok": b} for a, b in items]


def upstream_probe():
    """伺服器自己試連各上游，回報狀態碼與耗時，讓前端能分辨是誰連不上"""
    out = []
    targets = [
        ("CoinGecko", (PRO_BASE if CFG["pro"] else PUBLIC_BASE) + "/ping"
         + (("?" + ("x_cg_pro_api_key=" if CFG["pro"] else "x_cg_demo_api_key=")
             + urllib.parse.quote(CFG["key"])) if CFG["key"] else "")),
        ("GeckoTerminal", UPSTREAMS["/api/gt"] + "/networks"),
        ("Binance 合約", UPSTREAMS["/api/bn"] + "/fapi/v1/ping"),
    ]
    for name, url in targets:
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "local-crypto-screener/1.0"})
            with urllib.request.urlopen(req, timeout=12) as r:
                out.append({"name": name, "ok": True, "code": r.status,
                            "ms": int((time.time() - t0) * 1000)})
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()[:180]
            except Exception:
                pass
            out.append({"name": name, "ok": False, "code": e.code,
                        "ms": int((time.time() - t0) * 1000), "detail": body})
        except Exception as e:
            out.append({"name": name, "ok": False, "code": 0,
                        "ms": int((time.time() - t0) * 1000), "detail": str(e)[:180]})
    return out



# ── 快取清理 ────────────────────────────────────────────────
#
# 深度資料的快取檔過期後不會自動消失，只是不再被採用，
# 所以磁碟用量會持續累積。這裡定期清掉真的用不到的。
#
# 狀態檔（Telegram 配對、監控設定、交易紀錄）絕對不能刪，
# 它們跟快取放在同一個目錄，靠固定檔名保護。

PROTECTED = {"telegram.json", "monitor.json", "trader.json", "trader.live.json",
             "trader.testnet.json", "last_startup.json"}

LAST_CLEAN = {"ts": None, "removed": 0, "freedMB": 0.0, "by": None}


def cache_disk_mb():
    if not os.path.isdir(CACHE_DIR):
        return 0.0
    tot = 0
    try:
        for n in os.listdir(CACHE_DIR):
            try:
                tot += os.path.getsize(os.path.join(CACHE_DIR, n))
            except OSError:
                pass
    except OSError:
        return 0.0
    return round(tot / 1048576.0, 1)

CACHE_MAX_AGE_H = float(os.environ.get("CACHE_MAX_AGE_H", 72))   # 超過幾小時就刪
CACHE_MAX_MB = float(os.environ.get("CACHE_MAX_MB", 0))          # 0 = 不限總量


def cache_cleanup(max_age_h=None, max_mb=None, dry_run=False):
    """刪除過期或超量的快取檔。回傳統計。

    保留期刻意比 TTL 長很多：TTL 到期只代表「要重抓」，
    但背景監控的 histTTL 最長可設 72 小時，太早刪會讓它白白重抓。
    """
    max_age = (CACHE_MAX_AGE_H if max_age_h is None else max_age_h) * 3600
    limit_mb = CACHE_MAX_MB if max_mb is None else max_mb
    stat = {"scanned": 0, "removed": 0, "freedMB": 0.0, "keptMB": 0.0,
            "protected": 0, "errors": 0, "beforeMB": cache_disk_mb(),
            "oldestH": None, "newestH": None}
    if not os.path.isdir(CACHE_DIR):
        return stat

    now = time.time()
    entries = []
    for name in os.listdir(CACHE_DIR):
        if name in PROTECTED:
            stat["protected"] += 1
            continue
        if not name.endswith(".json"):
            continue                      # 不認識的檔案一律不碰
        p = os.path.join(CACHE_DIR, name)
        try:
            st = os.stat(p)
        except OSError:
            continue
        stat["scanned"] += 1

        # 以檔案內記錄的時間為準，檔案 mtime 可能因為搬移而失真
        ts = st.st_mtime
        try:
            with open(p) as f:
                ts_ = json.load(f).get("ts")
                ts = float(ts_) if isinstance(ts_, (int, float)) else st.st_mtime
        except Exception:
            pass
        entries.append((ts, p, st.st_size))

    keep = []
    for ts, p, size in entries:
        if now - ts > max_age:
            if dry_run:
                stat["removed"] += 1
                stat["freedMB"] += size / 1048576.0
            else:
                try:
                    os.remove(p)
                    stat["removed"] += 1
                    stat["freedMB"] += size / 1048576.0
                except OSError:
                    stat["errors"] += 1
        else:
            keep.append((ts, p, size))

    # 總量上限：從最舊的開始刪，直到低於上限
    if limit_mb > 0:
        keep.sort()                         # 舊的在前
        total = sum(x[2] for x in keep) / 1048576.0
        while keep and total > limit_mb:
            ts, p, size = keep.pop(0)
            if not dry_run:
                try:
                    os.remove(p)
                except OSError:
                    stat["errors"] += 1
                    continue
            stat["removed"] += 1
            stat["freedMB"] += size / 1048576.0
            total -= size / 1048576.0

    stat["keptMB"] = round(sum(x[2] for x in keep) / 1048576.0, 2)
    stat["freedMB"] = round(stat["freedMB"], 2)
    if keep:
        ages = [(now - x[0]) / 3600.0 for x in keep]
        stat["oldestH"] = round(max(ages), 1)
        stat["newestH"] = round(min(ages), 1)

    if not dry_run:
        LAST_CLEAN["ts"] = now
        LAST_CLEAN["removed"] = stat["removed"]
        LAST_CLEAN["freedMB"] = stat["freedMB"]

    # 記憶體快取也要同步清，否則刪了檔案卻還在記憶體裡佔空間
    if not dry_run and stat["removed"]:
        with _cache_lock:
            for k in [k for k, v in _cache.items() if now - v[0] > max_age]:
                _cache.pop(k, None)
    return stat


def cleanup_worker(every_h=6):
    """定期清理。啟動後先等一分鐘，避開開機時的尖峰。"""
    time.sleep(60)
    while True:
        try:
            st = cache_cleanup()
            LAST_CLEAN["by"] = "auto"
            sys.stderr.write(
                f"  ~ 快取清理（自動）：刪除 {st['removed']} 檔，釋出 {st['freedMB']} MB，"
                f"保留 {st['keptMB']} MB\n")
        except Exception as e:
            sys.stderr.write(f"  ! 快取清理失敗：{e}\n")
        time.sleep(every_h * 3600)


def notify_telegram(title, text):
    if not (_tg["token"] and _tg["chats"]):
        return None                       # 沒設定：push_all 會記一次「沒有任何推播管道」
    sent = 0
    for c in list(_tg["chats"]):
        try:
            tg_api("sendMessage", {"chat_id": c["id"], "text": f"{title}\n{text}"}, timeout=20)
            sent += 1
        except Exception as e:
            # r41 對照時發現：r39 只在 push_all 外層接例外，這裡每個聊天室各自 except: pass——
            # Telegram 權杖錯、網路斷時錯誤根本傳不出去（r39 的測試把整個 notify_telegram 換掉，測不到這層）
            _notify_err(f"Telegram 聊天室 {c.get('id')}：{type(e).__name__}: {str(e)[:120]}")
    return f"Telegram×{sent}" if sent else None


def notify_discord(title, text):
    if not NOTIFY["discord"]:
        return None
    body = json.dumps({"content": f"**{title}**\n{text}"}).encode()
    req = urllib.request.Request(NOTIFY["discord"], data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20):
        return "Discord"


def notify_email(title, text):
    if not (NOTIFY["smtp_host"] and NOTIFY["mail_to"]):
        return None
    msg = email.message.EmailMessage()
    msg["Subject"] = title
    msg["From"] = NOTIFY["smtp_user"] or NOTIFY["mail_to"]
    msg["To"] = NOTIFY["mail_to"]
    msg.set_content(text)
    with smtplib.SMTP(NOTIFY["smtp_host"], NOTIFY["smtp_port"], timeout=25) as sv:
        sv.starttls()
        if NOTIFY["smtp_user"]:
            sv.login(NOTIFY["smtp_user"], NOTIFY["smtp_pass"])
        sv.send_message(msg)
    return "電子郵件"


def keyless_now() -> bool:
    """Demo 金鑰的每月額度用盡時，自動改走無金鑰公開端點。
    公開端點沒有月額度，只有較嚴的每分鐘限制，所以是降速而非停擺。
    每 6 小時會試著切回金鑰，跨月重置後便自動恢復。"""
    if not QUOTA["exhausted"]:
        return False
    if time.time() - QUOTA["ts"] > 6 * 3600:
        QUOTA["exhausted"] = False          # 到期重試一次，成功就切回金鑰
        return False
    return True


def effective_gap() -> float:
    """無金鑰公開端點官方標示 5–15 次/分，取保守值 6 秒一次；
    有金鑰時沿用設定值。"""
    return max(CFG["gap"], 6.0) if keyless_now() else CFG["gap"]


def upstream_url(path_qs: str) -> str:
    if CG_UPSTREAM:
        return CG_UPSTREAM + "/api/v3" + path_qs        # 對方會自己附金鑰
    keyless = keyless_now()
    base = PRO_BASE if (CFG["pro"] and not keyless) else PUBLIC_BASE
    url = base + path_qs
    if CFG["key"] and not keyless:
        sep = "&" if "?" in url else "?"
        param = "x_cg_pro_api_key" if CFG["pro"] else "x_cg_demo_api_key"
        url += f"{sep}{param}={urllib.parse.quote(CFG['key'])}"
    return url


def ttl_for(path_qs: str) -> float:
    if "/futures/data/" in path_qs:
        return 240.0            # 未平倉量／多空比每 5 分鐘一根，快取 4 分鐘
    if "/premiumIndex" in path_qs:
        return 60.0             # 資金費率變動慢
    if "/fundingRate" in path_qs or "/exchangeInfo" in path_qs:
        return 1800.0
    if "/token_security" in path_qs:
        return 900.0            # 合約檢查結果變動慢
    if "/trades" in path_qs or "_pools" in path_qs:
        return 60.0
    return OHLC_TTL if "/ohlc" in path_qs or "market_chart" in path_qs else CACHE_TTL


def fetch_upstream(path_qs: str, prefix: str = "/api/v3", background: bool = False):
    """回傳 (status, body_bytes)。含節流與 429 退避。
    background=True：背景工作——CoinGecko 冷卻期間不打上游、直接回 429（不重試、不佔節流閘）。"""
    if background and prefix == "/api/v3" and cg_cooling():
        return 429, json.dumps({"error": "cg_cooldown", "detail": "CoinGecko 剛回 429，背景請求讓路中"},
                               ensure_ascii=False).encode()
    url = upstream_url(path_qs) if prefix == "/api/v3" else UPSTREAMS[prefix] + path_qs
    req = urllib.request.Request(url, headers={
        "User-Agent": "local-crypto-screener/1.0",
        "Accept": "application/json",
    })
    if prefix == "/api/v3":
        gap, lock, slot = effective_gap(), _gate_lock, _last_call
    elif prefix == "/api/bn":
        gap, lock, slot = 0.25, _bn_lock, _last_bn     # Binance 權重制，可較密集
    else:
        gap, lock, slot = 2.2, _chain_lock, _last_chain
    for attempt in range(3):
        with lock:
            wait = gap - (time.time() - slot[0])
            if wait > 0:
                time.sleep(wait)
            slot[0] = time.time()
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return 200, r.read()
        except urllib.error.HTTPError as e:
            body = e.read()
            # error_code 10006 是「每月總額度用盡」，重試沒有意義，直接放棄
            if b"10006" in body or b"calls limit" in body:
                QUOTA["exhausted"] = True
                QUOTA["ts"] = time.time()
                return e.code, body
            if e.code == 429 and prefix == "/api/v3":
                CG_COOL["until"] = time.time() + CG_COOL_SEC        # 背景工作讓路一分鐘
                CG_COOL["hits"] += 1
                if background:
                    return e.code, body                             # 背景不重試
            if e.code == 429 and attempt < 2:
                time.sleep(6 * (attempt + 1))
                continue
            if e.code >= 500 and attempt < 2:
                time.sleep(2.5)
                continue
            return e.code, body or json.dumps({"error": f"HTTP {e.code}"}).encode()
        except Exception as e:                                  # 連線層失敗
            if attempt < 2:
                time.sleep(2)
                continue
            return 502, json.dumps(
                {"error": "upstream_unreachable", "detail": str(e)}, ensure_ascii=False
            ).encode()
    return 502, b'{"error":"unknown"}'


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=STATIC_DIR, **kw)

    # ── 路由 ──────────────────────────────────────────────
    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/api/deep/ts":
            # 有上游時，深度資料的時間戳以上游為準——資料本來就在那邊
            if CG_UPSTREAM:
                try:
                    req = urllib.request.Request(CG_UPSTREAM + "/api/deep/ts",
                                                 headers={"User-Agent": "crypto-screener/relay"})
                    with urllib.request.urlopen(req, timeout=10) as r:
                        body = r.read()
                except Exception:
                    body = b"{}"
            else:
                body = json.dumps(deep_ts_map()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass
            return

        if p == "/api/health":
            info = {
                "server": "crypto-screener", "proxy": True,
                "hasKey": bool(CFG["key"]), "keyLen": len(CFG["key"]),
                "pro": CFG["pro"], "engine": bool(engine),
                "monitor": MON["on"], "cached": len(_cache),
                "quotaExhausted": QUOTA["exhausted"], "keyless": keyless_now(),
                "dataSource": CG_UPSTREAM or "direct",
                "live": bool(trader and trader.CFG["live"]),
                "liveBlocked": list(LIVE_BLOCKED),
                "liveChecklist": live_checklist(),
                "disk": (len(os.listdir(CACHE_DIR)) if os.path.isdir(CACHE_DIR) else 0),
                "diskMB": cache_disk_mb(),
                "refresh": {**{k: REFRESH[k] for k in
                               ("last", "lastTs", "callsToday", "budget", "interval",
                                "fresh", "stale", "never", "n")},
                            "source": CG_UPSTREAM or "direct"},
                "gap": CFG["gap"], "uptime": int(time.time() - START_TS),
            }
            if "probe=1" in (self.path.split("?", 1)[1] if "?" in self.path else ""):
                info["probe"] = upstream_probe()
                info["egressIp"] = egress_ip()
            body = json.dumps(info, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Screener-Server", "1")
            self.send_header("X-Cache", "LIVE")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass
            return
        if p.startswith("/api/tg/"):
            code, body = tg_handle(p, {})
            return self.send_json(code, json.dumps(body, ensure_ascii=False).encode())
        if p.startswith("/api/trade/"):
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            payload = {k: v[0] for k, v in qs.items()}
            code, body = trade_handle_safe(p, payload)
            return self.send_json(code, json.dumps(body, ensure_ascii=False).encode())

        if p.startswith("/api/monitor/"):
            code, body = mon_handle(p, {})
            return self.send_json(code, json.dumps(body, ensure_ascii=False).encode())
        for prefix in UPSTREAMS:
            if self.path.startswith(prefix + "/"):
                return self.handle_proxy(prefix)
        if self.path in ("/", ""):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self.send_json(400, b'{"error":"bad_json"}')
        if not isinstance(payload, dict):
            # 第 8 條 r45：解析得了但不是物件（[]、"x"、null）→ 以前各處理函式 .get() 丟例外、回 500，
            # 或被當成空的。格式不對就回錯誤、什麼都不改
            return self.send_json(400, json.dumps({"error": f"請求內容要是 JSON 物件，收到 {type(payload).__name__}"},
                                                  ensure_ascii=False).encode())

        if self.path.startswith("/api/tg/"):
            code, body = tg_handle(self.path, payload)
            return self.send_json(code, json.dumps(body, ensure_ascii=False).encode())

        if self.path.startswith("/api/trade/"):
            code, body = trade_handle_safe(self.path.split("?")[0], payload)
            return self.send_json(code, json.dumps(body, ensure_ascii=False).encode())

        if self.path.startswith("/api/monitor/"):
            code, body = mon_handle(self.path, payload)
            return self.send_json(code, json.dumps(body, ensure_ascii=False).encode())

        if self.path != "/api/notify":
            return self.send_json(404, b'{"error":"not_found"}')
        title = str(payload.get("title", "訊號提醒"))
        text = str(payload.get("text", ""))

        # 網頁送來的訊號也要跑一次下單判斷，並把結果附在訊息後面。
        # 否則同一批訊號會因為來源不同，有的有「是否下單」有的沒有。
        ev = payload.get("event")
        if isinstance(ev, dict):
            MON["lastPushTs"] = int(time.time() * 1000)
        if isinstance(ev, dict) and trader:
            try:
                outcome = auto_try_trade(ev, payload.get("row") or {})
            except Exception as e:
                outcome = {"stage": "failed", "why": f"執行時發生錯誤：{str(e)[:80]}"}
            text += "\n" + trade_outcome_text(outcome)
            if outcome.get("stage") == "opened":
                t2, x2 = notify_trade_open(outcome["result"])
                push_all(t2, x2)

        sent, failed = [], []
        for fn in (notify_telegram, notify_discord, notify_email):
            try:
                r = fn(title, text)
                if r:
                    sent.append(r)
            except Exception as e:
                failed.append(f"{fn.__name__}: {e}")
        sys.stderr.write(f"  ! 通知 [{title}] 送出 {sent or '無管道'}{' 失敗 ' + str(failed) if failed else ''}\n")
        self.send_json(200, json.dumps({"sent": sent, "failed": failed}, ensure_ascii=False).encode())

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def handle_proxy(self, prefix="/api/v3"):
        path_qs = self.path[len(prefix):]
        key = prefix + path_qs
        ttl = ttl_for(path_qs)

        cached = cache_get(key, ttl)
        if cached is not None:
            _, age = cache_peek(key)
            return self.send_json(200, cached, cached=True,
                                  data_ts=(time.time() - age) if age is not None else None)

        # 快取過期但還在寬限期內：先把舊資料送出去，背景再更新。
        # 節流閘與重試會讓同步等待長達十幾秒，這段等待對使用者沒有價值——
        # 行情差幾十秒不影響量能倍數的判讀，畫面卡住才是問題。
        # 行情榜（/coins/markets）不設寬限上限：只要快取裡有舊的就先送、背景更新——畫面永遠不等上游（回應帶 X-Stale-Age，網頁會標「快取」）。
        stale, age = cache_peek(key)
        markets = prefix == "/api/v3" and "/coins/markets" in path_qs
        if stale is not None and age is not None and (markets or age < ttl + STALE_GRACE):
            revalidate_async(path_qs, prefix, key)
            return self.send_json(200, stale, cached=True, stale=int(age), data_ts=time.time() - age)

        # 深度資料（瀏覽器的背景深掃）是背景工作：冷卻期間直接讓路
        background = prefix == "/api/v3" and ("market_chart" in path_qs or "/ohlc" in path_qs)
        status, body = fetch_upstream(path_qs, prefix, background=background)
        if status == 200:
            cache_put(key, body)
            # 網頁抓行情榜時順便更新宇宙清單，這樣即使沒開補抓，
            # /api/deep/ts 也能回報伺服器已有哪些檔的資料
            if prefix == "/api/v3" and "/coins/markets" in path_qs and "page=1" in path_qs:
                try:
                    ids = [c["id"] for c in json.loads(body)]
                    if ids and time.time() - REFRESH["universeTs"] > 300:
                        REFRESH["universe"] = ids
                        REFRESH["universeTs"] = time.time()
                except Exception:
                    pass
        self.send_json(status, body, data_ts=time.time() if status == 200 else None)

    def send_json(self, code, body, cached=False, stale=None, data_ts=None):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        if stale is not None:
            self.send_header("X-Stale-Age", str(stale))
        if data_ts is not None:
            self.send_header("X-Data-Ts", str(int(data_ts * 1000)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Cache", "HIT" if cached else "MISS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def log_message(self, fmt, *args):
        msg = fmt % args
        if "/api/v3/" in msg:
            sys.stderr.write(f"  · {msg}\n")


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


REFRESH = {"universe": [], "universeTs": 0, "last": None, "lastTs": None,
           "callsToday": 0, "day": None, "interval": None,
           "fresh": 0, "stale": 0, "never": 0, "n": 0, "budget": 0}


def _deep_key(cid):
    return "/api/v3" + f"/coins/{cid}/market_chart?vs_currency=usd&days=90"


def refresh_status(count: int, ttl_h: float):
    """統計目前宇宙裡各檔深度資料的狀態，供前端顯示與排程決策。"""
    now = time.time()
    fresh = stale = never = 0
    oldest = None
    for cid in REFRESH["universe"][:count]:
        body, age = cache_peek(_deep_key(cid))
        if body is None:
            never += 1
        elif age > ttl_h * 3600:
            stale += 1
            if oldest is None or age > oldest[0]:
                oldest = (age, cid)
        else:
            fresh += 1
            # 就算都新鮮，也記下最接近到期的，讓節奏保持平滑
            if oldest is None or age > oldest[0]:
                oldest = (age, cid)
    REFRESH.update({"fresh": fresh, "stale": stale, "never": never,
                    "n": len(REFRESH["universe"][:count])})
    return fresh, stale, never, oldest


def pick_next(count: int, ttl_h: float):
    """挑下一檔該補的：從未抓過的優先，再來是最舊的。"""
    fresh, stale, never, oldest = refresh_status(count, ttl_h)
    for cid in REFRESH["universe"][:count]:
        body, _ = cache_peek(_deep_key(cid))
        if body is None:
            return cid, "never"
    if oldest:
        age, cid = oldest
        if age > ttl_h * 3600:
            return cid, "stale"
        # 全部新鮮：提前補最接近到期的，讓過期時間錯開而不是一起到期
        if age > ttl_h * 3600 * 0.8:
            return cid, "ahead"
    return None, None



_deep_ts_memo = {"ts": 0, "data": {}}


def deep_ts_map():
    """回傳 {coin_id: 資料時間戳(ms)}，用檔案 mtime，不用解析內容。
    網頁用它判斷「伺服器是否有比我更新的資料」，取代自己的 12 小時計時器。"""
    now = time.time()
    if now - _deep_ts_memo["ts"] < 30:
        return _deep_ts_memo["data"]
    out = {}
    for cid in REFRESH["universe"][:400]:
        p = _disk_path(_deep_key(cid))
        try:
            out[cid] = int(os.stat(p).st_mtime * 1000)
        except OSError:
            pass
    _deep_ts_memo["ts"] = now
    _deep_ts_memo["data"] = out
    return out


def prefetch_worker(count: int, ttl_h: float):
    """滾動式深度資料補抓。

    以前是每小時整批跑：所有幣同一輪抓，就會同一時間過期，
    到期時又一次爆掃。現在改成每次只補一檔，節奏由每日預算決定，
    從未抓過的優先、其次最舊的、全部新鮮時提前補最接近到期的。
    幾輪之後各檔的到期時間自然錯開，負載變成穩定的細流。
    """
    time.sleep(5)
    budget = int(os.environ.get("REFRESH_BUDGET", 0)) or max(24, int(count * 24 / ttl_h * 1.3))
    interval = 86400.0 / budget
    REFRESH["budget"] = budget
    REFRESH["interval"] = round(interval, 1)
    sys.stderr.write(f"  ~ 滾動補抓：{count} 檔、保鮮 {ttl_h:g} 小時、"
                     f"每日預算 {budget} 次 → 每 {interval:.0f} 秒補一檔\n")

    while True:
        try:
            # 跨日重置計數
            d = time.strftime("%Y-%m-%d", time.gmtime())
            if REFRESH["day"] != d:
                REFRESH["day"] = d
                REFRESH["callsToday"] = 0

            # 宇宙每 15 分鐘更新一次，新幣進榜會自動被納入
            if time.time() - REFRESH["universeTs"] > 900:
                st, body = fetch_upstream(
                    "/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=1")
                if st == 200:
                    REFRESH["universe"] = [c["id"] for c in json.loads(body)]
                    REFRESH["universeTs"] = time.time()

            if QUOTA["exhausted"] or not REFRESH["universe"]:
                time.sleep(300)
                continue
            if REFRESH["callsToday"] >= budget:
                time.sleep(600)          # 今日預算用完，等跨日
                continue

            if cg_cooling():
                time.sleep(max(1.0, CG_COOL["until"] - time.time()))   # 剛被 429：讓路，把額度留給前景請求
                continue
            cid, why = pick_next(count, ttl_h)
            if cid is None:
                time.sleep(interval)
                continue

            st, b = fetch_upstream(f"/coins/{cid}/market_chart?vs_currency=usd&days=90", background=True)
            REFRESH["callsToday"] += 1
            if st == 200:
                cache_put(_deep_key(cid), b)
                REFRESH["last"] = f"{cid}（{ {'never': '新增', 'stale': '過期', 'ahead': '預補'}[why] }）"
                REFRESH["lastTs"] = int(time.time() * 1000)
            elif st == 429:
                time.sleep(30)
            time.sleep(interval)
        except Exception as e:
            sys.stderr.write(f"  ~ 滾動補抓中斷：{e}\n")
            time.sleep(60)


def lan_ip():
    """找出這台電腦在區網的位址，讓手機知道要連哪裡"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))          # 不會真的送出封包，只用來查本機出口網卡
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def warn_live_ledger():
    """切到模擬網時檢查正式網帳本：還有持倉要講清楚；讀不到也要講（第 8 條 r44）。"""
    try:
        lp = os.path.join(CACHE_DIR, "trader.live.json")
        if os.path.exists(lp):
            with open(lp) as f:
                live_pos = (json.load(f).get("state") or {}).get("positions") or {}
            if live_pos:
                m = (f"正式網帳本裡還有 {len(live_pos)} 筆持倉（{'、'.join(live_pos)}）。"
                     f"它們仍在幣安、停損單仍有效，但模擬網模式不會追蹤或移損；切回正式網會自動接續。")
                sys.stderr.write(f"  ⚠ {m}\n")
                push_all("⚠ 正式網仍有持倉", m)
    except Exception as e:
        # 第 8 條 r44：讀不到不等於「沒有持倉」——以前 except: pass，正式網帳本壞掉時切到模擬網沒有任何提醒
        m = (f"切到模擬網時讀不到正式網帳本（{type(e).__name__}: {str(e)[:100]}），無法確認正式網還有沒有持倉；"
             f"請到幣安正式網確認，那些部位的停損單仍在交易所上，但這裡不會追蹤")
        sys.stderr.write(f"  ⚠ {m}\n")
        push_all("⚠ 讀不到正式網帳本", m)


def selftest():
    print("  檢查對外連線 …", end=" ", flush=True)
    status, body = fetch_upstream("/ping")
    if status == 200:
        print("通過")
        return True
    print("失敗")
    try:
        detail = json.loads(body.decode()).get("detail") or body.decode()[:160]
    except Exception:
        detail = body.decode(errors="replace")[:160]
    print(f"  ✕ 這台機器連不到 CoinGecko（{status}）：{detail}")
    print("    表示問題出在網路本身，不是瀏覽器。檢查 DNS、VPN、防火牆或公司 Proxy。")
    return False


def main():
    ap = argparse.ArgumentParser(description="加密貨幣篩選終端本機伺服器")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8787)))
    ap.add_argument("--lan", action="store_true",
                    help="開放同一個 Wi-Fi 的手機連入（監聽 0.0.0.0，預設只允許本機）")
    ap.add_argument("--key", default=os.environ.get("CG_API_KEY", ""), help="CoinGecko API Key")
    ap.add_argument("--pro", action="store_true", help="使用 Pro 端點")
    ap.add_argument("--bn-key", default=os.environ.get("BN_KEY", ""),
                    help="幣安合約 API Key（模擬網請用 testnet.binancefuture.com 申請的）")
    ap.add_argument("--bn-secret", default=os.environ.get("BN_SECRET", ""), help="幣安 API Secret")
    ap.add_argument("--risk-pct", type=float, default=float(os.environ.get("RISK_PCT", 0.5)),
                    help="單筆風險佔帳戶權益的百分比，預設 0.5")
    ap.add_argument("--leverage", type=int, default=int(os.environ.get("LEVERAGE", 3)))
    ap.add_argument("--max-positions", type=int, default=int(os.environ.get("MAX_POSITIONS", 5)),
                    help="同時最多持有幾個部位，預設 5（等同環境變數 MAX_POSITIONS）")
    ap.add_argument("--live", action="store_true",
                    help="打正式網。必須同時設環境變數 ALLOW_LIVE=1，否則忽略")
    ap.add_argument("--tg-token", default=os.environ.get("TG_TOKEN", ""), help="Telegram Bot Token")
    ap.add_argument("--tg-chat", default=os.environ.get("TG_CHAT", ""), help="Telegram Chat ID")
    ap.add_argument("--discord", default=os.environ.get("DISCORD_WEBHOOK", ""), help="Discord Webhook 網址")
    ap.add_argument("--smtp-host", default=os.environ.get("SMTP_HOST", ""), help="SMTP 伺服器，例如 smtp.gmail.com")
    ap.add_argument("--smtp-port", type=int, default=int(os.environ.get("SMTP_PORT", 587)))
    ap.add_argument("--smtp-user", default=os.environ.get("SMTP_USER", ""))
    ap.add_argument("--smtp-pass", default=os.environ.get("SMTP_PASS", ""), help="建議用應用程式密碼")
    ap.add_argument("--mail-to", default=os.environ.get("MAIL_TO", ""), help="收件信箱")
    ap.add_argument("--monitor-every", type=float, default=float(os.environ.get("MONITOR_EVERY", 30)),
                    metavar="M", help="背景訊號監控的間隔分鐘數，預設 30（免費 Demo Key 每月僅 1 萬次，"
                       "設太密會很快用完；等同環境變數 MONITOR_EVERY）")
    ap.add_argument("--prefetch", type=int, default=int(os.environ.get("PREFETCH", 0)), metavar="N",
                    help="背景預抓市值前 N 檔的 90 日資料。Demo Key 每月僅 1 萬次，"
                         "建議 0 或 50 以內（等同環境變數 PREFETCH）")
    ap.add_argument("--prefetch-ttl", type=float, default=float(os.environ.get("PREFETCH_TTL", 72)),
                    metavar="H", help="深度資料的保鮮時數，預設 72。這些是 90 天歷史，變化慢；"
                                      "設太短會把額度燒光（250 檔×12h ≈ 每月 19,500 次）。"
                                      "等同環境變數 PREFETCH_TTL")
    ap.add_argument("--cache-dir", default=os.environ.get("CACHE_DIR", ""),
                    help="快取目錄，雲端掛載 Volume 時指定（等同環境變數 CACHE_DIR）")
    args = ap.parse_args()

    NOTIFY.update({
        "tg_token": args.tg_token.strip(), "tg_chat": args.tg_chat.strip(),
        "discord": args.discord.strip(), "smtp_host": args.smtp_host.strip(),
        "smtp_port": args.smtp_port, "smtp_user": args.smtp_user.strip(),
        "smtp_pass": args.smtp_pass, "mail_to": args.mail_to.strip(),
    })

    global CACHE_DIR
    if args.cache_dir:
        CACHE_DIR = args.cache_dir

    CFG["key"] = args.key.strip()
    CFG["pro"] = args.pro
    CFG["gap"] = 0.9 if CFG["key"] else 2.4

    print("\n  加密貨幣篩選終端 — 本機伺服器")
    print(f"  金鑰　{'已設定（' + ('Pro' if CFG['pro'] else 'Demo') + '）' if CFG['key'] else '未設定，額度僅 5–15 次/分'}")
    print(f"  節流　每 {CFG['gap']} 秒最多一次上游請求，行情快取 {int(CACHE_TTL)} 秒、K 線 {int(OHLC_TTL/60)} 分鐘")
    print("  鏈上　/api/gt 轉發 GeckoTerminal、/api/gp 轉發 GoPlus，另用一組節流閘")

    tg_load()
    mon_load()
    chans = [n for n, v in (("Telegram", _tg["token"]),
                            ("Discord", NOTIFY["discord"]),
                            ("電子郵件", NOTIFY["smtp_host"] and NOTIFY["mail_to"])) if v]
    print(f"  通知　{'、'.join(chans) if chans else '未設定，僅能用瀏覽器通知'}"
          + (f"　已配對 {len(_tg['chats'])} 個聊天室" if _tg["chats"] else ""))
    if _tg["token"] and not _tg["chats"]:
        print("        Bot 已設定但尚未配對，請到網頁的提醒設定按「開始配對」")

    # ── 模擬單設定 ──
    # 只做不碰網路的設定；對時、抓合約清單、查權益都丟到背景，
    # 否則上游一慢就會拖住連接埠，讓平台誤判容器沒起來。
    if trader is not None:
        want_live = args.live and os.environ.get("ALLOW_LIVE", "") == "1"
        if args.live and not want_live:
            sys.stderr.write("  ! 已指定 --live 但未設 ALLOW_LIVE=1，仍使用模擬網\n")

        # 正式網的硬性前提：沒有管理金鑰就等於任何人拿到網址都能下真錢的單。
        # 這裡直接拒絕啟動，而不是警告——警告會被忽略，真錢不能靠警告。
        if want_live:
            ak = os.environ.get("ADMIN_KEY", "").strip()
            problems = []
            if len(ak) < 16:
                problems.append("ADMIN_KEY 未設定或短於 16 字元")
            if args.risk_pct > 1.0:
                problems.append(f"RISK_PCT={args.risk_pct} 超過 1%，正式網首次啟用請從 0.25 開始")
            if args.leverage > 5:
                problems.append(f"LEVERAGE={args.leverage} 超過 5x")
            if not (args.bn_key and args.bn_secret):
                problems.append("BN_KEY／BN_SECRET 未設定")
            if problems:
                # 不能讓整個服務崩潰：這台可能還是另一個服務的資料供應者。
                # 降級成模擬網、停用下單，服務照常跑，並把原因放到看得到的地方。
                LIVE_BLOCKED[:] = problems
                want_live = False
                sys.stderr.write("\n  ✕ 正式網啟動條件不足，已降級為模擬網並停用下單：\n")
                for p in problems:
                    sys.stderr.write(f"     · {p}\n")
                sys.stderr.write("     補齊後重新部署即可切回正式網；資料代理與雷達不受影響。\n\n")
                trader.CFG["key"] = ""
                trader.CFG["secret"] = ""
        trader.CFG["key"] = args.bn_key.strip()
        trader.CFG["secret"] = args.bn_secret.strip()
        trader.CFG["live"] = want_live
        trader.CFG["riskPct"] = args.risk_pct
        trader.CFG["leverage"] = args.leverage
        trader.CFG["maxPositions"] = max(1, min(20, args.max_positions))
        trader.load_state(trader.state_path(CACHE_DIR, want_live))
        net = "正式網（真實資金）" if want_live else "模擬網 Testnet"

        # 上次跑的是正式網、這次卻是模擬網：多半是部署設定被覆蓋了。
        # 真實部位還在幣安但這裡不再追蹤，必須大聲警告。
        # 切回模擬網時，正式網帳本裡若還有持倉要講清楚：
        # 那些部位仍在幣安、停損單仍有效，只是這裡不再追蹤，切回正式網會接續。
        if not want_live:
            warn_live_ledger()

        last_net = trader.STATE.get("lastNet")
        if last_net == "live" and not want_live:
            msg = ("上一次啟動是正式網，這次卻是模擬網。若不是你刻意切換，"
                   "請檢查 Zeabur 的 ALLOW_LIVE 與 EXTRA_ARGS 是否還在。"
                   "真實部位仍在幣安、停損單仍有效，但伺服器已不再追蹤它們。")
            sys.stderr.write(f"\n  ⚠⚠ {msg}\n\n")
            try:
                push_all("⚠ 網路別異常", msg)
            except Exception:
                pass
        trader.STATE["lastNet"] = "live" if want_live else "testnet"
        trader.save_state()
        if trader.CFG["key"]:
            sys.stderr.write(f"  交易　{net}　風險 {args.risk_pct}%/筆　槓桿 {args.leverage}x　"
                             f"同時持倉上限 {trader.CFG['maxPositions']}　（連線資訊背景載入中）\n")
            if not want_live:
                sys.stderr.write("  　　　要接入正式網時，流程在 GO_LIVE.md；連線診斷會顯示還差哪幾項\n")
            if want_live:
                sys.stderr.write("  ⚠ 正在對正式網下單，會動用真實資金\n")

            def _trader_warmup():
                try:
                    _trader_warmup_body()
                except Exception as e:
                    sys.stderr.write(f"  ! 交易暖機失敗：{type(e).__name__}: {e}\n")
                    push_all("⚠ 交易暖機失敗", f"{type(e).__name__}: {str(e)[:150]}\n交易所規格或時鐘可能沒載入，請看自檢。")

            def _trader_warmup_body():
                off = trader.sync_time()
                n = len(trader.load_filters())
                eq, err = trader.account_equity()
                sys.stderr.write(
                    f"  交易　合約 {n} 檔　時鐘差 {off if off is not None else '?'}ms　"
                    f"權益 {('%.2f USDT' % eq) if eq is not None else '取不到（' + str(err) + '）'}\n")

            threading.Thread(target=_trader_warmup, daemon=True).start()
        else:
            sys.stderr.write(f"  交易　未設定 BN_KEY／BN_SECRET，模擬單功能停用（{net}）\n")

    if CG_UPSTREAM:
        sys.stderr.write(f"  上游　CoinGecko 請求轉給 {CG_UPSTREAM}（共用快取與額度）\n")
    if NOTIFY_PREFIX:
        sys.stderr.write(f"  通知　前綴「{NOTIFY_PREFIX}」\n")
    threading.Thread(target=selftest, daemon=True).start()
    threading.Thread(target=cleanup_worker, daemon=True).start()
    def _startup_report():
        """啟動後推一則狀態摘要。正常與異常都推——
        只有壞消息會通知的話，沒消息時分不出是『一切正常』還是『服務掛了』。
        同樣內容 6 小時內不重複，避免連續部署洗版。"""
        time.sleep(12)          # 等背景的連線與帳戶資訊載入
        pf_lines = []
        if preflight and trader and trader.CFG["key"]:
            try:
                pf_lines = preflight.summary_lines(preflight.run())
            except Exception as e:
                pf_lines = [f"自檢　執行失敗：{e}"]
        live = bool(trader and trader.CFG["live"])
        lines = []
        head = "正式網運作中" if live else "模擬網運作中"
        if LIVE_BLOCKED:
            head = "⚠ 正式網未啟動，已降級為模擬網"
            lines += ["設定了 --live 但條件不足，下單已停用："]
            lines += ["· " + p for p in LIVE_BLOCKED]
            lines.append("")

        if trader and trader.CFG["key"]:
            b, err = trader.account_balance(max_age=0)
            if b:
                lines.append(f"帳戶　權益 {b['equity']:,.2f} U　可用 {b['avail']:,.2f} U")
                cs, _ = trader.capital_state()
                if cs:
                    lines.append(f"本金　階梯 {cs['tier']:,.0f} U × {cs['usablePct']}% ＝ 可動用 {cs['usable']:,.0f} U")
            else:
                lines.append(f"帳戶　查不到餘額（{err}）")
            n = len(trader.STATE["positions"])
            a = trader.AUTO
            lines.append(f"持倉　{n} 筆　自動下單 {'啟用' if a['on'] else '關閉'}"
                         f"（風險 {trader.CFG['riskPct']}%/筆　槓桿 {trader.CFG['leverage']}x　"
                         f"上限 {trader.CFG['maxPositions']} 筆）")
            p = trader.performance()
            if p.get("count"):
                lines.append(f"績效　{p['count']} 筆　勝率 {p['winRate']}%　"
                             f"賺賠 {p.get('payoff') or '—'}　期望 {p['expectancyR']:+.3f}R")
        else:
            lines.append("交易　未設定幣安金鑰，僅作為雷達與資料服務")

        mon = "運作中" if MON["on"] else "未啟用"
        lines.append(f"監控　{mon}　資料來源 {CG_UPSTREAM or 'CoinGecko 直連'}")
        lines += pf_lines
        if any(l.startswith("自檢") and "異常" in l for l in pf_lines) and not head.startswith("⚠"):
            head = "⚠ " + head + "（自檢有異常）"

        # 自動下單開著、但這台沒有訊號來源：等於永遠不會下單
        if trader and trader.AUTO["on"] and not MON["on"]:
            head = "⚠ 自動下單不會觸發"
            lines.append("")
            lines.append("自動下單已啟用，但這台的「伺服器背景監控」未啟動，不會收到任何訊號。")
            lines.append("注意不是提醒設定頁上方那個「訊號監控」（只在網頁開著時運作），")
            lines.append("而是下方的「伺服器背景監控」→ 按「同步並啟動背景監控」。")
        text = "\n".join(lines)

        # 去重：同一份摘要 6 小時內只推一次
        sig = hashlib.sha1((head + text).encode()).hexdigest()[:16]
        p = os.path.join(CACHE_DIR, "last_startup.json")
        try:
            with open(p) as f:
                prev = json.load(f)
            if prev.get("sig") == sig and time.time() - prev.get("ts", 0) < 6 * 3600:
                sys.stderr.write("  ~ 啟動摘要與前次相同，6 小時內不重複推播\n")
                return
        except Exception:
            pass
        try:
            with open(p, "w") as f:
                json.dump({"sig": sig, "ts": time.time()}, f)
        except Exception:
            pass
        push_all(head, text)

    threading.Thread(target=_startup_report, daemon=True).start()
    if trader:
        threading.Thread(target=position_worker,
                         args=(float(os.environ.get("POSITION_POLL", 20)),),
                         daemon=True).start()

    if engine is None:
        print("  監控　找不到 engine.py，背景訊號監控停用")
    else:
        print(f"  監控　每 {args.monitor_every:.0f} 分鐘檢查一次"
              + (f"，範圍：{'市值前 ' + str(MON['topN']) + ' 檔' if MON['scope'] == 'top' else '觀察清單 ' + str(len(MON['watch'])) + ' 檔'}，狀態：開啟"
                 if MON["on"] and MON["cfg"] else "，尚未從網頁同步設定"))
        threading.Thread(target=monitor_worker, args=(args.monitor_every,), daemon=True).start()

    if args.prefetch:
        n_cached = len([f for f in os.listdir(CACHE_DIR)]) if os.path.isdir(CACHE_DIR) else 0
        print(f"  預抓　背景抓取前 {args.prefetch} 檔，保鮮 {args.prefetch_ttl} 小時"
              f"（磁碟已有 {n_cached} 筆）")
        print(f"        以每 {CFG['gap']} 秒一次估算，首輪約需 {args.prefetch * CFG['gap'] / 60:.0f} 分鐘")
        threading.Thread(target=prefetch_worker, args=(args.prefetch, args.prefetch_ttl), daemon=True).start()

    if not os.path.exists(os.path.join(STATIC_DIR, "index.html")):
        print(f"  ! 找不到 index.html（{STATIC_DIR}）。開發時先到 frontend 執行 npm run build")

    hosted = bool(os.environ.get("PORT"))          # Zeabur、Railway 等平台會設這個變數
    host = "0.0.0.0" if (args.lan or hosted) else "127.0.0.1"
    with Server((host, args.port), Handler) as httpd:
        if hosted:
            print(f"\n  雲端模式：監聽 0.0.0.0:{args.port}")
        print(f"\n  這台電腦 → http://localhost:{args.port}")
        if args.lan:
            ip = lan_ip()
            if ip:
                print(f"  手機請連 → http://{ip}:{args.port}   （需在同一個 Wi-Fi）")
                print("  手機開啟後，到連線設定勾選「透過本機代理」")
            else:
                print("  已開放區網連入，但查不到本機 IP，請自行用 ipconfig / ifconfig 查詢")
            print("  提醒：這會讓同網段的裝置都能存取，公用 Wi-Fi 請勿使用 --lan")
        print("  頁面上的「透過本機代理」會自動勾選，按重新整理就會載入即時行情。")
        print("  Ctrl+C 結束\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  已停止。")


if __name__ == "__main__":
    main()
