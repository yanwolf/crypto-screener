"""CoinGecko 額度（2026-09-29：Demo 月額度 10,000 次在 29 日用完、公開端點又擋伺服器 IP，監控停擺兩天）。

- 背景監控：幣安沒有合約的幣、已經持倉的幣，歷史保鮮拉長（HIST_TTL_LOW，預設 72 小時）；幣安上架或平倉後回到正常。
- 代理：90 天歷史的保鮮跟監控一致（以前 30 分鐘——模擬網與網頁來要時白花額度）。
- 網頁背景掃描只讀伺服器快取（X-Cache-Only），伺服器沒有就回「沒有」、不打上游。
- 上游錯誤講清楚（額度用完／公開端點擋 IP）。

    python3 -m tests.test_cg_budget
"""
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, ROOT)
from tests import test_r42 as R                                 # noqa: E402
from tests.fake_exchange import need                            # noqa: E402
from tests.harness import make_case                             # noqa: E402

RESULTS = []
case = make_case(RESULTS)
fresh, main_mod = R.fresh, R.main_mod
CHART = json.dumps({"prices": [[i, 1.0] for i in range(90)], "total_volumes": [[i, 100.0] for i in range(90)]}).encode()


def T():
    return R.T


def age_cache(M, key, age_s):
    """放一份 age_s 秒前的快取：記憶體與磁碟都要（磁碟檔案裡自己記了時間）。"""
    old = time.time() - age_s
    M.cache_put(key, CHART)
    with M._cache_lock:
        M._cache[key] = (old, CHART)
    p = M._disk_path(key)
    if os.path.exists(p):
        blob = json.load(open(p, encoding="utf-8"))
        blob["ts"] = old
        json.dump(blob, open(p, "w", encoding="utf-8"))


def setup(M):
    """模擬一輪監控需要的東西：三檔幣（能交易的 AAA、幣安沒有的 BBB、已持倉的 CCC），各有一份 20 小時前的歷史。"""
    need(hasattr(M, "HIST_TTL_LOW") and hasattr(M, "low_priority"), "程式沒有低優先保鮮（前提：2026-09-29 起才有）")
    M.CACHE_DIR = tempfile.mkdtemp()
    T()._filters.update({"AAAUSDT": {"status": "TRADING"}, "CCCUSDT": {"status": "TRADING"}})
    T()._filters_ts = 9e18
    T().STATE["positions"]["CCCUSDT"] = {"symbol": "CCCUSDT", "side": "LONG", "qty": 1}
    M.trader = T()
    M.MON.update({"cfg": {"bull": {"x": 1}, "bear": {"x": 1}}, "scope": "top", "topN": 10, "histTTL": 12, "maxRefresh": 8})
    markets = [{"id": s.lower(), "symbol": s.lower(), "name": s, "current_price": 1, "total_volume": 100, "market_cap": 1e6}
               for s in ("AAA", "BBB", "CCC")]
    M.mon_fetch_json = lambda path: markets
    for s in ("aaa", "bbb", "ccc"):
        age_cache(M, f"/api/v3/coins/{s}/market_chart?vs_currency=usd&days=90", 20 * 3600)
    calls = []

    def fake_up(path, prefix="/api/v3", background=False):
        calls.append(path)
        return 200, CHART
    M.fetch_upstream = fake_up
    M.engine.analyze = lambda base, scan: dict(base, scanned=True)
    M.engine.extract_scan = lambda chart, vol: {}
    M.engine.evaluate = lambda rows, cfg, states, now: ([], states)
    M.push_all = lambda *a, **k: None
    return calls


def get_usage(M):
    h = M.Handler.__new__(M.Handler)
    h.path = "/api/cg/usage"
    h.headers = {}
    out = {}
    h.send_json = lambda code, body, **k: out.update(code=code, body=json.loads(body))
    h.do_GET()
    need(out, "用量端點沒有回應（前提）")
    return out


@case("b-8", "網頁用量端點：回今日與本月各來源次數、上限（Demo 10,000）、額度用完與否；重啟後本月計數讀得回來")
def _():
    ex, clock = fresh()
    M = main_mod()
    need(hasattr(M, "cg_usage") and hasattr(M, "cg_calls_load"), "程式沒有用量端點（前提）")
    M.CACHE_DIR = tempfile.mkdtemp()
    M.CFG["key"], M.CFG["pro"] = "k", False
    with M.cg_source("網頁"):
        M._count_cg("/coins/x/market_chart?vs_currency=usd&days=90")
    with M.cg_source("監控"):
        M._count_cg("/coins/markets?vs_currency=usd")
    M._cg_calls_save(force=True)
    out = get_usage(M)
    u = out["body"]
    if out["code"] != 200 or u.get("limit") != 10000:
        return f"用量端點不對：{out}"
    if sum(u.get("thisMonth", {}).values()) != 2 or "網頁·history" not in u["thisMonth"]:
        return f"本月計數不對：{u.get('thisMonth')}"
    cache_dir = M.CACHE_DIR
    M = main_mod()                                              # 重啟
    M.CACHE_DIR = cache_dir
    M.cg_calls_load()
    u2 = get_usage(M)["body"]
    if sum(u2.get("thisMonth", {}).values()) != 2:
        return f"重啟後本月計數沒讀回來：{u2.get('thisMonth')}（每次部署都從 0 開始就看不出用了多少）"


@case("b-9", "模擬網的用量端點：問正式網伺服器（額度在那邊），不是回自己的轉發次數；問不到就講明")
def _():
    ex, clock = fresh()
    M = main_mod()
    need(hasattr(M, "cg_usage"), "程式沒有用量端點（前提）")
    M.CG_UPSTREAM = "http://upstream.invalid"
    seen = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"thisMonth": {"監控·markets": 123}, "limit": 10000}).encode()

    def fake_open(req, timeout=0):
        seen.append(req.full_url)
        return Resp()
    real = M.urllib.request.urlopen
    M.urllib.request.urlopen = fake_open
    try:
        u = get_usage(M)["body"]
    finally:
        M.urllib.request.urlopen = real
    if not seen or not seen[0].endswith("/api/cg/usage"):
        return f"模擬網沒有去問上游：{seen}"
    if u.get("via") != "upstream" or u.get("thisMonth", {}).get("監控·markets") != 123:
        return f"沒有回上游的用量：{u}"
    M.urllib.request.urlopen = lambda req, timeout=0: (_ for _ in ()).throw(OSError("down"))
    try:
        u3 = get_usage(M)["body"]
    finally:
        M.urllib.request.urlopen = real
    if "問不到上游" not in str(u3.get("error")):
        return f"上游問不到時沒講明：{u3}"


def R55_proxy(M, key, cache_only=False):
    """直接呼叫代理處理（不開伺服器）。"""
    h = M.Handler.__new__(M.Handler)
    h.path = key
    h.headers = {"X-Cache-Only": "1"} if cache_only else {}
    out = {}
    h.send_json = lambda code, body, **k: out.update(code=code, body=body, xcgerr=k.get("cg_error"))
    h.send_response = lambda code: out.update(code=code)
    h.send_header = lambda k, v: out.update(xcache=v) if k == "X-Cache" else None
    h.end_headers = lambda: None
    h.handle_proxy("/api/v3")
    need(out, "代理沒有回應（前提）")
    return out


@case("b-1", "背景監控：幣安沒有合約的幣、已持倉的幣，20 小時前的歷史還算新鮮（不打上游）；能交易的照 12 小時補抓")
def _():
    ex, clock = fresh()
    M = main_mod()
    calls = setup(M)
    M.mon_run_once()
    got = sorted(p.split("/")[2] for p in calls if "market_chart" in p)
    if "bbb" in got:
        return "幣安沒有合約的幣照樣每 12 小時補抓歷史"
    if "ccc" in got:
        return "已持倉的幣照樣每 12 小時補抓歷史"
    if "aaa" not in got:
        return f"能交易的幣歷史 20 小時沒補抓（應照 12 小時保鮮）：{got}"


@case("b-2", "幣安後來上架、部位平掉之後：下一輪就回到正常保鮮（照樣補抓）")
def _():
    ex, clock = fresh()
    M = main_mod()
    calls = setup(M)
    T()._filters["BBBUSDT"] = {"status": "TRADING"}             # 幣安上架了
    T().STATE["positions"].pop("CCCUSDT")                       # 部位平掉了
    M.mon_run_once()
    got = sorted(p.split("/")[2] for p in calls if "market_chart" in p)
    if got != ["aaa", "bbb", "ccc"]:
        return f"上架／平倉後沒有回到正常保鮮：只補抓了 {got}"


@case("b-3", "幣安合約清單取不到：一律當成有上架、不降頻（查不到不等於沒有）；1000 開頭的合約也認得")
def _():
    ex, clock = fresh()
    M = main_mod()
    need(hasattr(M, "low_priority"), "程式沒有低優先判斷（前提）")
    if M.low_priority("bbb", None, set()) is not None:
        return "合約清單取不到時被當成幣安沒有（會把能交易的幣降頻）"
    if M.low_priority("pepe", {"1000PEPEUSDT"}, set()) is not None:
        return "1000PEPEUSDT 沒被認成 PEPE 的合約"
    if M.low_priority("pepe", {"1000PEPEUSDT"}, {"1000PEPEUSDT"}) != "held":
        return "1000PEPEUSDT 持倉沒被認出來"


@case("b-4", "代理（本來就對，鎖住）：伺服器那份 2 小時前的 90 天歷史，模擬網或網頁來要時直接給、不打上游（記憶體 30 分鐘、磁碟 12 小時）")
def _():
    ex, clock = fresh()
    M = main_mod()
    calls = setup(M)
    key = "/api/v3/coins/aaa/market_chart?vs_currency=usd&days=90"
    age_cache(M, key, 2 * 3600)
    out = R55_proxy(M, key)
    if calls:
        return f"2 小時前的歷史，代理還是打了上游：{calls}"
    if out.get("code") != 200:
        return f"沒有直接給快取：{out}"


@case("b-5", "網頁背景掃描只讀快取：伺服器有（20 小時前的也給）就給；沒有就回「沒有」，不打上游")
def _():
    ex, clock = fresh()
    M = main_mod()
    calls = setup(M)
    hit = R55_proxy(M, "/api/v3/coins/bbb/market_chart?vs_currency=usd&days=90", cache_only=True)
    miss = R55_proxy(M, "/api/v3/coins/zzz/market_chart?vs_currency=usd&days=90", cache_only=True)
    if calls:
        return f"只讀快取的請求打了上游：{calls}"
    if hit.get("code") != 200:
        return f"伺服器有的沒給：{hit}"
    if miss.get("code") != 404 or miss.get("xcache") != "CACHE-ONLY-MISS":
        return f"伺服器沒有的應回 404（CACHE-ONLY-MISS）：{miss}"


@case("b-6", "上游錯誤講清楚：額度用完、公開端點擋 IP 分得出來（不是只寫「上游回應 403」）")
def _():
    ex, clock = fresh()
    M = main_mod()
    need(hasattr(M, "_upstream_error_text"), "程式沒有上游錯誤說明（前提）")
    body = json.dumps({"status": {"error_code": 10006, "error_message": "You have reached 10,000 calls limit."}}).encode()
    t1 = M._upstream_error_text(429, body)
    M.QUOTA["exhausted"] = True
    t2 = M._upstream_error_text(403, b"<html>blocked</html>")
    M.QUOTA["exhausted"] = False
    if "額度用完" not in t1:
        return f"10006 沒講是額度用完：{t1}"
    if "額度" not in t2 or "公開端點" not in t2:
        return f"額度用完後公開端點 403，沒講清楚：{t2}"


@case("b-7", "CoinGecko 用量按來源計數：監控、網頁、模擬網分得出來，只算真的打到 CoinGecko 的")
def _():
    ex, clock = fresh()
    M = main_mod()
    need(hasattr(M, "CG_CALLS") and hasattr(M, "cg_source"), "程式沒有用量計數（前提）")
    setup(M)
    real = M.fetch_upstream

    def counted(path, prefix="/api/v3", background=False):
        if prefix == "/api/v3":
            M._count_cg(path)
        return 200, CHART
    M.fetch_upstream = counted
    M.mon_run_once()
    R55_proxy(M, "/api/v3/coins/zzz/market_chart?vs_currency=usd&days=90")          # 網頁要一檔伺服器沒有的
    R55_proxy(M, "/api/v3/coins/aaa/market_chart?vs_currency=usd&days=90")          # 伺服器有的：不算
    M.fetch_upstream = real
    t = M.CG_CALLS["today"]
    if t.get("監控·history", 0) < 1:
        return f"監控補抓歷史沒算到：{t}"
    if t.get("網頁·history", 0) != 1:
        return f"網頁要的那一檔應算 1 次，實際 {t}"


@case("b-10", "行情榜存進磁碟：部署重啟後（記憶體清空）額度又用完，網頁照樣先拿到上一份行情，不打上游、不會變成示範資料")
def _():
    ex, clock = fresh()
    M = main_mod()
    calls = setup(M)
    key = "/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=1"
    M.cache_put(key, b'[{"id":"aaa"}]')
    cache_dir = M.CACHE_DIR
    M = main_mod()                                              # 重啟：記憶體快取清空
    M.CACHE_DIR = cache_dir
    calls = []
    M.fetch_upstream = lambda path, prefix="/api/v3", background=False: (calls.append(path) or (403, b"blocked"))
    M.revalidate_async = lambda *a, **k: None
    M.QUOTA["exhausted"] = True
    out = R55_proxy(M, key)
    if out.get("code") != 200 or out.get("body") != b'[{"id":"aaa"}]':
        return f"重啟後行情榜沒有先給上一份：{out}"
    if calls:
        return f"有上一份還同步打上游：{calls}"


@case("b-11", "額度用完、公開端點也被拒、又沒有快取可給：回「額度用完」（不是 403 被網頁當成金鑰錯）")
def _():
    ex, clock = fresh()
    M = main_mod()
    setup(M)
    M.fetch_upstream = lambda path, prefix="/api/v3", background=False: (403, b"<html>blocked</html>")
    M.QUOTA["exhausted"] = True
    out = R55_proxy(M, "/api/v3/coins/nothing-cached/market_chart?vs_currency=usd&days=90")
    if out.get("code") == 403:
        return "照樣回 403（網頁會顯示「API Key 被拒絕」，講錯原因）"
    if out.get("xcgerr") != "QUOTA":
        return f"沒有標明額度用完：{out}"


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✓' if not err else '✕'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
