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
QUOTA = {"exhausted": False, "ts": 0}
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
_tg_lock = threading.Lock()
_pending = {}          # code -> {"ts": float, "chat": dict|None}


def tg_file():
    return os.path.join(CACHE_DIR, "telegram.json")


def tg_load():
    try:
        with open(tg_file(), "r") as f:
            _tg.update(json.load(f))
    except Exception:
        pass
    if not _tg["token"] and NOTIFY["tg_token"]:
        _tg["token"] = NOTIFY["tg_token"]          # 環境變數設定的權杖
    if NOTIFY["tg_chat"] and not any(c["id"] == NOTIFY["tg_chat"] for c in _tg["chats"]):
        _tg["chats"].append({"id": NOTIFY["tg_chat"], "name": "環境變數設定", "ts": time.time()})


def tg_save():
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(tg_file(), "w") as f:
            json.dump(_tg, f)
    except Exception:
        pass


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
        cid = str(payload.get("id", ""))
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
except Exception:
    trader = None

MON = {"on": False, "watch": [], "cfg": None, "states": {}, "scope": "watch", "topN": 100,
       "history": [], "lastRun": None, "lastCount": 0, "lastError": None,
       "histTTL": 12, "maxRefresh": 8, "lastRefreshed": 0, "callsToday": 0}
_mon_lock = threading.Lock()


def mon_file():
    return os.path.join(CACHE_DIR, "monitor.json")


def mon_load():
    try:
        with open(mon_file(), "r") as f:
            MON.update(json.load(f))
    except Exception:
        pass


def mon_save():
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(mon_file(), "w") as f:
            json.dump({k: MON[k] for k in ("on", "watch", "cfg", "states", "history",
                                           "lastRun", "lastCount", "scope", "topN",
                                           "histTTL", "maxRefresh", "callsToday")}, f)
    except Exception:
        pass


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
    "failed": "下單失敗",
    "opened": "已下單",
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
    lines.append(f"（{net}）")
    return "自動開倉", "\n".join(lines)


def notify_trade_close(t):
    """平倉通知。R 倍數是重點，金額只是附帶。"""
    win = (t.get("pnl") or 0) > 0
    rm = t.get("rMultiple")
    head = "獲利平倉" if win else "虧損平倉"
    mark = "＋" if win else "－"
    lines = [
        f"{'▲' if t['side'] == 'LONG' else '▼'} {t['symbol']}　{t['side'] == 'LONG' and '做多' or '做空'}",
        f"進場　{fmt_money(t['entry'])}",
        f"出場　{fmt_money(t['exit'])}",
        f"損益　{mark}{fmt_money(abs(t.get('pnl') or 0))} U"
        + (f"　{rm:+.2f}R" if rm is not None else ""),
        f"原因　{t.get('reason', '—')}",
    ]
    held = (t.get("closed", 0) - t.get("opened", 0)) / 60000.0
    if held > 0:
        lines.append(f"持有　{int(held // 60)} 小時 {int(held % 60)} 分")

    if trader:
        p = trader.performance()
        if p.get("count"):
            lines.append("")
            lines.append(f"累計 {p['count']} 筆　勝率 {p['winRate']}%　"
                         f"賺賠比 {p.get('payoff') or '—'}　期望值 {p['expectancyR']}R")
            a = trader.AUTO
            lines.append(f"今日 {a['opened']} 筆　已實現 {a['closedR']:+.2f}R")
            if a.get("blocked"):
                lines.append(f"⚠ {a['blocked']}")
    return head, "\n".join(lines)


def push_all(title, text):
    for fn in (notify_telegram, notify_discord, notify_email):
        try:
            fn(title, text)
        except Exception:
            pass


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
            st, body = fetch_upstream(path)
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

    # 停損來源與畫面一致：空頭用結構停損（row["stop"]），
    # 多頭用三刀流的小綠（row["ma60"]，在 row 頂層，不在 blades 底下）。
    if bear:
        stop = row.get("stop") or ev.get("stop")
    else:
        ma60 = row.get("ma60")
        price0 = row.get("price") or ev.get("price")
        stop = ma60 * 0.995 if ma60 else (price0 * 0.94 if price0 else None)
    price = row.get("price") or ev.get("price")
    if stop is None or price is None or (stop > price) != bear:
        return {"stage": "stop", "why": "算不出合理的停損價，不送無停損的單"}

    # 幣安沒有合約就不可能下單，這裡才擋，讓前面的原因先被看到
    if dv is None and dv_err:
        ok_sym, _, msg, _ = trader.check_tradable(sym)
        if not ok_sym:
            return {"stage": "gate", "why": msg}

    note = f"{'空頭' if bear else '多頭'}雷達 {engine._fmt1(score)} 分"
    if warns:
        note += "（" + "；".join(warns) + "）"

    r = trader.auto_open(sym, "SHORT" if bear else "LONG", price, float(stop), note=note)
    if r.get("ok"):
        return {"stage": "opened", "why": None, "result": r, "note": "；".join(warns) or None}
    if r.get("skipped"):
        return {"stage": "risk", "why": r.get("error")}
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



def position_worker(every_s=20):
    """獨立的部位監看執行緒。

    停損停利是掛在幣安上的，觸發不需要我們輪詢；
    這裡只是盡快「發現」它成交了，好記錄績效與推播。
    綁在 30 分鐘的行情監控上沒有意義——幣安的額度與 CoinGecko 無關，
    每 20 秒一次也只用掉不到 1% 的權重。
    """
    idle = 0
    while True:
        try:
            if trader and trader.STATE["positions"]:
                idle = 0
                before = len(trader.STATE["trades"])
                trader.sync_positions()
                for t in trader.STATE["trades"][before:]:
                    ti, tx = notify_trade_close(t)
                    push_all(ti, tx)

                # 主動管理：到 1R 把停損移到成本
                for ev in trader.manage_positions():
                    if ev.get("ok"):
                        push_all("停損移至成本",
                                 f"{ev['symbol']} 已到 {trader.CFG['breakevenR']}R，"
                                 f"停損從 {ev['old']:g} 移到 {ev['new']:g}（現價 {ev['mark']:g}）。\n"
                                 f"這筆最差就是打平。")
                        sys.stderr.write(f"  $ {ev['symbol']} 停損移至成本 {ev['new']:g}\n")
                    else:
                        sys.stderr.write(f"  ! {ev['symbol']} 移損失敗：{ev.get('why')}\n")

                # 確認停損還在。預設只警告不平倉——
                # 一次誤判造成的平倉，比暫時裸倉幾十秒的損失更大。
                for ev in trader.guard_positions():
                    a = ev["action"]
                    if a == "restored":
                        push_all("停損已補掛",
                                 f"{ev['symbol']} 連續三輪確認停損不在，已重新掛回 {ev['stop']:g}。")
                    elif a == "false_alarm":
                        sys.stderr.write(f"  ~ {ev['symbol']} 停損檢查誤報（交易所回報已存在），不動作\n")
                    elif a == "alert":
                        push_all("⚠ 需要你處理",
                                 f"{ev['symbol']} 連續三輪查不到停損單，補掛也失敗。\n"
                                 f"原因：{ev.get('why')}\n"
                                 f"請到幣安確認。若要讓系統自動平倉，在設定開啟 guardClose。")
                    elif a == "closed":
                        push_all("強制平倉",
                                 f"{ev['symbol']} 停損單遺失且無法補掛，依設定已平倉。\n原因：{ev.get('why')}")
            else:
                idle += 1
        except Exception as e:
            sys.stderr.write(f"  ! 部位監看失敗：{str(e)[:100]}\n")
        # 沒有部位時放慢，不必空轉
        time.sleep(every_s if (trader and trader.STATE["positions"]) else 60)


def monitor_worker(interval_min):
    time.sleep(8)
    while True:
        try:
            if MON["on"]:
                mon_run_once()          # 額度用盡會自動降級為無金鑰，仍可續跑

                MON["lastError"] = None
        except Exception as e:
            MON["lastError"] = str(e)
            sys.stderr.write(f"  ! 背景監控失敗：{e}\n")
        time.sleep(max(60, interval_min * 60))



def trade_handle(path, payload):
    """模擬單相關端點。金鑰只留在伺服器，前端永遠拿不到。"""
    if trader is None:
        return 500, {"error": "trader_module_missing"}

    if path == "/api/trade/status":
        st = trader.status()
        st["adminRequired"] = bool(os.environ.get("ADMIN_KEY", "").strip())
        st["diskMB"] = cache_disk_mb()
        st["poll"] = float(os.environ.get("POSITION_POLL", 20))
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

    need = os.environ.get("ADMIN_KEY", "").strip()
    if need and str(payload.get("adminKey", "")) != need:
        return 403, {"error": "admin_key_required"}

    if path == "/api/trade/config":
        limits = {"riskPct": (0.1, 5.0), "maxPositions": (1, 20), "leverage": (1, 20),
                  "stopAtrMult": (0.5, 5.0), "tp1R": (1.0, 10.0), "tp1Portion": (0.0, 1.0),
                  "trailCallback": (0.1, 10.0), "trailActivateR": (0.5, 10.0),
                  "trailR": (0.0, 3.0), "breakevenR": (0.0, 5.0), "guardClose": (0, 1)}
        kw = {}
        for k, (lo, hi) in limits.items():
            if k in payload:
                try:
                    v = float(payload[k])
                except (TypeError, ValueError):
                    continue
                v = max(lo, min(hi, v))
                kw[k] = bool(v) if k == "guardClose" else int(v) if k in ("maxPositions", "leverage") else v
        return 200, {"cfg": trader.configure(**kw)}

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
        if "on" in payload:
            trader.AUTO["on"] = bool(payload["on"])
            if trader.AUTO["on"]:
                trader.AUTO["blocked"] = None      # 手動重啟時解除當日封鎖
        for k in ("maxPerDay", "cooldownMin", "minScore"):
            if k in payload:
                trader.AUTO[k] = int(payload[k])
        if "dailyLossR" in payload:
            trader.AUTO["dailyLossR"] = -abs(float(payload["dailyLossR"]))
        trader.save_state()
        return 200, {"auto": trader.status()["auto"]}

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
        r = trader.open_position(base, side, entry, float(stop), note=str(payload.get("note", "")))
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
        with _mon_lock:
            if "watch" in payload:
                MON["watch"] = [str(x) for x in payload["watch"]][:250]
            if "scope" in payload:
                MON["scope"] = "top" if payload["scope"] == "top" else "watch"
            if "topN" in payload:
                MON["topN"] = max(10, min(250, int(payload["topN"])))
            if "histTTL" in payload:
                MON["histTTL"] = max(2, min(72, float(payload["histTTL"])))
            if "maxRefresh" in payload:
                MON["maxRefresh"] = max(0, min(50, int(payload["maxRefresh"])))
            if "cfg" in payload:
                MON["cfg"] = payload["cfg"]
            if "on" in payload:
                MON["on"] = bool(payload["on"])
            mon_save()
        return 200, {"ok": True, "on": MON["on"], "watch": len(MON["watch"]),
                     "scope": MON["scope"], "topN": MON["topN"]}
    if path == "/api/monitor/history":
        return 200, {"history": MON["history"][:200]}
    if path == "/api/monitor/run":
        try:
            n = mon_run_once()
            return 200, {"ok": True, "fired": n, "checked": MON["lastCount"]}
        except Exception as e:
            return 500, {"error": str(e)}
    return 404, {"error": "not_found"}



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

PROTECTED = {"telegram.json", "monitor.json", "trader.json"}

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
                ts = float(json.load(f).get("ts") or st.st_mtime)
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
        return None
    sent = 0
    for c in list(_tg["chats"]):
        try:
            tg_api("sendMessage", {"chat_id": c["id"], "text": f"{title}\n{text}"}, timeout=20)
            sent += 1
        except Exception:
            pass
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


def fetch_upstream(path_qs: str, prefix: str = "/api/v3"):
    """回傳 (status, body_bytes)。含節流與 429 退避。"""
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
        super().__init__(*a, directory=HERE, **kw)

    # ── 路由 ──────────────────────────────────────────────
    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/api/health":
            info = {
                "server": "crypto-screener", "proxy": True,
                "hasKey": bool(CFG["key"]), "keyLen": len(CFG["key"]),
                "pro": CFG["pro"], "engine": bool(engine),
                "monitor": MON["on"], "cached": len(_cache),
                "quotaExhausted": QUOTA["exhausted"], "keyless": keyless_now(),
                "disk": (len(os.listdir(CACHE_DIR)) if os.path.isdir(CACHE_DIR) else 0),
                "diskMB": cache_disk_mb(),
                "refresh": {k: REFRESH[k] for k in
                            ("last", "lastTs", "callsToday", "budget", "interval",
                             "fresh", "stale", "never", "n")},
                "gap": CFG["gap"], "uptime": int(time.time() - START_TS),
            }
            if "probe=1" in (self.path.split("?", 1)[1] if "?" in self.path else ""):
                info["probe"] = upstream_probe()
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
            code, body = trade_handle(p, payload)
            return self.send_json(code, json.dumps(body, ensure_ascii=False).encode())

        if p.startswith("/api/monitor/"):
            code, body = mon_handle(p, {})
            return self.send_json(code, json.dumps(body, ensure_ascii=False).encode())
        for prefix in UPSTREAMS:
            if self.path.startswith(prefix + "/"):
                return self.handle_proxy(prefix)
        if self.path in ("/", ""):
            self.path = "/index.html" if os.path.exists(os.path.join(HERE, "index.html")) else "/crypto-screener.html"
        return super().do_GET()

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self.send_json(400, b'{"error":"bad_json"}')

        if self.path.startswith("/api/tg/"):
            code, body = tg_handle(self.path, payload)
            return self.send_json(code, json.dumps(body, ensure_ascii=False).encode())

        if self.path.startswith("/api/trade/"):
            code, body = trade_handle(self.path.split("?")[0], payload)
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
            return self.send_json(200, cached, cached=True)

        # 快取過期但還在寬限期內：先把舊資料送出去，背景再更新。
        # 節流閘與重試會讓同步等待長達十幾秒，這段等待對使用者沒有價值——
        # 行情差幾十秒不影響量能倍數的判讀，畫面卡住才是問題。
        stale, age = cache_peek(key)
        if stale is not None and age is not None and age < ttl + STALE_GRACE:
            revalidate_async(path_qs, prefix, key)
            return self.send_json(200, stale, cached=True, stale=int(age))

        status, body = fetch_upstream(path_qs, prefix)
        if status == 200:
            cache_put(key, body)
        self.send_json(status, body)

    def send_json(self, code, body, cached=False, stale=None):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        if stale is not None:
            self.send_header("X-Stale-Age", str(stale))
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

            cid, why = pick_next(count, ttl_h)
            if cid is None:
                time.sleep(interval)
                continue

            st, b = fetch_upstream(f"/coins/{cid}/market_chart?vs_currency=usd&days=90")
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
        trader.CFG["key"] = args.bn_key.strip()
        trader.CFG["secret"] = args.bn_secret.strip()
        trader.CFG["live"] = want_live
        trader.CFG["riskPct"] = args.risk_pct
        trader.CFG["leverage"] = args.leverage
        trader.CFG["maxPositions"] = max(1, min(20, args.max_positions))
        trader.load_state(os.path.join(CACHE_DIR, "trader.json"))
        net = "正式網（真實資金）" if want_live else "模擬網 Testnet"
        if trader.CFG["key"]:
            sys.stderr.write(f"  交易　{net}　風險 {args.risk_pct}%/筆　槓桿 {args.leverage}x　"
                             f"同時持倉上限 {trader.CFG['maxPositions']}　（連線資訊背景載入中）\n")
            if want_live:
                sys.stderr.write("  ⚠ 正在對正式網下單，會動用真實資金\n")

            def _trader_warmup():
                off = trader.sync_time()
                n = len(trader.load_filters())
                eq, err = trader.account_equity()
                sys.stderr.write(
                    f"  交易　合約 {n} 檔　時鐘差 {off if off is not None else '?'}ms　"
                    f"權益 {('%.2f USDT' % eq) if eq is not None else '取不到（' + str(err) + '）'}\n")

            threading.Thread(target=_trader_warmup, daemon=True).start()
        else:
            sys.stderr.write(f"  交易　未設定 BN_KEY／BN_SECRET，模擬單功能停用（{net}）\n")

    threading.Thread(target=selftest, daemon=True).start()
    threading.Thread(target=cleanup_worker, daemon=True).start()
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

    if not any(os.path.exists(os.path.join(HERE, f)) for f in ("index.html", "crypto-screener.html")):
        print(f"  ! 找不到 index.html，請把頁面檔放到 {HERE}")

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
