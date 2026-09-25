"""BINANCE_LESSONS 第 15 條 r72、r73：界線之後的平倉成交要湊滿；估算／未知過的部分出場沒推進界線，最後出場會把那段混進去。

情境：部位 1 張，交易所端減碼 0.4 張（App／ADL），那段成交當下還沒出現在成交明細；之後交易所端平掉剩下 0.6 張。

    python3 -m tests.test_r73
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, ROOT)
from tests import test_r42 as R                                 # noqa: E402
from tests import test_r71 as R71                               # noqa: E402
from tests.fake_exchange import need                            # noqa: E402
from tests.harness import make_case                             # noqa: E402

RESULTS = []
case = make_case(RESULTS)
fresh, open_long = R.fresh, R.open_long
run_backfills, capture_notify = R71.run_backfills, R71.capture_notify
REDUCE_PX, CLOSE_PX = 108.0, 104.0                             # 減碼那段的價格跟最後出場差得夠遠，混進去看得出來


def T():
    return R.T


def reduce_hidden(ex, clock, frac=0.4):
    """交易所端減碼 frac，成交明細 5 秒後才出現；對帳一次 → 這段記未知。回傳 (減碼量, 剩餘量)。"""
    q0 = T().STATE["positions"]["XUSDT"]["qty"]
    part = round(q0 * frac, 1)
    ex.trades_visible_at = clock.time() + 5
    ex.trigger("XUSDT", "LONG", part, REDUCE_PX)
    T().sync_positions()
    pos = T().STATE["positions"].get("XUSDT") or {}
    need(pos.get("qty") is not None and abs(pos["qty"] - (q0 - part)) < 1e-9, f"減碼沒被對帳偵測到（前提）：{pos.get('qty')}")
    parts = pos.get("partials") or []
    need(parts and parts[-1].get("px") is None, "減碼那段當下應記未知（前提：成交明細還沒出現）")
    return part, q0 - part


@case("r73-1", "減碼那段稍後才出現、接著交易所端平掉剩下的：出場價只算平倉那 0.6 張，不能把減碼那 0.4 混進去")
def _():
    ex, clock = fresh()
    open_long(ex)
    part, left = reduce_hidden(ex, clock)
    R.BACKFILLS[:] = []                                         # 這項不跑背景補登：測結帳時自己跳過
    clock.sleep(10)                                             # 減碼那段的成交現在出現了
    ex.trigger("XUSDT", "LONG", left, CLOSE_PX)
    T().sync_positions()
    need(T().STATE["trades"], "沒有結帳（前提）")
    t = T().STATE["trades"][-1]
    if t.get("exit") is None:
        return "出場價記成未知（減碼那段沒被跳過、或跨過帳上數量被當成分不出）"
    if abs(float(t["exit"]) - CLOSE_PX) > 1e-6:
        return f"出場價 {t['exit']}，應為 {CLOSE_PX}（減碼那 {part:g} 張混進來了）"


@case("r73-2", "減碼那段查不到：部位記下還沒認領的減少量，界線不推進（不能把界線跳過空的地方）")
def _():
    ex, clock = fresh()
    open_long(ex)
    pos0 = dict(T().STATE["positions"]["XUSDT"])
    part, left = reduce_hidden(ex, clock)
    pos = T().STATE["positions"]["XUSDT"]
    need(hasattr(T(), "schedule_backfill"), "程式沒有背景補登（前提：r71 起才有）")
    if abs(float(pos.get("unclaimedQty") or 0) - part) > 1e-9:
        return f"沒記下還沒認領的減少量：{pos.get('unclaimedQty')}（應為 {part:g}）"
    if pos.get("fillsFromId") != pos0.get("fillsFromId"):
        return "成交沒出現卻推進了界線"


@case("r73-3", "減碼那段之後出現：背景補登換成實際損益、界線推進過去、清掉記號；之後平倉出場價正確")
def _():
    ex, clock = fresh()
    open_long(ex)
    part, left = reduce_hidden(ex, clock)
    notes = capture_notify()
    n = run_backfills()
    need(n == 1, f"應排一個背景補登工作（前提），實際 {n}")
    pos = T().STATE["positions"]["XUSDT"]
    p = (pos.get("partials") or [{}])[-1]
    if p.get("px") is None or abs(float(p["px"]) - REDUCE_PX) > 1e-6:
        return f"減碼那段沒補成實際成交價：{p.get('px')}"
    if float(pos.get("unclaimedQty") or 0) > 1e-9:
        return f"補登後記號沒清掉：{pos.get('unclaimedQty')}"
    if not [x for x in notes if "補登" in x[0]]:
        return "沒有補發通知"
    ex.trigger("XUSDT", "LONG", left, CLOSE_PX)
    T().sync_positions()
    need(T().STATE["trades"], "沒有結帳（前提）")
    t = T().STATE["trades"][-1]
    if t.get("exit") is None or abs(float(t["exit"]) - CLOSE_PX) > 1e-6:
        return f"補登推進界線後，最後出場價 {t.get('exit')}，應為 {CLOSE_PX}"
    entry = float(t["entry"])
    want = (REDUCE_PX - entry) * part + (CLOSE_PX - entry) * left
    if t.get("pnl") is None or abs(float(t["pnl"]) - want) > 1e-3:
        return f"整筆損益 {t.get('pnl')}，應為 {want:.4f}"


@case("r73-4", "r72：交易所端平倉的結帳，界線之後的平倉成交只先出現 0.4 張：出場價記未知，不拿前半段當實際成交價")
def _():
    ex, clock = fresh()
    open_long(ex)
    q = T().STATE["positions"]["XUSDT"]["qty"]
    ex.trigger("XUSDT", "LONG", q, CLOSE_PX)

    def first_part(rows):
        if not rows:
            return rows
        head = rows[0]
        return [dict(head, qty=str(round(float(head["qty"]) * 0.4, 3)))]
    ex.trades_filter = first_part
    T().sync_positions()
    need(T().STATE["trades"], "沒有結帳（前提）")
    t = T().STATE["trades"][-1]
    if t.get("exit") is not None:
        return f"只出現 0.4 張就算出出場價 {t['exit']}"


@case("r73-5", "界線之後的平倉成交最後一筆跨過帳上數量（多出來的是別的東西）：分不出，記未知，不硬算")
def _():
    ex, clock = fresh()
    open_long(ex)
    q = T().STATE["positions"]["XUSDT"]["qty"]
    ex.trigger("XUSDT", "LONG", q, CLOSE_PX)
    ex._fill("XUSDT", "SELL", "LONG", round(q * 0.5, 1), 90.0)   # 之後又多了一筆平倉方向的成交（別的減碼／重開後的平倉）
    T().sync_positions()
    need(T().STATE["trades"], "沒有結帳（前提）")
    t = T().STATE["trades"][-1]
    if t.get("exit") is not None:
        return f"跨過帳上數量還算出出場價 {t['exit']}（把多出來的那筆混進去）"


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✓' if not err else '✕'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
