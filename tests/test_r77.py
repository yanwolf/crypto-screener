"""BINANCE_LESSONS 第 15 條 r76、r77：出場補登的記號在結帳之前寫、完成或放棄時清掉；
進場補登跑完之前部位就平掉、接著重啟，這筆的進場成交價還要有人再查。

    python3 -m tests.test_r77
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
fresh, open_long = R.fresh, R.open_long
run_backfills, capture_notify, restart = R71.run_backfills, R71.capture_notify, R75.restart


def T():
    return R.T


def state_path():
    p = os.path.join(tempfile.mkdtemp(), "state.json")
    T().STATE_FILE = p
    return p


def close_unknown(ex, clock, forever=False):
    """平倉，出場價當下查不到（成交明細稍後才出現；forever：永遠查不到）。"""
    ex.drop_avg = True
    if forever:
        ex.trades_fail = True
    else:
        ex.trades_visible_at = clock.time() + 20
    r = T().close_position("XUSDT")
    need(r.get("ok") and T().STATE["trades"], f"沒有結帳（前提）：{r}")
    t = T().STATE["trades"][-1]
    need(t.get("exit") is None, "出場價應先記未知（前提）")
    return t


@case("r76-1", "出場補登的記號寫在結帳（界線）之前：存檔那一刻紀錄上就有記號，重啟讀回來也有")
def _():
    ex, clock = fresh(keep_save=True)
    path = state_path()
    open_long(ex)
    saves = []
    real_save = T().save_state
    T().save_state = lambda: saves.append(bool((T().STATE["trades"] or [{}])[-1].get("backfillPending"))) or real_save()
    t = close_unknown(ex, clock)
    need(saves, "結帳時沒有存檔（前提）")
    if not t.get("backfillPending"):
        return "紀錄上沒有「補登還沒完成」的記號"
    if not saves[0]:
        return "結帳那次存檔時記號還沒寫進去（記號在界線之後才寫，中間出事就丟了）"
    T().save_state = real_save
    restart(ex, path)
    if not T().STATE["trades"] or not T().STATE["trades"][-1].get("backfillPending"):
        return "重啟後紀錄上的記號不見了"


@case("r76-2", "補登完成：記號清掉；放棄（查了幾次仍查不到）：記號也清掉，重啟不再排")
def _():
    ex, clock = fresh(keep_save=True)
    path = state_path()
    open_long(ex)
    t = close_unknown(ex, clock, forever=True)
    need(t.get("backfillPending"), "放棄之前記號應該在（前提）")
    capture_notify()
    run_backfills()
    need(T().STATE["trades"], "平倉紀錄不見了（前提）")
    t = T().STATE["trades"][-1]
    if t.get("backfillPending"):
        return "放棄後記號沒清掉"
    R.BACKFILLS[:] = []
    restart(ex, path)
    if R.BACKFILLS:
        return "放棄後重啟還是重新排了"


@case("r77-1", "結帳後出場價已從別處拿到、補登才跑：不覆寫、只清記號")
def _():
    ex, clock = fresh(keep_save=True)
    state_path()
    open_long(ex)
    t = close_unknown(ex, clock)
    t["exit"] = 123.45                                          # 這段期間從別的地方補上了
    capture_notify()
    run_backfills()
    need(T().STATE["trades"], "平倉紀錄不見了（前提）")
    t = T().STATE["trades"][-1]
    if t.get("exit") != 123.45:
        return f"補登覆寫了已有的出場價：{t.get('exit')}"
    if t.get("backfillPending"):
        return "已有出場價，記號卻沒清掉"


@case("r77-2", "進場補登跑完之前部位就平掉、接著重啟：紀錄上的記號讓進場價還有人再查，補到平倉紀錄上並補算損益")
def _():
    ex, clock = fresh(keep_save=True)
    path = state_path()
    ex.pos[("XUSDT", "LONG")] = [2.0, 90.0]
    ex.drop_avg = True
    ex.trades_visible_at = clock.time() + 20                    # 進場成交稍後才出現
    ex.mark["XUSDT"] = 100.4
    r = T().open_position("X", "LONG", 100.0, None, stop_pct=5)
    need(r.get("ok") and (T().STATE["positions"].get("XUSDT") or {}).get("entryUnverified"), f"進場價應先標未驗證（前提）：{r}")
    ex.drop_avg = False
    ex.mark["XUSDT"] = 104.0
    r2 = T().close_position("XUSDT")                            # 進場補登還沒跑，部位就平掉了
    need(r2.get("ok") and T().STATE["trades"] and T().STATE["trades"][-1].get("pnl") is None, f"損益應先記未知（前提）：{r2}")
    R.BACKFILLS[:] = []                                         # 補登跟著服務一起沒了
    restart(ex, path)
    if not R.BACKFILLS:
        return "重啟後沒人再查這筆的進場成交價（紀錄上沒有記號）"
    capture_notify()
    run_backfills()
    t = T().STATE["trades"][-1]
    buys = [f for f in ex.fills if f["side"] == "BUY"]
    need(buys, "沒有進場成交（前提）")
    if t.get("entryUnverified") or abs(float(t.get("entry") or 0) - float(buys[-1]["price"])) > 1e-9:
        return f"進場價沒補到平倉紀錄上：{t.get('entry')}"
    if t.get("pnl") is None:
        return "進場價補到了，損益卻沒補算"


@case("r77-3", "進場補登（部位還開著時排的）跑到一半部位平掉：查到後補到平倉紀錄，不是丟掉")
def _():
    ex, clock = fresh()
    ex.pos[("XUSDT", "LONG")] = [2.0, 90.0]
    ex.drop_avg = True
    ex.trades_visible_at = clock.time() + 20
    ex.mark["XUSDT"] = 100.4
    r = T().open_position("X", "LONG", 100.0, None, stop_pct=5)
    need(r.get("ok") and R.BACKFILLS, f"應排了進場補登（前提）：{r}")
    ex.drop_avg = False
    ex.mark["XUSDT"] = 104.0
    r2 = T().close_position("XUSDT")
    need(r2.get("ok") and T().STATE["trades"], f"沒有結帳（前提）：{r2}")
    capture_notify()
    run_backfills()
    t = T().STATE["trades"][-1]
    if t.get("entryUnverified") or t.get("pnl") is None:
        return f"補登找不到部位就放棄了：進場 {t.get('entry')}（未驗證 {t.get('entryUnverified')}）、損益 {t.get('pnl')}"


@case("r77-4", "測試框架：新加的重排上限計數都在狀態裡、模組層級的補登計數每個情境重設（整套跑跟單獨跑結果一樣）")
def _():
    ex, clock = fresh()
    need(hasattr(T(), "BACKFILL"), "程式沒有 BACKFILL 計數（前提）")
    T().BACKFILL["scheduled"] = 99
    fresh()
    if T().BACKFILL.get("scheduled") != 0:
        return f"模組層級的補登計數沒重設：{T().BACKFILL}"
    if any(k.startswith("_reschedul") for k in vars(T())):
        return "程式有行程層級的「只做一次」旗標，框架沒有納入重設"


@case("r78-1", "進場補登放棄之後：記號標成已結束、不刪掉；之後每次重啟都不再查（放棄過的跟從來沒有記號的分得出來）")
def _():
    ex, clock = fresh(keep_save=True)
    path = state_path()
    ex.pos[("XUSDT", "LONG")] = [2.0, 90.0]
    ex.drop_avg = True
    ex.trades_fail = True                                       # 永遠查不到
    ex.mark["XUSDT"] = 100.4
    r = T().open_position("X", "LONG", 100.0, None, stop_pct=5)
    need(r.get("ok") and (T().STATE["positions"].get("XUSDT") or {}).get("entryUnverified"), f"進場價應先標未驗證（前提）：{r}")
    need(R.BACKFILLS, "應排了進場補登（前提）")
    capture_notify()
    run_backfills()                                             # 查四次都查不到 → 放棄
    counts = []
    for _i in range(2):
        R.BACKFILLS[:] = []
        restart(ex, path)
        counts.append(len(R.BACKFILLS))
    if counts != [0, 0]:
        return f"放棄過的進場補登重啟後又排了：{counts}"
    pos = T().STATE["positions"]["XUSDT"]
    need(hasattr(T(), "_mark_ended"), "程式沒有「標成已結束」（前提：r79 起才有）")
    mark = pos.get("entryBackfill")
    if not isinstance(mark, dict) or mark.get("ended") != "gave_up":
        return f"放棄後記號不是標成已結束（gave_up）：{mark}"


@case("r78-2", "出場補登查到：記號標成已結束（found），不是刪掉；重啟不再排")
def _():
    ex, clock = fresh(keep_save=True)
    path = state_path()
    open_long(ex)
    t = close_unknown(ex, clock)
    capture_notify()
    run_backfills()
    need(T().STATE["trades"], "平倉紀錄不見了（前提）")
    t = T().STATE["trades"][-1]
    need(t.get("exit") is not None, "補登應已查到出場價（前提）")
    R.BACKFILLS[:] = []
    restart(ex, path)
    if R.BACKFILLS:
        return "已結束的出場補登重啟後又排了"
    t = T().STATE["trades"][-1]
    need(hasattr(T(), "_mark_ended"), "程式沒有「標成已結束」（前提：r79 起才有）")
    if not isinstance(t.get("backfillEnded"), dict) or t["backfillEnded"].get("ended") != "found":
        return f"查到後記號不是標成已結束（found）：{t.get('backfillEnded')}"


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✓' if not err else '✕'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
