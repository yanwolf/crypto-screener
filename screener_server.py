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
}

CACHE_TTL = 45.0          # 行情快取秒數，重新整理不會重複打 API
OHLC_TTL = 1800.0         # 90 日 K 線快取半小時，這種資料不會秒變

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

CFG = {"key": "", "pro": False, "gap": 2.4}
NOTIFY = {"tg_token": "", "tg_chat": "", "discord": "",
          "smtp_host": "", "smtp_port": 587, "smtp_user": "", "smtp_pass": "", "mail_to": ""}


def notify_telegram(title, text):
    if not (NOTIFY["tg_token"] and NOTIFY["tg_chat"]):
        return None
    url = f"https://api.telegram.org/bot{NOTIFY['tg_token']}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": NOTIFY["tg_chat"], "text": f"{title}\n{text}"}).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=20):
        return "Telegram"


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


def upstream_url(path_qs: str) -> str:
    base = PRO_BASE if CFG["pro"] else PUBLIC_BASE
    url = base + path_qs
    if CFG["key"]:
        sep = "&" if "?" in url else "?"
        param = "x_cg_pro_api_key" if CFG["pro"] else "x_cg_demo_api_key"
        url += f"{sep}{param}={urllib.parse.quote(CFG['key'])}"
    return url


def ttl_for(path_qs: str) -> float:
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
    gap = CFG["gap"] if prefix == "/api/v3" else 2.2
    lock = _gate_lock if prefix == "/api/v3" else _chain_lock
    slot = _last_call if prefix == "/api/v3" else _last_chain
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
        for prefix in UPSTREAMS:
            if self.path.startswith(prefix + "/"):
                return self.handle_proxy(prefix)
        if self.path in ("/", ""):
            self.path = "/crypto-screener.html"
        return super().do_GET()

    def do_POST(self):
        if self.path != "/api/notify":
            return self.send_json(404, b'{"error":"not_found"}')
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self.send_json(400, b'{"error":"bad_json"}')
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
                st, b = fetch_upstream(path)
                if st == 200:
                    cache_put(key, b)
                    done += 1
                elif st == 429:
                    time.sleep(30)
            sys.stderr.write(f"  ~ 背景預抓完成一輪：新增 {done} 檔、快取命中 {skipped} 檔\n")
        except Exception as e:
            sys.stderr.write(f"  ~ 背景預抓中斷：{e}\n")
        time.sleep(300)


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
    ap.add_argument("--key", default=os.environ.get("CG_API_KEY", ""), help="CoinGecko API Key")
    ap.add_argument("--pro", action="store_true", help="使用 Pro 端點")
    ap.add_argument("--tg-token", default=os.environ.get("TG_TOKEN", ""), help="Telegram Bot Token")
    ap.add_argument("--tg-chat", default=os.environ.get("TG_CHAT", ""), help="Telegram Chat ID")
    ap.add_argument("--discord", default=os.environ.get("DISCORD_WEBHOOK", ""), help="Discord Webhook 網址")
    ap.add_argument("--smtp-host", default=os.environ.get("SMTP_HOST", ""), help="SMTP 伺服器，例如 smtp.gmail.com")
    ap.add_argument("--smtp-port", type=int, default=int(os.environ.get("SMTP_PORT", 587)))
    ap.add_argument("--smtp-user", default=os.environ.get("SMTP_USER", ""))
    ap.add_argument("--smtp-pass", default=os.environ.get("SMTP_PASS", ""), help="建議用應用程式密碼")
    ap.add_argument("--mail-to", default=os.environ.get("MAIL_TO", ""), help="收件信箱")
    ap.add_argument("--prefetch", type=int, default=0, metavar="N",
                    help="背景預抓市值前 N 檔的 90 日資料，讓瀏覽器直接命中快取")
    ap.add_argument("--prefetch-ttl", type=float, default=12, metavar="H", help="預抓資料的保鮮時數，預設 12")
    args = ap.parse_args()

    NOTIFY.update({
        "tg_token": args.tg_token.strip(), "tg_chat": args.tg_chat.strip(),
        "discord": args.discord.strip(), "smtp_host": args.smtp_host.strip(),
        "smtp_port": args.smtp_port, "smtp_user": args.smtp_user.strip(),
        "smtp_pass": args.smtp_pass, "mail_to": args.mail_to.strip(),
    })

    CFG["key"] = args.key.strip()
    CFG["pro"] = args.pro
    CFG["gap"] = 0.9 if CFG["key"] else 2.4

    print("\n  加密貨幣篩選終端 — 本機伺服器")
    print(f"  金鑰　{'已設定（' + ('Pro' if CFG['pro'] else 'Demo') + '）' if CFG['key'] else '未設定，額度僅 5–15 次/分'}")
    print(f"  節流　每 {CFG['gap']} 秒最多一次上游請求，行情快取 {int(CACHE_TTL)} 秒、K 線 {int(OHLC_TTL/60)} 分鐘")
    print("  鏈上　/api/gt 轉發 GeckoTerminal、/api/gp 轉發 GoPlus，另用一組節流閘")

    chans = [n for n, v in (("Telegram", NOTIFY["tg_token"] and NOTIFY["tg_chat"]),
                            ("Discord", NOTIFY["discord"]),
                            ("電子郵件", NOTIFY["smtp_host"] and NOTIFY["mail_to"])) if v]
    print(f"  通知　{'、'.join(chans) if chans else '未設定，僅能用瀏覽器通知'}")

    ok = selftest()

    if args.prefetch:
        n_cached = len([f for f in os.listdir(CACHE_DIR)]) if os.path.isdir(CACHE_DIR) else 0
        print(f"  預抓　背景抓取前 {args.prefetch} 檔，保鮮 {args.prefetch_ttl} 小時"
              f"（磁碟已有 {n_cached} 筆）")
        print(f"        以每 {CFG['gap']} 秒一次估算，首輪約需 {args.prefetch * CFG['gap'] / 60:.0f} 分鐘")
        threading.Thread(target=prefetch_worker, args=(args.prefetch, args.prefetch_ttl), daemon=True).start()

    page = os.path.join(HERE, "crypto-screener.html")
    if not os.path.exists(page):
        print(f"  ! 找不到 crypto-screener.html，請把它放到 {HERE}")

    with Server(("127.0.0.1", args.port), Handler) as httpd:
        print(f"\n  開啟瀏覽器 → http://localhost:{args.port}")
        if ok:
            print("  頁面上的「透過本機代理」會自動勾選，按重新整理就會載入即時行情。")
        print("  Ctrl+C 結束\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  已停止。")


if __name__ == "__main__":
    main()
