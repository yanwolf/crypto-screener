"""BINANCE_LESSONS r53 → r55 逐段差異的檢查項目（crypto-screener）。

r54：交易所端的「平掉後同檔重開」——原本那筆在交易所端被停損平掉，別的專案或 App 接著在同一檔同方向開了新部位，
帳上還記著舊的。五條路徑（出場管理的移損、守衛補掛、待平倉重試、網頁手動平倉、對帳）各有一組：
- 防線開著：不能動別人的部位，對帳要把原本那筆照成交明細結帳。
- 對照組：別人**剛好在同一個價格**重開——均價比對分不出新舊，同樣的情境必須出事（r54 pump-dump-hunter：
  有好幾道防線時，每一道的對照組都要讓其他防線失效；這裡交易所端的事鎖本來就擋不到，只剩均價比對一道）。
另有兩組：均價不同但沒有平倉成交（帳上記法有出入，不是重開）照常管理；成交明細查不到時不送單。
前提都在動作之前記下（r54：前提不能量到被測的結果）。

    python3 -m tests.test_r55
"""
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
fresh, open_long, market_posts, stops = R.fresh, R.open_long, R.market_posts, R.stops_on_exchange
OTHER = 200.0                                                   # 別人重開的數量（跟我們的不同，才看得出被動過）


def T():
    return R.T


def reopen(ex, same_price=False):
    """交易所端：原本那筆的停損觸發（停損單用掉了），別人接著在同一檔同方向開 OTHER 顆。回傳別人的進場價。"""
    pos = T().STATE["positions"]["XUSDT"]
    for k in [k for k, v in ex.algo.items() if v.get("type") == "STOP_MARKET"]:
        ex.algo.pop(k)
    ex.trigger("XUSDT", "LONG", pos["qty"], 95.0)
    px = pos["entry"] if same_price else 97.0
    ex._fill("XUSDT", "BUY", "LONG", OTHER, px)
    ex.pos[("XUSDT", "LONG")] = [OTHER, px]
    return px


def sent_for(ex, since):
    """這段期間對 XUSDT 送出的單（市價平倉、條件單）。"""
    return [c for c in ex.calls[since:] if c[0] == "POST" and c[2].get("symbol") == "XUSDT"
            and (c[1] == "/fapi/v1/algoOrder" or (c[1] == "/fapi/v1/order" and R.Ex42.is_close(c[2])))]


def other_qty(ex):
    return ex.pos.get(("XUSDT", "LONG"), [0])[0]


# ── 五條路徑 ────────────────────────────────────────────────────────────

def path_manage(ex):
    ex.mark["XUSDT"] = 108.0                                    # 過了 1R，出場管理會移損
    T().manage_positions()


def path_guard(ex):
    for _i in range(4):
        T().guard_positions()


def path_retry(ex):
    T()._set_pending_close(T().STATE["positions"]["XUSDT"], "測試：待平倉", "測試")
    T().retry_pending_closes()


def path_manual(ex):
    T().close_position("XUSDT")


def run_path(ex, fn, same_price):
    open_long(ex)
    reopen(ex, same_price=same_price)
    need(other_qty(ex) == OTHER, "別人重開的部位沒放上去（前提，動作之前記下）")
    need("XUSDT" in T().STATE["positions"], "帳上應還記著原本那筆（前提：對帳還沒跑到）")
    n0 = len(ex.calls)
    fn(ex)
    return sent_for(ex, n0), other_qty(ex)


@case("r54-a", "交易所端重開：出場管理（移損到成本）不能動別人的部位")
def _():
    ex, clock = fresh()
    sent, left = run_path(ex, path_manage, same_price=False)
    if sent or left != OTHER:
        return f"出場管理（移損到成本）對別人的部位送了 {len(sent)} 張單，別人的部位剩 {left}（原本 {OTHER:g}）"


@case("r54-ac", "對照組（同價重開，均價比對失效）：出場管理（移損到成本）必須動到別人的部位")
def _():
    ex, clock = fresh()
    sent, left = run_path(ex, path_manage, same_price=True)
    if not sent:
        return "對照組裡出場管理（移損到成本）沒有送單——情境沒走到送單那一步，證明不了均價比對"


@case("r54-b", "交易所端重開：守衛（停損不見，補掛）不能動別人的部位")
def _():
    ex, clock = fresh()
    sent, left = run_path(ex, path_guard, same_price=False)
    if sent or left != OTHER:
        return f"守衛（停損不見，補掛）對別人的部位送了 {len(sent)} 張單，別人的部位剩 {left}（原本 {OTHER:g}）"


@case("r54-bc", "對照組（同價重開，均價比對失效）：守衛（停損不見，補掛）必須動到別人的部位")
def _():
    ex, clock = fresh()
    sent, left = run_path(ex, path_guard, same_price=True)
    if not sent:
        return "對照組裡守衛（停損不見，補掛）沒有送單——情境沒走到送單那一步，證明不了均價比對"


@case("r54-c", "交易所端重開：待平倉重試不能動別人的部位")
def _():
    ex, clock = fresh()
    sent, left = run_path(ex, path_retry, same_price=False)
    if sent or left != OTHER:
        return f"待平倉重試對別人的部位送了 {len(sent)} 張單，別人的部位剩 {left}（原本 {OTHER:g}）"


@case("r54-cc", "對照組（同價重開，均價比對失效）：待平倉重試必須動到別人的部位")
def _():
    ex, clock = fresh()
    sent, left = run_path(ex, path_retry, same_price=True)
    if not sent:
        return "對照組裡待平倉重試沒有送單——情境沒走到送單那一步，證明不了均價比對"


@case("r54-d", "交易所端重開：網頁手動平倉不能動別人的部位")
def _():
    ex, clock = fresh()
    sent, left = run_path(ex, path_manual, same_price=False)
    if sent or left != OTHER:
        return f"網頁手動平倉對別人的部位送了 {len(sent)} 張單，別人的部位剩 {left}（原本 {OTHER:g}）"


@case("r54-dc", "對照組（同價重開，均價比對失效）：網頁手動平倉必須動到別人的部位")
def _():
    ex, clock = fresh()
    sent, left = run_path(ex, path_manual, same_price=True)
    if not sent:
        return "對照組裡網頁手動平倉沒有送單——情境沒走到送單那一步，證明不了均價比對"


@case("r54-e", "交易所端重開：對帳要把原本那筆照成交明細結帳（出場價是停損的成交價），別人的部位不動")
def _():
    ex, clock = fresh()
    open_long(ex)
    reopen(ex)
    need(other_qty(ex) == OTHER, "別人重開的部位沒放上去（前提）")
    n0 = len(ex.calls)
    T().sync_positions()
    if "XUSDT" in T().STATE["positions"]:
        return "對帳把別人的部位當成原本那筆，繼續持有"
    if not T().STATE["trades"]:
        return "沒有結帳"
    t = T().STATE["trades"][-1]
    if t.get("exit") is None or abs(float(t["exit"]) - 95.0) > 1e-9:
        return f"出場價 {t.get('exit')}，應為停損的成交價 95"
    if sent_for(ex, n0) or other_qty(ex) != OTHER:
        return "對帳動到了別人的部位"


@case("r54-ec", "對照組（同價重開）：對帳必須把別人的部位當成原本那筆（證明是均價比對擋下的）")
def _():
    ex, clock = fresh()
    open_long(ex)
    reopen(ex, same_price=True)
    need(other_qty(ex) == OTHER, "別人重開的部位沒放上去（前提）")
    T().sync_positions()
    if "XUSDT" not in T().STATE["positions"]:
        return "對照組裡對帳也判成平倉了——同價重開不該分得出來，情境不對"


# ── 誤判保護與查不到 ──────────────────────────────────────────────────

@case("r54-f", "均價跟帳上成交價有出入、但沒有任何平倉成交（部位從沒被平過）：照常管理，手動平倉照送")
def _():
    ex, clock = fresh()
    open_long(ex)
    T().STATE["positions"]["XUSDT"]["entry"] = 100.0            # 帳上記法跟交易所的 100.4 有出入
    need(not [f for f in ex.fills if f["side"] == "SELL"], "不該有任何平倉成交（前提）")
    T().sync_positions()
    if "XUSDT" not in T().STATE["positions"]:
        return "均價有出入就被當成已平倉（會撤掉還在場部位的停損）"
    n0 = len(ex.calls)
    r = T().close_position("XUSDT")
    if not market_posts(ex, n0, close=True) or not r.get("ok"):
        return f"手動平倉沒送出：{r}"


@case("r54-g", "交易所端重開、成交明細查不到：判斷不了，不送任何單、也不結帳")
def _():
    ex, clock = fresh()
    open_long(ex)
    reopen(ex)
    need(other_qty(ex) == OTHER, "別人重開的部位沒放上去（前提）")
    ex.trades_fail = True
    n0 = len(ex.calls)
    r = T().close_position("XUSDT")
    T().sync_positions()
    if sent_for(ex, n0) or other_qty(ex) != OTHER:
        return f"判斷不了還送單：{r}"
    if "XUSDT" not in T().STATE["positions"]:
        return "判斷不了就結帳了"


# ── r55：每條平倉路徑送單前都有部位確認 ─────────────────────────────────

@case("r55-a", "每條平倉路徑都經過同一個送單前確認：交易所上沒了就不送（手動平倉、待平倉重試、守衛 -2021）")
def _():
    ex, clock = fresh()
    open_long(ex)
    q = T().STATE["positions"]["XUSDT"]["qty"]
    ex.trigger("XUSDT", "LONG", q, 95.0)
    need(other_qty(ex) == 0, "交易所上應已沒有部位（前提）")
    n0 = len(ex.calls)
    T().close_position("XUSDT")
    if "XUSDT" in T().STATE["positions"]:
        T()._set_pending_close(T().STATE["positions"]["XUSDT"], "測試", "測試")
        T().retry_pending_closes()
    if market_posts(ex, n0, close=True):
        return "交易所上已經沒有部位，平倉路徑還是送了單"


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✓' if not err else '✕'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
