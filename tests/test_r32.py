"""BINANCE_LESSONS r30 → r32 逐段差異的檢查項目（crypto-screener 從 r30 回來，含 r31、r32）。

    python3 -m tests.test_r32
"""
import os
import sys
import importlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, ROOT)
import trader as T                                              # noqa: E402
from tests.fake_exchange import Ex17, Clock, need, entry_sent, titled  # noqa: E402

RESULTS = []
from tests.harness import make_case                             # noqa: E402
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


# ════ 第 8 條 r31：平倉成交用方向、界線用成交 id ═══════════════

@case("8-y", "打平出場（成交價＝進場價，realizedPnl 剛好 0）：損益是已知的 0，不能被當成「不是平倉」而變未知")
def _():
    ex, clock = fresh()
    r = open_long(ex)
    p = T.STATE["positions"]["XUSDT"]
    entry = p["entry"]
    ex.trigger("XUSDT", "LONG", r["qty"], entry)              # 在成本價被停損
    need(ex.fills, "要取的清單是空的（前提，r35：先確認有東西再索引）")
    need(float(ex.fills[-1]["realizedPnl"]) == 0.0, "模擬的平倉成交 realizedPnl 不是 0（前提）")
    T.sync_positions()
    need(T.STATE["trades"], "要取的清單是空的（前提，r35：先確認有東西再索引）")
    t = T.STATE["trades"][-1]
    need(t.get("symbol") == "XUSDT", "沒有結帳（前提）")
    if t.get("pnl") is None or abs(t["pnl"]) > 1e-9:
        return f"打平出場的損益應為已知的 0，實際 {t.get('pnl')}（出場價 {t.get('exit')}）"


@case("8-z", "同一毫秒內兩筆平倉成交（停利與停損）：界線用成交 id，第二筆不能被跳過")
def _():
    ex, clock = fresh()
    r = open_long(ex)
    ex.same_ms = True                                         # 之後的成交都落在同一毫秒
    p = T.STATE["positions"]["XUSDT"]
    tp = next(o for o in p["orders"] if o["type"] == "TAKE_PROFIT_MARKET")
    half = float(ex.algo[tp["id"]]["quantity"])
    ex.algo.pop(tp["id"])
    ex.trigger("XUSDT", "LONG", half, 110.0)
    T.sync_positions()
    part = (T.STATE["positions"]["XUSDT"].get("partials") or [None])[-1]
    need(part and part.get("px") == 110.0, f"部分出場沒有讀到成交價（前提）：{part}")
    ex.trigger("XUSDT", "LONG", r["qty"] - half, 101.0)        # 同一毫秒
    need(len(ex.fills) >= 2, "要取的清單是空的（前提，r35：先確認有東西再索引）")
    need(ex.fills[-1]["time"] == ex.fills[-2]["time"], "兩筆成交不在同一毫秒（前提）")
    T.sync_positions()
    need(T.STATE["trades"], "要取的清單是空的（前提，r35：先確認有東西再索引）")
    t = T.STATE["trades"][-1]
    need(t.get("symbol") == "XUSDT", "沒有結帳（前提）")
    if t.get("exit") is None or abs(float(t["exit"]) - 101.0) > 1e-9:
        return f"出場價 {t.get('exit')}，應為 101.0（界線用時間時，同一毫秒的第二筆會被跳過或重算）"


# ════ r31 pump-dump-hunter 的同類問題：pending 時間戳缺值 ════════

@case("8-aa", "pending 沒有時間戳：不能永遠卡著（不到期、佔名額、擋同幣），要走確認後判定")
def _():
    ex, clock = fresh()
    T.STATE["pending"]["XUSDT"] = {"side": "LONG", "qty": 1.0, "stopPct": 5, "note": "", "base": 0.0,
                                   "params": {k: T.CFG.get(k) for k in T.PARAM_KEYS}}      # 沒有 ts
    n0 = len(ex.calls)
    T.sync_positions()
    need(any(c[1] == "/fapi/v2/positionRisk" for c in ex.calls[n0:]), "對帳沒有跑（前提）")
    if "XUSDT" in T.STATE["pending"]:
        return "沒有時間戳的 pending 永遠不會到期，這個幣永遠不能再下單"
    if len(titled(T.drain_alerts(), "送出的進場單判定未成交")) != 1:
        return "判定未成交時沒有通知"


# ════ 用法第 5 點 r32：金絲雀、改寫工具 ═══════════════════════

@case("32-a", "stderr 攔截的金絲雀：背景執行緒的例外，用另一個緩衝區確認真的攔得到", infra=True)
def _():
    ex, clock = fresh()
    from tests.harness import selftest_thread
    ok, tail = selftest_thread()
    need(tail, "金絲雀沒有任何輸出（前提）")
    if not ok:
        return f"背景執行緒的 traceback 沒有被攔到：{tail}"


@case("32-b", "改寫工具：整段刪除不會被換行檢查擋下（自我驗證）", infra=True)
def _():
    ex, clock = fresh()
    import subprocess
    out = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "patch.py")],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    need("patch 自我驗證" in out, f"改寫工具的自我驗證沒有執行（前提）：{out[-200:]}")
    if "整段刪除除外" not in out or out.startswith("✕"):
        return out.strip()


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✓' if not err else '✕'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
