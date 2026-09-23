"""CoinGecko 429 讓路與行情榜永不等上游（2026-09-23，模擬網重新整理 20～30 秒的修正）。

    python3 -m tests.test_cg_cooldown
"""
import io
import os
import sys
import time
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, ROOT)
from tests import test_r42 as R                                 # noqa: E402
from tests.fake_exchange import need                            # noqa: E402
from tests.harness import make_case                             # noqa: E402

RESULTS = []
case = make_case(RESULTS)
fresh, main_mod = R.fresh, R.main_mod


def fake_cg(M, status=200, body=b'[{"id":"bitcoin"}]'):
    """把 urlopen 換成假的 CoinGecko：記下每次呼叫；status 非 200 時丟 HTTPError。時間睡眠換成計數。"""
    calls = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return body

    def urlopen(req, timeout=0):
        calls.append(req.full_url)
        if status != 200:
            raise urllib.error.HTTPError(req.full_url, status, "x", {}, io.BytesIO(b'{"error":"rate"}'))
        return Resp()
    M.urllib.request.urlopen = urlopen
    slept = []
    M.time = type("T", (), {"__getattr__": lambda s, k: getattr(time, k)})()
    M.time.sleep = lambda s: slept.append(s)
    M.CFG["gap"] = 0.0
    M.CG_UPSTREAM = ""
    need(hasattr(M, "CG_COOL") and hasattr(M, "cg_cooling"), "程式沒有 CoinGecko 冷卻機制（前提：2026-09-23 起才有）")
    M.CG_COOL.update({"until": 0.0, "hits": 0})
    return calls, slept


def proxy(M, path):
    h = M.Handler.__new__(M.Handler)
    h.path = path
    out = {}

    def send_json(code, body, cached=False, stale=None, data_ts=None):
        out.update(code=code, body=body, stale=stale)
    h.send_json = send_json
    h.handle_proxy("/api/v3")
    need(out, "代理沒有回應（前提）")
    return out


@case("cg-1", "CoinGecko 回 429：進入冷卻；冷卻期間背景請求直接讓路，不打上游、不重試")
def _():
    ex, clock = fresh()
    M = main_mod()
    calls, slept = fake_cg(M, status=429)
    st, _b = M.fetch_upstream("/coins/x/market_chart?days=90", background=True)
    need(len(calls) == 1, f"第一次背景請求應打上游一次（前提），實際 {len(calls)}")
    if not M.cg_cooling():
        return "429 之後沒有進入冷卻"
    st2, _b2 = M.fetch_upstream("/coins/y/market_chart?days=90", background=True)
    if len(calls) != 1 or st2 != 429:
        return f"冷卻期間背景請求還打了上游（{len(calls)} 次）或沒回 429（{st2}）"
    if any(s >= 6 for s in slept):
        return f"背景請求遇到 429 還在原地退避重試：睡了 {slept}"


@case("cg-2", "冷卻期間前景請求（行情榜、監控）照常打上游，不受背景讓路影響")
def _():
    ex, clock = fresh()
    M = main_mod()
    calls, slept = fake_cg(M, status=200)
    M.CG_COOL["until"] = time.time() + 60
    need(M.cg_cooling(), "應在冷卻中（前提）")
    st, _b = M.fetch_upstream("/coins/markets?vs_currency=usd&page=1")
    if st != 200 or len(calls) != 1:
        return f"冷卻期間前景請求被擋了：{st}、打上游 {len(calls)} 次"


@case("cg-3", "冷卻到期：背景請求恢復打上游")
def _():
    ex, clock = fresh()
    M = main_mod()
    calls, slept = fake_cg(M, status=200)
    M.CG_COOL["until"] = time.time() - 1
    need(not M.cg_cooling(), "冷卻應已到期（前提）")
    st, _b = M.fetch_upstream("/coins/x/market_chart?days=90", background=True)
    if st != 200 or len(calls) != 1:
        return f"冷卻到期後背景請求沒有打上游：{st}、{len(calls)} 次"


@case("cg-4", "行情榜代理：快取過期超過寬限期也先送舊的、背景更新，畫面不等上游")
def _():
    ex, clock = fresh()
    M = main_mod()
    calls, slept = fake_cg(M, status=200)
    M.revalidate_async = lambda *a, **k: calls.append("revalidate")
    key = "/api/v3/coins/markets?vs_currency=usd&page=1"
    M.cache_put(key, b'[{"id":"old"}]')
    with M._cache_lock:
        M._cache[key] = (time.time() - M.CACHE_TTL - M.STALE_GRACE - 3600, b'[{"id":"old"}]')   # 過期超過寬限一小時
    need(M.cache_get(key, M.CACHE_TTL) is None, "快取應已過期（前提）")
    out = proxy(M, key)
    if out["body"] != b'[{"id":"old"}]' or out.get("stale") is None:
        return f"行情榜沒有先送舊資料：{out}"
    if "revalidate" not in calls:
        return "沒有排背景更新"
    if any(c != "revalidate" for c in calls):
        return "畫面請求同步打了上游"


@case("cg-5", "深度資料代理（對照）：過期超過寬限期照舊同步打上游；冷卻期間則讓路回 429、不打上游")
def _():
    ex, clock = fresh()
    M = main_mod()
    calls, slept = fake_cg(M, status=200, body=b'{"prices":[]}')
    key = "/api/v3/coins/x/market_chart?vs_currency=usd&days=90"
    with M._cache_lock:
        M._cache[key] = (time.time() - 100 * 3600, b'{"prices":[]}')
    out = proxy(M, key)
    need(out["code"] == 200 and len(calls) == 1, f"深度資料過期應同步打上游（前提）：{out['code']}、{len(calls)} 次")
    with M._cache_lock:
        M._cache[key] = (time.time() - 100 * 3600, b'{"prices":[]}')   # 再讓它過期一次（記憶體與磁碟都要）
    if os.path.exists(M._disk_path(key)):
        os.remove(M._disk_path(key))
    need(M.cache_get(key, M.ttl_for(key)) is None, "快取應已過期（前提）")
    M.CG_COOL["until"] = time.time() + 60
    out2 = proxy(M, key)
    if out2["code"] != 429 or len(calls) != 1:
        return f"冷卻期間深掃還打了上游：{out2['code']}、{len(calls)} 次"


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✓' if not err else '✕'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
