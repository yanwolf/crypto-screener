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
OHLC_TTL = 1800.0         # 90 日 K 線快取半小時，這種資料不會秒變

START_TS = time.time()
QUOTA = {"exhausted": False, "ts": 0}
_cache = {}
_cache_lock = threading.Lock()
CACHE_DIR = os.path.join(HERE, ".screener_cache")
DISK_TTL = 12 * 3600.0          # 深度資料寫入磁碟，重啟後仍可用


def _disk_path(key: str) -> str:
    return os.path.join(CACHE_DIR, hashlib.sha1(key.encode()).hexdigest() + ".json")


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
    with _mon_lock:
        MON["states"] = states
        MON["lastRun"] = now_ms
        MON["lastCount"] = len(rows)
        MON["lastRefreshed"] = refreshed[0]
        MON["callsToday"] = MON.get("callsToday", 0) + 1 + refreshed[0]
        if events:
            MON["history"] = (events + MON["history"])[:400]
        mon_save()

    for e in events:
        title, text = engine.alert_text(e)
        for fn in (notify_telegram, notify_discord, notify_email):
            try:
                fn(title, text)
            except Exception:
                pass
    if events:
        sys.stderr.write(f"  ! 背景監控觸發 {len(events)} 則："
                         f"{'、'.join(x['sym'] + '/' + x['side'] for x in events)}\n")
    return len(events)


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
        allowed = ("riskPct", "maxPositions", "leverage", "stopAtrMult",
                   "tp1R", "tp1Portion", "trailCallback", "trailActivateR")
        kw = {k: payload[k] for k in allowed if k in payload}
        return 200, {"cfg": trader.configure(**kw)}

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
        cached = cache_get(key, ttl_for(path_qs))
        if cached is not None:
            return self.send_json(200, cached, cached=True)

        status, body = fetch_upstream(path_qs, prefix)
        if status == 200:
            cache_put(key, body)
        self.send_json(status, body)

    def send_json(self, code, body, cached=False):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
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


def prefetch_worker(count: int, ttl_h: float):
    """在背景把額度用滿，持續把深度資料抓進快取。
    瀏覽器要用的時候直接命中快取，不必當場等 30 次/分的節流。"""
    time.sleep(3)
    while True:
        try:
            st, body = fetch_upstream(
                "/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=1")
            if st != 200:
                time.sleep(120)
                continue
            ids = [c["id"] for c in json.loads(body)][:count]
            done = skipped = 0
            for cid in ids:
                path = f"/coins/{cid}/market_chart?vs_currency=usd&days=90"
                key = "/api/v3" + path
                if cache_get(key, ttl_h * 3600) is not None:
                    skipped += 1
                    continue
                if QUOTA["exhausted"]:
                    sys.stderr.write("  ~ 額度已用盡，預抓暫停（監控仍以無金鑰模式續跑）\n")
                    break
                st, b = fetch_upstream(path)
                if st == 200:
                    cache_put(key, b)
                    done += 1
                elif st == 429:
                    time.sleep(30)
            sys.stderr.write(f"  ~ 背景預抓完成一輪：新增 {done} 檔、快取命中 {skipped} 檔\n")
        except Exception as e:
            sys.stderr.write(f"  ~ 背景預抓中斷：{e}\n")
        time.sleep(3600)      # 預抓每小時跑一輪就夠，快取命中不耗額度


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
    ap.add_argument("--prefetch-ttl", type=float, default=float(os.environ.get("PREFETCH_TTL", 24)),
                    metavar="H", help="預抓資料的保鮮時數，預設 12（等同環境變數 PREFETCH_TTL）")
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
    if trader is not None:
        want_live = args.live and os.environ.get("ALLOW_LIVE", "") == "1"
        if args.live and not want_live:
            sys.stderr.write("  ! 已指定 --live 但未設 ALLOW_LIVE=1，仍使用模擬網\n")
        trader.CFG["key"] = args.bn_key.strip()
        trader.CFG["secret"] = args.bn_secret.strip()
        trader.CFG["live"] = want_live
        trader.CFG["riskPct"] = args.risk_pct
        trader.CFG["leverage"] = args.leverage
        trader.load_state(os.path.join(CACHE_DIR, "trader.json"))
        net = "正式網（真實資金）" if want_live else "模擬網 Testnet"
        if trader.CFG["key"]:
            off = trader.sync_time()
            n = len(trader.load_filters())
            eq, err = trader.account_equity()
            sys.stderr.write(
                f"  交易　{net}　風險 {args.risk_pct}%/筆　槓桿 {args.leverage}x　"
                f"合約 {n} 檔　時鐘差 {off if off is not None else '?'}ms\n")
            sys.stderr.write(f"  權益　{('%.2f USDT' % eq) if eq is not None else '取不到（' + str(err) + '）'}\n")
            if want_live:
                sys.stderr.write("  ⚠ 正在對正式網下單，會動用真實資金\n")
        else:
            sys.stderr.write(f"  交易　未設定 BN_KEY／BN_SECRET，模擬單功能停用（{net}）\n")

    ok = selftest()

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
        if ok:
            print("  頁面上的「透過本機代理」會自動勾選，按重新整理就會載入即時行情。")
        print("  Ctrl+C 結束\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  已停止。")


if __name__ == "__main__":
    main()
