"""BINANCE_LESSONS r24 → r26 逐段差異的檢查項目（crypto-screener 從 r24 回來，含 r25、r26）。

    python3 -m tests.test_r26
"""
import os
import sys
import importlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, ROOT)
import trader as T                                              # noqa: E402
import tests.fake_exchange as FX                                # noqa: E402
from tests.fake_exchange import Ex17, Clock, need, entry_sent   # noqa: E402

RESULTS = []
from tests.harness import make_case, selftest as harness_selftest  # noqa: E402
case = make_case(RESULTS)


def fresh(mode="oneway"):
    importlib.reload(T)
    clock = Clock()
    T.time = clock
    T.save_state = lambda: None
    T.CFG.update({"key": "k", "secret": "s", "dryRun": False, "leverage": 3, "riskPct": 0.5,
                  "breakevenR": 1.0, "maxPositions": 8})
    T._filters["XUSDT"] = {"status": "TRADING", "tick": 0.01, "step": 0.1, "minQty": 0.1, "minNotional": 5}
    T._filters_ts = 9e18
    T._algo_supported[0] = True
    ex = Ex17(mode)
    T._request_raw = ex
    return ex, clock


def open_long(ex, mark=100.4):
    ex.mark["XUSDT"] = mark
    n0 = len(ex.calls)
    r = T.open_position("X", "LONG", 100.0, None, stop_pct=5)
    need(entry_sent(ex, n0), f"進場單沒有送出：{r.get('error')}")
    return r


def _main():
    import main as M
    importlib.reload(M)
    M.trader = T
    pushed = []
    M.push_all = lambda title, text: pushed.append((title, text))   # 只攔最底層的送出，不 mock 通知本身
    return M, pushed


# ════ 第 2 條 r25、r26：守衛每個出口都回傳原因 ════════════════

@case("2-k", "守衛每個出口都回傳原因：查詢失敗／停損還在／還沒到第 3 輪／沒了")
def _():
    ex, clock = fresh()
    open_long(ex)
    p = T.STATE["positions"]["XUSDT"]
    need(any(v.get("type") == "STOP_MARKET" for v in ex.algo.values()), "停損沒有掛上（前提：第一個出口要測「還在」）")
    got = {"present": T._guard_one("XUSDT", p, [])}
    for o in list(ex.algo):
        if ex.algo[o].get("type") == "STOP_MARKET":
            ex.algo.pop(o)
    got["counting"] = T._guard_one("XUSDT", p, [])
    T._request_raw = lambda m, path, params, s, t: (500, {"msg": "x"}) if path == "/fapi/v1/openAlgoOrders" else ex(m, path, params, s, t)
    got["query_failed"] = T._guard_one("XUSDT", p, [])
    T._request_raw = ex
    T._guard_one("XUSDT", p, [])
    ex.pos[("XUSDT", "LONG")] = [0, 0]
    got["gone"] = T._guard_one("XUSDT", p, [])
    bad = {k: v for k, v in got.items() if v != k}
    if bad:
        return f"出口回傳不對（預期＝鍵）：{bad}"


@case("2-k2", "守衛、移損、掛停損、平倉的每個 return 帶值、最後不會掉出函式（語法樹檢查）")
def _():
    ex, clock = fresh()                                       # 全域檢查自成情境（r25）
    import subprocess
    out = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "check_returns.py")],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    need("組人造函式自我驗證通過" in out and "個函式" in out, f"檢查器沒有真的跑完（第 19 種）：{out[-200:]}")
    if "問題 0 項" not in out:
        return out.strip().splitlines()[-1]


# ════ 第 8 條 r26：通知的格式化要真的跑到 ════════════════════

@case("8-s", "平倉通知：不 mock 通知本身，餵缺欄位的紀錄，訊息要組得出來")
def _():
    ex, clock = fresh()
    M, pushed = _main()
    samples = [
        {"symbol": "XUSDT", "side": "LONG", "entry": 100.0, "exit": None, "pnl": None, "rMultiple": None,
         "opened": None, "closed": 1, "reason": None, "partials": [{"qty": None}]},
        {"symbol": None, "side": None, "entry": "壞", "exit": 99.0, "pnl": 1.5, "rMultiple": None,
         "opened": 1, "closed": None},
        {},
    ]
    for t in samples:
        try:
            head, text = M.notify_trade_close(t)
        except Exception as e:
            return f"缺欄位的紀錄組不出通知：{type(e).__name__}: {e}（資料 {t}）"
        need(head and text, "通知是空的")
    head, _ = M.notify_trade_close(samples[0])
    if "損益未知" not in head:
        return f"損益未知時標題寫成「{head}」"


@case("8-s2", "端到端：結帳時資料壞掉（損益記為未知），使用者照樣收到平倉通知，不是「通知出錯」")
def _():
    ex, clock = fresh()
    M, pushed = _main()
    open_long(ex)
    T.STATE["positions"]["XUSDT"]["entry"] = "壞掉的資料"        # 結帳算損益會失敗 → 損益記為未知
    ex.pos[("XUSDT", "LONG")] = [0, 0]
    M.position_round(20)
    need(any(t.get("symbol") == "XUSDT" for t in T.STATE["trades"]), "沒有結帳（前提）")
    titles = [t for t, _ in pushed]
    if not any("平倉" in t and "損益未知" in t for t in titles):
        return f"沒有收到平倉通知：{titles}"
    if any("平倉通知" in t and "出錯" in t for t in titles):
        return f"平倉通知本身出錯：{titles}"


@case("8-s3", "統計：損益未知的交易不算虧損，另外計數")
def _():
    ex, clock = fresh()
    T.STATE["trades"] = [{"pnl": 10.0, "rMultiple": 1.0}, {"pnl": -5.0, "rMultiple": -0.5},
                         {"pnl": None, "rMultiple": None}]
    p = T.performance()
    need(p.get("count") is not None, "沒有統計（前提）")
    if p.get("winRate") != 50.0 or p.get("unknown") != 1:
        return f"勝率 {p.get('winRate')}%、未知 {p.get('unknown')}，應為 50%、1 筆"


@case("8-t", "背景迴圈整輪出錯（不是某一步）：照節奏推播，不能只寫錯誤區")
def _():
    ex, clock = fresh()
    M, pushed = _main()
    M.position_round = lambda s: (_ for _ in ()).throw(RuntimeError("整輪壞了"))
    tick = getattr(M, "position_tick", None)
    need(tick is not None, "沒有可單獨執行的 position_tick")
    tick(20)
    if not any("整輪壞了" in x for _, x in pushed):
        return f"整輪出錯沒有推播：{pushed}"


# ════ 用法第 5 點 r25：命中次數算「被呼叫」 ═════════════════

@case("25-a", "突變命中：情境自己注入「逐幣回空」時，被突變的查詢被呼叫也要算命中")
def _():
    ex, clock = fresh()
    old = os.environ.get("MUTATE_SYMBOL_EMPTY")
    os.environ["MUTATE_SYMBOL_EMPTY"] = "1"
    try:
        FX.HITS.pop("25-a", None)
        ex.symbol_empty = 1                                   # 情境自己的注入先回
        ex("GET", "/fapi/v2/positionRisk", {"symbol": "XUSDT"}, True, 5)
        need(ex.inject_log, "情境的注入沒有觸發（前提）")
        if FX.HITS.get("25-a", 0) != 1:
            return f"命中 {FX.HITS.get('25-a', 0)} 次，應為 1（注入先回，突變分支沒執行，但查詢確實被呼叫了）"
    finally:
        FX.HITS.pop("25-a", None)                             # 這項測的是計數本身，自己的命中不算進突變檢查
        if old is None:
            os.environ.pop("MUTATE_SYMBOL_EMPTY", None)
        else:
            os.environ["MUTATE_SYMBOL_EMPTY"] = old


# ════ 測錯方式第 19 種：全域檢查本身也要有前提 ═══════════════

@case("19-a", "測試框架的錯誤輸出攔截真的生效（固定人造案例）")
def _():
    ex, clock = fresh()
    err, n = harness_selftest()
    need(n == 4, f"自我驗證只跑了 {n} 個人造案例（前提，第 19 種）")
    if err:
        return f"攔截沒生效：{err}"


@case("19-b", "注入綁定的檢查真的抓得到錯位（人造：注入打在第二次查詢）")
def _():
    ex, clock = fresh()
    from tests.fake_exchange import injected_at_step
    saved = os.environ.pop("MUTATE_SYMBOL_EMPTY", None)     # 這項測的是模擬交易所本身，不受程式突變影響
    n0 = len(ex.calls)
    ex("GET", "/fapi/v2/positionRisk", {"symbol": "XUSDT"}, True, 5)     # 被測那一步的第一次查詢（沒注入）
    ex.symbol_empty = 1
    ex("GET", "/fapi/v2/positionRisk", {"symbol": "XUSDT"}, True, 5)     # 注入打在第二次
    ok_, why_ = injected_at_step(ex, n0)
    if saved is not None:
        os.environ["MUTATE_SYMBOL_EMPTY"] = saved
    need(ex.inject_log, "注入沒有觸發（前提）")
    if ok_:
        return f"錯位沒被抓到：{why_}"


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✓' if not err else '✕'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
