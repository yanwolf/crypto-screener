"""BINANCE_LESSONS 第 15 條 r71：帶了 RESULT、回應也是 FILLED，均價照樣可能沒有；當下查不到不能就此放棄。

沒辦法在交易所自檢裡檢查（要真的送單、而且要剛好碰上），只能靠測試：用一份沒有 avgPrice／cumQuote 的 FILLED 回應
（模擬交易所 drop_avg），成交明細「稍後才出現」（trades_visible_at）與「先出現一部分」（trades_filter）兩種情境，跑開倉與平倉。
背景補登不真的開執行緒：fresh() 把 _start_backfill_thread 換掉收下來，由測試自己跑。

    python3 -m tests.test_r71
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, ROOT)
from tests import test_r42 as R                                 # noqa: E402
from tests.fake_exchange import need                            # noqa: E402
from tests.harness import make_case                             # noqa: E402

RESULTS = []
case = make_case(RESULTS)
fresh, open_long, market_posts = R.fresh, R.open_long, R.market_posts


def T():
    return R.T


def run_backfills():
    need(hasattr(T(), "schedule_backfill"), "程式沒有背景補登（前提：r71 起才有）")
    jobs = list(R.BACKFILLS)
    R.BACKFILLS[:] = []
    for fn in jobs:
        fn()
    return len(jobs)


def capture_notify():
    out = []
    T()._notify = lambda title, text: out.append((title, text))
    return out


def order_queries(ex, since):
    return [c for c in ex.calls[since:] if c[0] == "GET" and c[1] == "/fapi/v1/order"]


# ── 均價的來源 ────────────────────────────────────────────────────────

@case("r71-1", "回應沒有 avgPrice 但有 cumQuote：成交額 ÷ 成交量就是實際均價，不是估算、不是未知")
def _():
    ex, clock = fresh()
    need(hasattr(T(), "order_outcome"), "程式沒有 order_outcome（前提）")
    o = T().order_outcome({"status": "FILLED", "executedQty": "2", "cumQuote": "201.0", "orderId": 1})
    if abs((o.get("avg") or 0) - 100.5) > 1e-9:
        return f"cumQuote ÷ executedQty 應為 100.5，實際 {o.get('avg')}"


@case("r71-2", "平倉回應 FILLED 但沒有均價：送單流程重查訂單幾次（不是 1 次），每次寫日誌；成交明細稍後出現 → 拿到實際出場價")
def _():
    ex, clock = fresh()
    open_long(ex)
    ex.drop_avg = True
    ex.trades_visible_at = clock.time() + 1.2                    # 成交明細 1.2 秒後才出現：3 次 0.5 秒重查之後查得到
    n0 = len(ex.calls)
    buf, old = io.StringIO(), sys.stderr
    sys.stderr = buf
    try:
        r = T().close_position("XUSDT")
    finally:
        sys.stderr = old
    need(market_posts(ex, n0, close=True), "平倉單沒有送出（前提）")
    if len(order_queries(ex, n0)) < 2:
        return f"FILLED 沒均價時只重查了 {len(order_queries(ex, n0))} 次訂單"
    if "重查訂單仍沒有均價" not in buf.getvalue():
        return "重查失敗沒有寫日誌"
    sells = [f for f in ex.fills if f["side"] == "SELL"]
    need(sells, "沒有平倉成交（前提）")
    t = T().STATE["trades"][-1] if T().STATE["trades"] else {}
    if t.get("exit") is None or abs(float(t["exit"]) - float(sells[-1]["price"])) > 1e-9:
        return f"出場價 {t.get('exit')}，應為成交明細的 {sells[-1]['price']}（{r}）"


@case("r71-3", "成交明細只先出現 0.4 張：不能拿前半段算成「真實成交價」，要當成還查不到（記未知）")
def _():
    ex, clock = fresh()
    open_long(ex)
    ex.drop_avg = True

    def first_part(rows):
        if not rows:
            return rows
        head = rows[0]
        return [dict(head, qty=str(round(float(head["qty"]) * 0.4, 3)))]
    ex.trades_filter = first_part
    r = T().close_position("XUSDT")
    need(r.get("ok") and T().STATE["trades"], f"沒有結帳（前提）：{r}")
    t = T().STATE["trades"][-1]
    if t.get("exit") is not None:
        return f"成交明細沒湊滿，卻算出了出場價 {t['exit']}（拿前半段當真實成交價）"
    if t.get("pnl") is not None:
        return f"損益應為未知，實際 {t['pnl']}"


@case("r71-4", "出場價當下查不到：先記未知、排背景補登；成交明細之後出現 → 補寫出場價與損益、補發通知、每日統計跟著改")
def _():
    ex, clock = fresh()
    open_long(ex)
    ex.drop_avg = True
    ex.trades_visible_at = clock.time() + 20                     # 送單流程內（幾秒）查不到，背景第二次（3+10 秒）才查得到
    ex.mark["XUSDT"] = 104.0
    T().AUTO["on"] = True
    T().auto_roll_day()
    unk0 = T().AUTO.get("unknownToday", 0)
    r = T().close_position("XUSDT")
    need(r.get("ok") and T().STATE["trades"], f"沒有結帳（前提）：{r}")
    t = T().STATE["trades"][-1]
    need(t.get("exit") is None, "出場價應先記未知（前提：送單流程內查不到）")
    need(T().AUTO.get("unknownToday", 0) == unk0 + 1, "結帳時未知筆數應 +1（前提）")
    notes = capture_notify()
    n = run_backfills()
    if n != 1:
        return f"應排一個背景補登工作，實際 {n}"
    sells = [f for f in ex.fills if f["side"] == "SELL"]
    need(sells, "沒有平倉成交（前提）")
    t = T().STATE["trades"][-1]
    if t.get("exit") is None or abs(float(t["exit"]) - float(sells[-1]["price"])) > 1e-9:
        return f"背景沒有補登出場價：{t.get('exit')}（應為 {sells[-1]['price']}）"
    if t.get("pnl") is None:
        return "補登後損益仍是未知"
    if T().AUTO.get("unknownToday", 0) != unk0:
        return f"補登後未知筆數沒有減回去：{T().AUTO.get('unknownToday')}"
    if not [x for x in notes if "補登" in x[0]]:
        return f"沒有補發通知：{notes}"


@case("r71-5", "背景補登一直查不到：維持未知，推一則「補登失敗」附單號；不估算、不推估")
def _():
    ex, clock = fresh()
    open_long(ex)
    ex.drop_avg = True
    ex.trades_fail = True
    r = T().close_position("XUSDT")
    need(r.get("ok") and T().STATE["trades"], f"沒有結帳（前提）：{r}")
    notes = capture_notify()
    n = run_backfills()
    need(n == 1, f"應排一個背景補登工作（前提），實際 {n}")
    t = T().STATE["trades"][-1]
    if t.get("exit") is not None or t.get("pnl") is not None:
        return f"查不到卻補了數字：{t.get('exit')}／{t.get('pnl')}"
    fail = [x for x in notes if "補登失敗" in x[0]]
    if not fail:
        return f"沒有推「補登失敗」：{notes}"
    if "單號" not in fail[0][1]:
        return f"補登失敗的通知沒附單號：{fail[0]}"


@case("r71-6", "進場回應 FILLED 但沒有均價、成交明細稍後才出現：先用標記價估、標未驗證，背景補登後進場價改成實際成交價、出場位階重算")
def _():
    ex, clock = fresh()
    ex.pos[("XUSDT", "LONG")] = [2.0, 90.0]                     # 有基準：交易所 entryPrice 是合併的，只能靠單號
    ex.drop_avg = True
    ex.trades_visible_at = clock.time() + 20
    ex.mark["XUSDT"] = 100.4
    r = T().open_position("X", "LONG", 100.0, None, stop_pct=5)
    need(r.get("ok") and "XUSDT" in T().STATE["positions"], f"沒有開倉（前提）：{r}")
    pos = T().STATE["positions"]["XUSDT"]
    need(pos.get("entryUnverified"), "進場價當下查不到時應標未驗證（前提：r42）")
    buys = [f for f in ex.fills if f["side"] == "BUY"]
    need(buys and abs(float(buys[-1]["price"]) - 100.4) > 1e-6, "成交價要跟標記價分得出來（前提）")
    notes = capture_notify()
    n = run_backfills()
    if n != 1:
        return f"應排一個背景補登工作，實際 {n}"
    pos = T().STATE["positions"]["XUSDT"]
    if pos.get("entryUnverified") or abs(float(pos.get("entry") or 0) - float(buys[-1]["price"])) > 1e-9:
        return f"背景沒有把進場價補成實際成交價：{pos.get('entry')}（未驗證 {pos.get('entryUnverified')}）"
    if not [x for x in notes if "補登" in x[0]]:
        return "沒有補發通知"


@case("r71-7", "通知裡不貼整包原始回應：查不到時只附單號")
def _():
    ex, clock = fresh()
    open_long(ex)
    ex.drop_avg = True
    ex.trades_fail = True
    notes = capture_notify()
    r = T().close_position("XUSDT")
    need(r.get("ok"), f"沒有平倉（前提）：{r}")
    run_backfills()
    blob = " ".join(str(x) for x in notes) + str(r)
    if "clientOrderId" in blob or "workingType" in blob or "'status':" in blob:
        return "通知或回應裡貼了整包原始回應"


@case("r71-8", "對照：回應有均價時不重查訂單，流程跟以前一樣")
def _():
    ex, clock = fresh()
    open_long(ex)
    n0 = len(ex.calls)
    r = T().close_position("XUSDT")
    need(r.get("ok"), f"沒有平倉（前提）：{r}")
    if order_queries(ex, n0):
        return "回應有均價還重查了訂單"
    if R.BACKFILLS:
        return "有均價還排了背景補登"


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✓' if not err else '✕'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
