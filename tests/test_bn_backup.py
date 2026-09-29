"""幣安備援資料（2026-09-29）：90 天基準量改用幣安合約小時 K 線，平常影子比對、CoinGecko 拿不到時接手。

- 換算：幣安成交量只是全市場的一部分（這裡假設固定 30%），換算後量能倍數、分數、門檻要跟 CoinGecko 算的一樣。
- 影子比對：每輪最多 N 檔、12 小時內不重比、存檔讀得回來。
- 接手：CoinGecko 額度用完時，幣安有合約的幣用幣安算、標資料來源；幣安沒有的略過；不打 CoinGecko。
- 把關：備援算出的訊號，比對沒達標只通知不下單；達標才往下走。
- 行情榜也拿不到：沿用 6 小時內的上一份，價格與成交量換成幣安即時；沒有換算比例的幣略過。

    python3 -m tests.test_bn_backup
"""
import json
import math
import os
import random
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
SHARE = 0.30                                                    # 幣安佔全市場成交量


def T():
    return R.T


def make_series(seed, hours=91 * 24 + 30, burst=True, drift=0.0):
    """產生一段小時資料：收盤價、每小時成交額（USDT，幣安）。最後 24 小時放量＋上漲（burst）。"""
    rnd = random.Random(seed)
    now_h = int(time.time() // 3600)
    p, out = 1.0, []
    for i in range(hours):
        last = i >= hours - 24
        p *= math.exp(rnd.gauss(drift + (0.004 if (burst and last) else 0.0), 0.01))
        qv = 1000.0 * math.exp(rnd.gauss(0, 0.3)) * (6.0 if (burst and last) else 1.0)
        open_ms = (now_h - hours + 1 + i) * 3600000
        out.append((open_ms, p, qv))
    return out


def klines(series, mult=1.0):
    return [[o, str(p * mult), str(p * mult), str(p * mult), str(p * mult), "0", o + 3599999, str(qv), 0, "0", "0", "0"]
            for o, p, qv in series]


def cg_chart(series):
    """同一段資料換成 CoinGecko market_chart：每小時收盤、滾動 24 小時全市場成交額（幣安 ÷ 佔比）。"""
    prices, vols = [], []
    for i in range(23, len(series)):
        t = series[i][0] + 3600000
        prices.append([t, series[i][1]])
        vols.append([t, sum(x[2] for x in series[i - 23:i + 1]) / SHARE])
    return {"prices": prices[-90 * 24:], "total_volumes": vols[-90 * 24:]}


def install(M, series_by_sym, cg_fail=False):
    """假的上游：/api/bn 回 K 線與 24 小時統計；/api/v3 回 CoinGecko（cg_fail 時回 403）。回傳呼叫紀錄。"""
    calls = []

    def fake(path, prefix="/api/v3", background=False):
        calls.append((prefix, path))
        if prefix == "/api/bn":
            if path.startswith("/fapi/v1/ticker/24hr"):
                return 200, json.dumps([{"symbol": s, "lastPrice": str(sr[-1][1] * (1000 if s.startswith("1000") else 1)),
                                         "quoteVolume": str(sum(x[2] for x in sr[-24:])), "priceChangePercent": "3.0"}
                                        for s, sr in series_by_sym.items()]).encode()
            q = dict(kv.split("=") for kv in path.split("?")[1].split("&"))
            sr = series_by_sym.get(q["symbol"])
            if sr is None:
                return 400, b'{"code":-1121,"msg":"Invalid symbol."}'
            end, lim = int(q["endTime"]), int(q["limit"])
            rows = [k for k in klines(sr, 1000 if q["symbol"].startswith("1000") else 1) if k[0] <= end][-lim:]
            return 200, json.dumps(rows).encode()
        if cg_fail:
            return 403, b"<html>blocked</html>"
        cid = path.split("/")[2]
        sym = cid.upper() + "USDT"
        sr = series_by_sym.get(sym) or series_by_sym.get("1000" + sym)
        return 200, json.dumps(cg_chart(sr)).encode()
    M.fetch_upstream = fake
    return calls


def mon_setup(M, syms, listed=None):
    need(hasattr(M, "bn_chart") and hasattr(M, "bn_shadow_summary"), "程式沒有幣安備援（前提：2026-09-29 起才有）")
    M.CACHE_DIR = tempfile.mkdtemp()
    M.trader = T()
    T()._filters.update({s + "USDT": {"status": "TRADING"} for s in (listed if listed is not None else syms)})
    T()._filters_ts = 9e18
    series = {s + "USDT": make_series(i + 1) for i, s in enumerate(syms)}
    M.MON.update({"cfg": {"bull": {"minRvol": 2, "minRadar": 65, "minLiq": 0, "maxChase": 100, "maxStopPct": 25},
                          "bear": {"minBear": 65, "minStruct": 0, "maxVolRatio": 99, "minLiq": 0, "maxStopPct": 25},
                          "cooldownMin": 60, "minDelta": 5, "breakoutPct": 3},
                  "scope": "top", "topN": 10, "histTTL": 12, "maxRefresh": 8})
    markets = [{"id": s.lower(), "symbol": s.lower(), "name": s, "market_cap": 5e7,
                "current_price": series[s + "USDT"][-1][1],
                "total_volume": sum(x[2] for x in series[s + "USDT"][-24:]) / SHARE,
                "price_change_percentage_24h_in_currency": 3.0,
                "price_change_percentage_7d_in_currency": 5.0, "price_change_percentage_30d_in_currency": 8.0}
               for s in syms]
    M.mon_fetch_json = lambda path: markets
    M.push_all = lambda *a, **k: None
    return series, markets


# ── 換算 ─────────────────────────────────────────────────────────────

@case("bn-1", "幣安 K 線轉成的圖表：每小時收盤、滾動 24 小時成交額；1000 開頭的合約價格除回原單位")
def _():
    ex, clock = fresh()
    M = main_mod()
    need(hasattr(M, "bn_chart"), "程式沒有幣安備援（前提）")
    M.CACHE_DIR = tempfile.mkdtemp()
    sr = make_series(7)
    install(M, {"1000PEPEUSDT": sr})
    ch = M.bn_chart("1000PEPEUSDT")
    need(ch, "拿不到圖表（前提）")
    if len(ch["prices"]) != 90 * 24:
        return f"應有 90 天 × 24 小時的點，實際 {len(ch['prices'])}"
    if abs(ch["prices"][-1][1] - sr[-1][1]) > 1e-9:
        return f"1000 開頭的價格沒除回原單位：{ch['prices'][-1][1]}（應為 {sr[-1][1]}）"
    want = sum(x[2] for x in sr[-24:])
    if abs(ch["total_volumes"][-1][1] - want) > 1e-6:
        return f"最後一點的滾動 24 小時成交額 {ch['total_volumes'][-1][1]}，應為 {want}"


@case("bn-2", "換算一致性：幣安成交量固定佔全市場 30% 時，量能倍數、雷達分數、多空門檻跟 CoinGecko 算的一樣")
def _():
    ex, clock = fresh()
    M = main_mod()
    series, markets = mon_setup(M, ["AAA"])
    install(M, series)
    c = markets[0]
    base = {"id": "aaa", "sym": "AAA", "name": "AAA", "price": c["current_price"], "vol": c["total_volume"], "mcap": c["market_cap"],
            "m24": 3.0, "m7": 5.0, "m30": 8.0}
    base["turn"] = base["vol"] / base["mcap"] * 100
    cg = M.engine.analyze(base, M.engine.extract_scan(cg_chart(series["AAAUSDT"]), base["vol"]))
    ch = M.bn_chart("AAAUSDT")
    need(ch and cg.get("scanned"), "CoinGecko 這邊沒算出來（前提）")
    bn = M.engine.analyze(base, M.bn_scan(base["vol"], ch))
    need(cg.get("rvol7") and cg["rvol7"] > 2, f"情境應是放量（前提）：{cg.get('rvol7')}")
    if abs(bn["rvol7"] / cg["rvol7"] - 1) > 0.01:
        return f"量能倍數不一致：幣安 {bn['rvol7']:.3f}、CoinGecko {cg['rvol7']:.3f}（換算錯了）"
    if abs((bn.get("radar") or 0) - (cg.get("radar") or 0)) > 0.5:
        return f"雷達分數不一致：幣安 {bn.get('radar')}、CoinGecko {cg.get('radar')}"
    if M._gates(bn) != M._gates(cg):
        return f"多空門檻判斷不一致：幣安 {M._gates(bn)}、CoinGecko {M._gates(cg)}"


@case("bn-3", "對照：不做換算（直接拿幣安自家成交量當全市場）量能倍數就會錯——證明換算那一步是必要的")
def _():
    ex, clock = fresh()
    M = main_mod()
    series, markets = mon_setup(M, ["AAA"])
    install(M, series)
    c = markets[0]
    ch = M.bn_chart("AAAUSDT")
    need(ch, "拿不到圖表（前提）")
    base = {"id": "aaa", "sym": "AAA", "name": "AAA", "price": c["current_price"], "vol": c["total_volume"], "mcap": c["market_cap"]}
    raw = M.engine.analyze(base, M.engine.extract_scan(ch, ch["total_volumes"][-1][1]))
    cg = M.engine.analyze(base, M.engine.extract_scan(cg_chart(series["AAAUSDT"]), base["vol"]))
    if abs(raw["rvol7"] / cg["rvol7"] - 1) < 0.5:
        return f"沒換算也一樣？這個對照組證明不了換算必要：{raw['rvol7']:.2f} vs {cg['rvol7']:.2f}"


# ── 影子比對 ──────────────────────────────────────────────────────────

@case("bn-4", "影子比對：CoinGecko 正常時每輪最多比 N 檔、12 小時內不重比、樣本存檔重啟讀得回來；不動用備援、不改訊號")
def _():
    ex, clock = fresh()
    M = main_mod()
    syms = ["AAA", "BBB", "CCC", "DDD"]
    series, markets = mon_setup(M, syms)
    install(M, series)
    M.BN_SHADOW_PER_ROUND = 3
    M.mon_run_once()
    s1 = M.bn_shadow_summary()
    M.mon_run_once()
    s2 = M.bn_shadow_summary()
    if s1["n"] != 3:
        return f"第一輪應比 3 檔，實際 {s1['n']}"
    if s2["n"] != 4:
        return f"第二輪只該補比剩下 1 檔（12 小時內不重比），實際累計 {s2['n']}"
    if s2["gateAgree"] != 1.0 or (s2["rvolMedDiff"] or 0) > 0.01:
        return f"同一份資料比對應完全一致：{s2}"
    cache_dir = M.CACHE_DIR
    M = main_mod()
    M.CACHE_DIR = cache_dir
    M.bn_shadow_load()
    if M.bn_shadow_summary()["n"] != 4:
        return "重啟後比對樣本不見了"


# ── 接手 ─────────────────────────────────────────────────────────────

@case("bn-5", "CoinGecko 額度用完：幣安有合約的幣用幣安 K 線接手（標資料來源）、幣安沒有的略過；不打 CoinGecko 歷史")
def _():
    ex, clock = fresh()
    M = main_mod()
    series, markets = mon_setup(M, ["AAA", "BBB"], listed=["AAA"])
    calls = install(M, series, cg_fail=True)
    M.QUOTA["exhausted"] = True
    got = {}
    real_eval = M.engine.evaluate
    M.engine.evaluate = lambda rows, cfg, states, now: (got.update({r["id"]: r for r in rows}) or real_eval(rows, cfg, states, now))
    M.mon_run_once()
    M.QUOTA["exhausted"] = False
    if [p for pre, p in calls if pre == "/api/v3" and "market_chart" in p]:
        return "額度用完還去打 CoinGecko 歷史"
    if "aaa" not in got or got["aaa"].get("dataSource") != "binance":
        return f"幣安有合約的幣沒被接手：{list(got)}"
    if "bbb" in got:
        return "幣安沒有合約的幣也算出來了（用什麼算的？）"


@case("bn-6", "備援資料算出的訊號：比對沒達標只通知不下單；達標才往下走（不被這一關擋）")
def _():
    ex, clock = fresh()
    M = main_mod()
    need(hasattr(M, "bn_shadow_summary"), "程式沒有幣安備援（前提）")
    T().AUTO["on"] = True
    ev = {"side": "bull", "score": 90, "id": "aaa", "sym": "AAA"}
    row = {"id": "aaa", "sym": "AAA", "radar": 90, "dataSource": "binance"}
    M.BN_SHADOW["samples"] = []
    out1 = M.auto_try_trade(ev, row)
    if out1.get("stage") != "gate" or "備援" not in out1.get("why", ""):
        return f"樣本不足時應擋下、講明是備援資料：{out1}"
    M.BN_SHADOW["samples"] = [{"sym": "X", "rvCG": 3.0, "rvBN": 3.1, "scCG": 80, "scBN": 81, "stCG": "accel", "stBN": "accel",
                               "gCG": True, "gBN": True, "bCG": False, "bBN": False}] * 80
    need(M.bn_shadow_summary()["ok"], "樣本 80 筆、全部一致應達標（前提）")
    out2 = M.auto_try_trade(ev, row)
    if "備援" in str(out2.get("why")):
        return f"達標了還被備援這一關擋：{out2}"


@case("bn-7", "行情榜也拿不到：沿用 6 小時內的上一份，價格與成交量換成幣安即時；沒有換算比例的幣略過；超過 6 小時照樣報錯")
def _():
    ex, clock = fresh()
    M = main_mod()
    series, markets = mon_setup(M, ["AAA", "BBB"])
    install(M, series)
    path = "/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=10&page=1&sparkline=false&price_change_percentage=24h,7d,30d"
    M.cache_put("/api/v3" + path, json.dumps(markets).encode())
    M.BN_SHADOW["ratio"] = {"AAA": 1 / SHARE}                   # 只有 AAA 比對過、有換算比例

    def boom(p):
        raise RuntimeError("CoinGecko 每月額度用完")
    M.mon_fetch_json = boom
    mk, stale = M.mon_markets(path)
    if not stale or [c["id"] for c in mk] != ["aaa"]:
        return f"應沿用上一份、只留有換算比例的 AAA：{stale}、{[c['id'] for c in mk]}"
    want_vol = sum(x[2] for x in series["AAAUSDT"][-24:]) / SHARE
    if abs(mk[0]["total_volume"] / want_vol - 1) > 0.01:
        return f"成交量沒換成幣安即時×換算比例：{mk[0]['total_volume']} vs {want_vol}"
    with M._cache_lock:
        M._cache["/api/v3" + path] = (time.time() - 7 * 3600, M._cache["/api/v3" + path][1])
    p = M._disk_path("/api/v3" + path)
    if os.path.exists(p):
        os.remove(p)
    try:
        M.mon_markets(path)
        return "上一份超過 6 小時還拿來用"
    except RuntimeError:
        pass


@case("bn-8", "CoinGecko 正常時：歷史照舊用 CoinGecko，資料來源不會被標成幣安（影子比對不改訊號）")
def _():
    ex, clock = fresh()
    M = main_mod()
    series, markets = mon_setup(M, ["AAA"])
    install(M, series)
    got = {}
    real_eval = M.engine.evaluate
    M.engine.evaluate = lambda rows, cfg, states, now: (got.update({r["id"]: r for r in rows}) or real_eval(rows, cfg, states, now))
    M.mon_run_once()
    need("aaa" in got, "這輪沒算出 AAA（前提）")
    if got["aaa"].get("dataSource") == "binance":
        return "CoinGecko 正常卻標成幣安資料（會被下單把關擋掉）"


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✓' if not err else '✕'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
