"""清單 r88 → r97：第 6 條「公開行情端點一樣算 IP 權重」。

模擬幣安在 HTTP 層（urlopen）攔截，照真的幣安算 K 線權重：limit <100→1、<500→2、≤1000→5、>1000→10，
每分鐘歸零；超過上限回 429（帶 Retry-After 到下一分鐘）；429 冷卻中還打 → 418 封 IP 120 秒；每個回應帶 X-MBX-USED-WEIGHT-1M。
時間用可控時鐘（sleep 讓時間往前走），fetch_upstream／bn_chart 的程式真的跑過。

    python3 -m tests.test_r97
"""
import io
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, ROOT)
from tests import test_r42 as R                                 # noqa: E402
from tests.fake_exchange import Clock, need                     # noqa: E402
from tests.harness import make_case                             # noqa: E402

RESULTS = []
case = make_case(RESULTS)
fresh, main_mod = R.fresh, R.main_mod
MIN0 = 30_000_000 * 60.0
H = 3_600_000


class FakeBN:
    """幣安合約公開端點（K 線、24 小時統計），照 IP 權重回 429／418。其他上游（CoinGecko 等）一律 403。"""

    def __init__(self, clock, bars=None):
        self.clock, self.cap, self.used, self.minute = clock, 2400, 0, None
        self.cool, self.ban = 0.0, 0.0
        self.calls = []                       # dict(t, path, status, used)
        self.bars = bars or {}                # 合約 → 有幾根小時 K（沒列 = 3000 根）
        self.force = {}                       # 合約 → 固定回的狀態碼（不計權重）

    @staticmethod
    def weight(limit):
        limit = int(limit)
        return 1 if limit < 100 else 2 if limit < 500 else 5 if limit <= 1000 else 10

    def _err(self, url, code, body, extra=None):
        h = {"X-MBX-USED-WEIGHT-1M": str(self.used)}
        h.update(extra or {})
        raise urllib.error.HTTPError(url, code, "x", h, io.BytesIO(body))

    def urlopen(self, req, timeout=0):
        url = req.full_url
        if "fapi.binance.com" not in url:
            raise urllib.error.HTTPError(url, 403, "x", {}, io.BytesIO(b"blocked"))
        u = urllib.parse.urlsplit(url)
        path, q = u.path, dict(urllib.parse.parse_qsl(u.query))
        now = self.clock.time()
        m = int(now // 60)
        if self.minute != m:
            self.minute, self.used = m, 0
        rec = dict(t=now, path=path, symbol=q.get("symbol"), status=None)
        self.calls.append(rec)
        if now < self.ban:
            rec["status"] = 418
            self._err(url, 418, b'{"code":-1003,"msg":"IP banned"}', {"Retry-After": str(int(self.ban - now) + 1)})
        if now < self.cool:
            self.ban = now + 120
            rec["status"] = 418
            self._err(url, 418, b'{"code":-1003,"msg":"IP banned"}', {"Retry-After": "120"})
        if q.get("symbol") in self.force:
            rec["status"] = self.force[q["symbol"]]
            self._err(url, rec["status"], b'{"code":-1000,"msg":"x"}')
        self.used += self.weight(q.get("limit", 500)) if path == "/fapi/v1/klines" else 1
        rec["used"] = self.used
        if self.used > self.cap:
            self.cool = (m + 1) * 60
            rec["status"] = 429
            self._err(url, 429, b'{"code":-1003,"msg":"Too many requests"}', {"Retry-After": str(int(self.cool - now) + 1)})
        rec["status"] = 200
        if path == "/fapi/v1/klines":
            n = self.bars.get(q["symbol"], 3000)
            last = int(now * 1000) // H * H - H
            opens = [last - i * H for i in range(n)][::-1]
            lim = int(q.get("limit", 500))
            if "startTime" in q:
                opens = [t for t in opens if t >= int(q["startTime"])][:lim]
            else:
                opens = [t for t in opens if t <= int(q.get("endTime", 9e15))][-lim:]
            body = json.dumps([[t, "1", "1", "1", "1", "0", t + H - 1, "100", 0, "0", "0", "0"] for t in opens]).encode()
        elif path == "/fapi/v1/ticker/24hr":
            body = b"[]"
        else:
            body = b"{}"
        hdr = {"X-MBX-USED-WEIGHT-1M": str(self.used)}

        class Resp:
            headers = hdr

            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False

            def read(s):
                return body
        return Resp()


def setup(M, **kw):
    need(hasattr(M, "bn_chart"), "程式沒有幣安備援（前提：2026-09-29 起才有）")
    clock = Clock()
    clock.now = MIN0 + 1.0
    M.time = clock
    M.CACHE_DIR = tempfile.mkdtemp()
    fx = FakeBN(clock, **kw)
    M.urllib.request.urlopen = fx.urlopen
    return fx, clock


def kl(fx):
    return [c for c in fx.calls if c["path"] == "/fapi/v1/klines"]


def sts(fx):
    return [c["status"] for c in fx.calls]


@case("97-1", "同一 IP 這一分鐘已用 1000（trader、別的請求）＋監控備援連抓 150 檔 90 天小時 K：要讓路，不能打到 429／418，資料照樣完整")
def _():
    ex, clock0 = fresh()
    M = main_mod()
    fx, clock = setup(M)
    fx.minute, fx.used = int(MIN0 // 60), 1000
    got = [M.bn_chart(f"C{i}USDT") for i in range(150)]
    need(len(kl(fx)) >= 150, f"（前提）K 線請求真的送了：{len(kl(fx))} 個")
    total = 1000 + sum(FakeBN.weight(1500 if i % 2 == 0 else 700) for i in range(len(kl(fx))))
    need(total > 2400, "（對照組）總權重確實超過一分鐘上限——不讓路就一定會撞到")
    if any(s in (429, 418) for s in sts(fx)):
        return f"打到限流：{[s for s in sts(fx) if s != 200][:5]}"
    if not all(g and len(g["prices"]) == 90 * 24 for g in got):
        return f"有 {sum(1 for g in got if not g)} 檔沒抓到完整的 90 天"
    over = [c["used"] for c in kl(fx) if c["used"] > 1200 and c["t"] >= MIN0 + 60]
    if over:
        return f"讓路之後的分鐘裡，已用權重還超過一半：{over[:5]}"
    if clock.now - MIN0 < 60:
        return "沒有等到下一分鐘"


@case("97-2", "429：照 Retry-After 等到下一分鐘重試、資料完整；冷卻中沒有再打（不會升級成 418）")
def _():
    ex, clock0 = fresh()
    M = main_mod()
    fx, clock = setup(M)
    fx.minute, fx.used = int(MIN0 // 60), 2395       # 別人把這一分鐘幾乎用完；程式這邊還不知道
    g = M.bn_chart("AUSDT")
    need(sts(fx)[:1] == [429], f"（前提）第一個請求真的被 429：{sts(fx)}")
    if not g or len(g["prices"]) != 90 * 24:
        return "等完重試後沒有拿到完整資料"
    if 418 in sts(fx):
        return f"冷卻中又打、升級成 418：{sts(fx)}"
    if len(fx.calls) < 2 or fx.calls[1]["t"] < MIN0 + 60:
        return f"重試沒等到 Retry-After：{[round(c['t'] - MIN0, 1) for c in fx.calls]}"


@case("97-3", "一直被 429：重試 3 次（每次照 Retry-After、最多 120 秒）後放棄，回「限流」——跟「沒有資料」分得開")
def _():
    ex, clock0 = fresh()
    M = main_mod()
    fx, clock = setup(M)
    fx.cap = 0
    need(hasattr(M, "bn_chart_ex"), "（前提）有 bn_chart_ex（拿不到時回原因）")
    ch, why = M.bn_chart_ex("AUSDT")
    need(sts(fx)[:1] == [429], f"（前提）真的被 429：{sts(fx)}")
    if ch is not None or why != "limited":
        return f"應回 (None, limited)，實際 ({bool(ch)}, {why})"
    if len(kl(fx)) != 4:
        return f"應送 4 次（第一次＋重試 3 次），實際 {len(kl(fx))}"
    if 418 in sts(fx):
        return f"冷卻中又打、升級成 418：{sts(fx)}"
    fx2 = FakeBN(clock, bars={"NEWUSDT": 10})
    M.urllib.request.urlopen = fx2.urlopen
    M.BN_RL.update(cool=0.0, limitedAt=0.0)
    ch2, why2 = M.bn_chart_ex("NEWUSDT")
    if ch2 is not None or why2 != "nodata":
        return f"（對照組）真的沒歷史（剛上架）應回 nodata，實際 {why2}"


@case("97-4", "418：不重試、立刻回；封鎖期間任何 /api/bn 都不送（K 線、24 小時統計）")
def _():
    ex, clock0 = fresh()
    M = main_mod()
    fx, clock = setup(M)
    fx.ban = MIN0 + 600
    st, _b = M.fetch_upstream("/fapi/v1/klines?symbol=AUSDT&interval=1h&limit=1500&endTime=1", "/api/bn")
    need(sts(fx) == [418], f"（前提）第一個請求被 418：{sts(fx)}")
    if st != 418:
        return f"應回 418，實際 {st}"
    st2, _ = M.fetch_upstream("/fapi/v1/klines?symbol=BUSDT&interval=1h&limit=80&startTime=1", "/api/bn")
    st3, _ = M.fetch_upstream("/fapi/v1/ticker/24hr", "/api/bn")
    if len(fx.calls) != 1:
        return f"封鎖期間還送了 {len(fx.calls) - 1} 個請求"
    if st2 != 418 or st3 != 418:
        return f"封鎖期間應直接回 418：{st2}、{st3}"
    if clock.now - MIN0 > 2:
        return f"為了重試去等了 {clock.now - MIN0:.0f} 秒"


@case("97-5", "CoinGecko 拿不到、監控改用幣安備援，第一檔就一直被限流：剩下的這輪直接讓開（不一檔一檔重試），依原因記數")
def _():
    ex, clock0 = fresh()
    M = main_mod()
    syms = ["AAA", "BBB", "CCC", "DDD"]
    fx, clock = setup(M)
    M.trader = R.T
    R.T._filters.update({s + "USDT": {"status": "TRADING"} for s in syms})
    R.T._filters_ts = 9e18
    M.MON.update({"cfg": {"bull": {}, "bear": {}}, "scope": "top", "topN": 10, "histTTL": 12, "maxRefresh": 8})
    markets = [{"id": s.lower(), "symbol": s.lower(), "name": s, "market_cap": 5e7, "current_price": 1.0, "total_volume": 1e6,
                "price_change_percentage_24h_in_currency": 3.0, "price_change_percentage_7d_in_currency": 5.0,
                "price_change_percentage_30d_in_currency": 8.0} for s in syms]
    M.mon_fetch_json = lambda path: markets
    M.push_all = lambda *a, **k: None
    fx.cap = 0
    M.QUOTA["exhausted"] = True
    M.engine.evaluate = lambda rows, cfg, states, now: ([], states)
    M.mon_run_once()
    M.QUOTA["exhausted"] = False
    need(kl(fx), f"（前提）監控真的去抓幣安 K 線了：{len(fx.calls)} 個請求")
    skip = M.MON.get("bnSkip") or {}
    if skip.get("limited") != len(syms):
        return f"這輪 {len(syms)} 檔都該記成「限流」，實際 {skip}"
    if len(kl(fx)) != 4:
        return f"第一檔放棄後還在打：K 線請求 {len(kl(fx))} 個（應只有第一檔的 4 個）"
    st = M.mon_handle("/api/monitor/status", {})[1]
    if not (st.get("bnSkip") or {}).get("limited") or "bnRate" not in st:
        return f"狀態端點沒有帶出備援略過與權重：{ {k: st.get(k) for k in ('bnSkip', 'bnRate')} }"


@case("97-6", "自檢多一項「IP 權重（K 線）」：平常 ok、寫出已用權重；被封鎖時 fail")
def _():
    ex, clock0 = fresh()
    M = main_mod()
    fx, clock = setup(M)
    need(M.preflight is not None, "（前提）有自檢模組")
    out = []
    for fn in M.preflight.EXTRA:
        fn(lambda item, st, msg="", lesson=None: out.append((item, st, msg)))
    it = [o for o in out if "IP 權重" in o[0]]
    if len(it) != 1 or it[0][1] != "ok" or "已用" not in it[0][2]:
        return f"平常應有一項 ok：{it}"
    fx.ban = clock.now + 600
    M.BN_RL["ban"] = 0.0
    out.clear()
    for fn in M.preflight.EXTRA:
        fn(lambda item, st, msg="", lesson=None: out.append((item, st, msg)))
    it = [o for o in out if "IP 權重" in o[0]]
    if len(it) != 1 or it[0][1] != "fail":
        return f"被封鎖時應是 fail：{it}"


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✓' if not err else '✕'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
