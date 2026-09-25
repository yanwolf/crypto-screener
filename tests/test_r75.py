"""BINANCE_LESSONS 第 15 條 r74、r75：記號要存進狀態檔；重啟後排好的背景補登要重新排；框架預設不真的開執行緒。

重啟用真的狀態檔走一遍：fresh(keep_save=True) → 存檔 → 新的 fresh() → load_state 讀回來。

    python3 -m tests.test_r75
"""
import os
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, ROOT)
from tests import test_r42 as R                                 # noqa: E402
from tests import test_r71 as R71                               # noqa: E402
from tests import test_r73 as R73                               # noqa: E402
from tests import fake_exchange as FX                           # noqa: E402
from tests.fake_exchange import need                            # noqa: E402
from tests.harness import make_case                             # noqa: E402

RESULTS = []
case = make_case(RESULTS)
fresh, open_long = R.fresh, R.open_long
run_backfills, capture_notify = R71.run_backfills, R71.capture_notify


def T():
    return R.T


def restart(ex, path):
    """存檔、重新載入模組（模擬重啟）、讀回狀態檔；沿用同一個模擬交易所。"""
    T().save_state()
    ex2, clock2 = fresh(keep_save=True, ex=ex)
    T().STATE_FILE = path
    ok, err = T().load_state(path)
    need(ok, f"重啟後狀態檔讀不回來（前提）：{err}")
    return clock2


@case("r75-1", "部分出場的損益、未知標記、還沒認領的量都存進狀態檔；重啟後讀回來一樣")
def _():
    ex, clock = fresh(keep_save=True)
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    T().STATE_FILE = path
    open_long(ex)
    part, left = R73.reduce_hidden(ex, clock)
    before = dict(T().STATE["positions"]["XUSDT"])
    need(before.get("unclaimedQty") and before.get("partials"), "減碼後應有記號與部分出場紀錄（前提）")
    restart(ex, path)
    pos = T().STATE["positions"].get("XUSDT") or {}
    if abs(float(pos.get("unclaimedQty") or 0) - part) > 1e-9:
        return f"重啟後還沒認領的量不見了：{pos.get('unclaimedQty')}"
    p = (pos.get("partials") or [{}])[-1]
    if p.get("qty") != part or "px" not in p or p.get("px") is not None:
        return f"重啟後部分出場那段的未知標記不見了：{p}"
    if pos.get("entry") != before.get("entry"):
        return f"重啟後進場成交價沒讀回來：{pos.get('entry')}"


@case("r75-2", "重啟後：部分出場那段的補登重新排，之後那段出現 → 補成實際損益、界線推進、記號清掉")
def _():
    ex, clock = fresh(keep_save=True)
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    T().STATE_FILE = path
    open_long(ex)
    part, left = R73.reduce_hidden(ex, clock)
    R.BACKFILLS[:] = []                                         # 重啟前排的那個：跟著服務一起沒了
    restart(ex, path)
    if not R.BACKFILLS:
        return "重啟後沒有重新排補登（排好的補登跟著服務一起沒了）"
    notes = capture_notify()
    run_backfills()
    pos = T().STATE["positions"]["XUSDT"]
    p = (pos.get("partials") or [{}])[-1]
    if p.get("px") is None or abs(float(p["px"]) - R73.REDUCE_PX) > 1e-6:
        return f"重啟後補登沒補成實際成交價：{p.get('px')}"
    if float(pos.get("unclaimedQty") or 0) > 1e-9:
        return "記號沒清掉"
    if not [x for x in notes if "補登" in x[0]]:
        return "沒有補發通知"


@case("r75-3", "重啟後：進場成交價還沒驗證、有開倉單號 → 重新排進場補登，補成實際成交價")
def _():
    ex, clock = fresh(keep_save=True)
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    T().STATE_FILE = path
    ex.pos[("XUSDT", "LONG")] = [2.0, 90.0]
    ex.drop_avg = True
    ex.trades_visible_at = clock.time() + 20
    ex.mark["XUSDT"] = 100.4
    r = T().open_position("X", "LONG", 100.0, None, stop_pct=5)
    need(r.get("ok") and (T().STATE["positions"].get("XUSDT") or {}).get("entryUnverified"), f"進場價應先標未驗證（前提）：{r}")
    R.BACKFILLS[:] = []
    restart(ex, path)
    if not R.BACKFILLS:
        return "重啟後沒有重新排進場補登（排好的補登跟著服務一起沒了）"
    run_backfills()
    pos = T().STATE["positions"]["XUSDT"]
    buys = [f for f in ex.fills if f["side"] == "BUY"]
    need(buys, "沒有進場成交（前提）")
    if pos.get("entryUnverified") or abs(float(pos.get("entry") or 0) - float(buys[-1]["price"])) > 1e-9:
        return f"重啟後進場價沒補成實際成交價：{pos.get('entry')}"


@case("r75-4", "重啟後：出場價未知、還標著待補登的平倉紀錄 → 重新排出場補登，補成實際成交價與損益")
def _():
    ex, clock = fresh(keep_save=True)
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    T().STATE_FILE = path
    open_long(ex)
    ex.drop_avg = True
    ex.trades_visible_at = clock.time() + 20
    r = T().close_position("XUSDT")
    need(r.get("ok") and T().STATE["trades"] and T().STATE["trades"][-1].get("exit") is None, f"出場價應先記未知（前提）：{r}")
    R.BACKFILLS[:] = []
    restart(ex, path)
    if not R.BACKFILLS:
        return "重啟後沒有重新排出場補登（排好的補登跟著服務一起沒了）"
    run_backfills()
    need(T().STATE["trades"], "沒有平倉紀錄（前提）")
    t = T().STATE["trades"][-1]
    sells = [f for f in ex.fills if f["side"] == "SELL"]
    need(sells, "沒有平倉成交（前提）")
    if t.get("exit") is None or abs(float(t["exit"]) - float(sells[-1]["price"])) > 1e-9 or t.get("pnl") is None:
        return f"重啟後出場價沒補成實際成交價：{t.get('exit')}／損益 {t.get('pnl')}"


@case("r75-5", "重啟重排有上限：同一筆重啟兩次後不再重排（永遠查不到的不會每次重啟都排）")
def _():
    ex, clock = fresh(keep_save=True)
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    T().STATE_FILE = path
    open_long(ex)
    ex.drop_avg = True
    ex.trades_fail = True
    r = T().close_position("XUSDT")
    need(r.get("ok") and T().STATE["trades"], f"沒有結帳（前提）：{r}")
    need(T().STATE["trades"][-1].get("exit") is None, "出場價應先記未知（前提）")
    counts = []
    for _i in range(3):
        R.BACKFILLS[:] = []
        restart(ex, path)
        counts.append(len(R.BACKFILLS))
    if counts[:2] != [1, 1] or counts[2] != 0:
        return f"三次重啟各排了 {counts}（應為 1、1、0）"


@case("r75-6", "測試框架：預設不真的開補登執行緒（沒用 fresh() 的舊測試、reload 之後都一樣）")
def _():
    ex, clock = fresh()
    import importlib
    import trader as T2
    importlib.reload(T2)                                        # 舊測試自己 reload 的樣子
    need(hasattr(T2, "schedule_backfill"), "程式沒有背景補登（前提）")
    before = len(FX.BACKFILL_REAL_STARTED)
    n0 = len(FX.BACKFILL_COLLECTED)
    T2.schedule_backfill({"kind": "close", "symbol": "XUSDT"})
    if len(FX.BACKFILL_REAL_STARTED) != before:
        return "reload 之後排補登真的開了執行緒"
    if len(FX.BACKFILL_COLLECTED) != n0 + 1:
        return "沒有被收集器收下"
    fresh()                                                     # 還原給後面的情境


@case("r75-7", "正式路徑：真的那個開的是名稱 backfill 的 daemon 執行緒，而且會跑（金絲雀之外的唯一一個真的）")
def _():
    ex, clock = fresh()
    done = threading.Event()
    seen = {}

    def job():
        seen["thread"] = threading.current_thread()
        done.set()
    before = len(FX.BACKFILL_REAL_STARTED)
    FX.real_start_backfill(job)
    need(done.wait(5), "正式路徑的執行緒沒有跑（前提）")
    th = seen.get("thread")
    if not th or th.name != "backfill" or not th.daemon:
        return f"正式路徑開的執行緒不是 daemon 的 backfill：{th}"
    if len(FX.BACKFILL_REAL_STARTED) != before + 1:
        return "框架沒數到這個真的開的執行緒（全套跑完的計數不可信）"
    FX.BACKFILL_REAL_STARTED.pop()                              # 這一個是故意開的，不算進「全套跑完要是 0」


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✓' if not err else '✕'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
