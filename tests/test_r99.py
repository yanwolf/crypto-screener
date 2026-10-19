"""交易所一直拒絕平倉（2026-10-20 模擬盤：WLFI、BR、XAU 連續上千次 ReduceOnly Order is rejected，
網頁上手動平倉、重置資產後全部平倉也一樣）。

- 交易所明確拒絕連續 30 次（約 10 分鐘）：標成卡住、只推一則要人工處理的通知，之後每 10 分鐘才試一次。
- 暫時性錯誤（500、逾時）不算：照舊每輪重試（正式網要緊急出場時不能被拖慢）。
- 停止追蹤：帳上移除、記一筆排除的損益未知（不算進斷路器）、交易所不動；這檔不再進場；交易所清空後自動解除；重啟後還在。

    python3 -m tests.test_r99
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, ROOT)
from tests import test_r42 as R                                 # noqa: E402
from tests import test_r71 as R71                               # noqa: E402
from tests import test_r75 as R75                               # noqa: E402
from tests.fake_exchange import need                            # noqa: E402
from tests.harness import make_case                             # noqa: E402

RESULTS = []
case = make_case(RESULTS)
fresh, open_long, restart, market_posts = R.fresh, R.open_long, R75.restart, R.market_posts
capture_notify = R71.capture_notify


def T():
    return R.T


def pending_after_close(ex, how):
    """開一筆多單，平倉時交易所照 how 回應：回傳部位（應已記待平倉）。"""
    open_long(ex)
    ex.market_new["close"] = how
    r = T().close_position("XUSDT")
    need(not r.get("ok"), f"平倉應失敗（前提）：{r}")
    pos = T().STATE["positions"].get("XUSDT") or {}
    need(pos.get("pendingClose"), "應記為待平倉（前提）")
    return pos


def retry(clock, n, step=20):
    for _i in range(n):
        clock.sleep(step)
        T().retry_pending_closes()


@case("x-1", "交易所連續拒絕 30 次：標成卡住、推一則「需要人工處理」，之後 10 分鐘內不再送平倉單；時間到再試一次")
def _():
    ex, clock = fresh()
    need(hasattr(T(), "REJECT_STUCK_AFTER"), "程式沒有拒絕退讓（前提：2026-10-20 起）")
    pending_after_close(ex, "reject")
    notes = capture_notify()
    retry(clock, 29)
    pc = T().STATE["positions"]["XUSDT"]["pendingClose"]
    need(pc["rejects"] == 30, f"應已連續被拒 30 次（前提）：{pc.get('rejects')}")
    if not pc.get("stuck"):
        return "連續被拒 30 次沒有標成卡住"
    stuck = [x for x in notes if "持續拒絕" in x[0]]
    if len(stuck) != 1 or "停止追蹤" not in stuck[0][1]:
        return f"應推一則要人工處理的通知（講明停止追蹤）：{[x[0] for x in notes]}"
    n0 = len(market_posts(ex, 0, close=True))
    retry(clock, 25)                                            # 約 8 分鐘
    if len(market_posts(ex, 0, close=True)) != n0:
        return "卡住後 10 分鐘內還在送平倉單"
    if [x for x in notes if "平倉失敗，部位仍在" in x[0] and "第 1" not in x[0]][5:]:
        return "卡住後還照次數洗版"
    retry(clock, 6)                                             # 再 2 分鐘：過了 10 分鐘
    if len(market_posts(ex, 0, close=True)) != n0 + 1:
        return f"10 分鐘到了應再試一次：{len(market_posts(ex, 0, close=True)) - n0}"


@case("x-2", "對照：暫時性錯誤（500）就算失敗 40 次也不算卡住，照舊每輪重試（正式網緊急出場不能被拖慢）")
def _():
    ex, clock = fresh()
    need(hasattr(T(), "REJECT_STUCK_AFTER"), "程式沒有拒絕退讓（前提）")
    pending_after_close(ex, "error")
    n0 = len(market_posts(ex, 0, close=True))
    retry(clock, 40)
    pc = T().STATE["positions"]["XUSDT"]["pendingClose"]
    if pc.get("stuck"):
        return "暫時性錯誤被當成交易所拒絕、標成卡住"
    if len(market_posts(ex, 0, close=True)) - n0 != 40:
        return f"暫時性錯誤應每輪重試：40 輪送了 {len(market_posts(ex, 0, close=True)) - n0} 次"


@case("x-3", "卡住之後交易所恢復：時間到那次平掉了 → 照常結帳、通知已平倉")
def _():
    ex, clock = fresh()
    need(hasattr(T(), "REJECT_STUCK_AFTER"), "程式沒有拒絕退讓（前提）")
    pending_after_close(ex, "reject")
    retry(clock, 29)
    need(T().STATE["positions"]["XUSDT"]["pendingClose"].get("stuck"), "應已卡住（前提）")
    ex.market_new.pop("close")
    notes = capture_notify()
    retry(clock, 31)
    if "XUSDT" in T().STATE["positions"]:
        return "交易所恢復後沒有平掉"
    if not [x for x in notes if x[0] == "已平倉"]:
        return "平掉後沒有通知"


@case("x-4", "停止追蹤：帳上移除、記一筆排除的損益未知（不算進當日斷路器）、交易所不送單不撤單；這檔不再進場")
def _():
    ex, clock = fresh()
    need(hasattr(T(), "abandon_position"), "程式沒有停止追蹤（前提：2026-10-20 起）")
    pending_after_close(ex, "reject")
    T().auto_roll_day()
    unk0, closed0 = T().AUTO.get("unknownToday", 0), T().AUTO.get("closedR", 0)
    n_calls = len(ex.calls)
    r = T().abandon_position("XUSDT")
    need(r.get("ok"), f"停止追蹤失敗（前提）：{r}")
    if "XUSDT" in T().STATE["positions"]:
        return "帳上還有這筆"
    sent = [c for c in ex.calls[n_calls:] if c[0] in ("POST", "DELETE")]
    if sent:
        return f"停止追蹤動到交易所：{sent}"
    t = T().STATE["trades"][-1] if T().STATE["trades"] else {}
    if not (t.get("excluded") and t.get("abandoned") and t.get("pnl") is None):
        return f"應記一筆排除、標明放棄追蹤、損益未知的紀錄：{t}"
    if T().AUTO.get("unknownToday", 0) != unk0 or T().AUTO.get("closedR", 0) != closed0:
        return "停止追蹤被算進當日斷路器（這不是策略出場）"
    r2 = T().open_position("X", "LONG", 100.0, None, stop_pct=5)
    if r2.get("ok") or "殘留" not in str(r2.get("error")):
        return f"放棄追蹤的幣還能再進場：{r2}"


@case("x-5", "交易所上那筆清空了：對帳時自動解除、通知、可以再進場；還在的話不動")
def _():
    ex, clock = fresh()
    need(hasattr(T(), "abandoned_sync") and hasattr(T(), "abandon_position"), "程式沒有停止追蹤與殘留部位對帳（前提）")
    pending_after_close(ex, "reject")
    T().abandon_position("XUSDT")
    notes = capture_notify()
    T().sync_positions()
    if "XUSDT" not in T().STATE["abandoned"]:
        return "交易所上還在，卻被解除了"
    ex.pos[("XUSDT", "LONG")] = [0.0, 0.0]                     # 交易所那邊終於清掉了
    T().sync_positions()
    if "XUSDT" in T().STATE["abandoned"]:
        return "交易所清空了沒有解除"
    if not [x for x in notes if "清空" in x[0]]:
        return "解除時沒有通知"


@case("x-6", "放棄追蹤的清單存進狀態檔：重啟後還在，這檔照樣不能進場")
def _():
    ex, clock = fresh(keep_save=True)
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    T().STATE_FILE = path
    need(hasattr(T(), "abandon_position"), "程式沒有停止追蹤（前提：2026-10-20 起）")
    pending_after_close(ex, "reject")
    T().abandon_position("XUSDT")
    need("XUSDT" in T().STATE["abandoned"], "應記進放棄清單（前提）")
    restart(ex, path)
    if "XUSDT" not in (T().STATE.get("abandoned") or {}):
        return "重啟後放棄清單不見了（存檔白名單沒加，第 8 條 r86）"
    r = T().open_position("X", "LONG", 100.0, None, stop_pct=5)
    if r.get("ok"):
        return "重啟後放棄追蹤的幣又能進場"


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✓' if not err else '✕'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
