"""BINANCE_LESSONS 第 8 條 r86：停止開關重啟後還在、讀不到當成停止中；「已經決定出場、還沒平完」的狀態存得下來；
寫的一方的白名單沒漏掉執行時長出來的鍵。

    python3 -m tests.test_r86
"""
import ast
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, ROOT)
from tests import test_r42 as R                                 # noqa: E402
from tests import test_r75 as R75                               # noqa: E402
from tests.fake_exchange import need                            # noqa: E402
from tests.harness import make_case                             # noqa: E402

RESULTS = []
case = make_case(RESULTS)
fresh, open_long, restart, market_posts = R.fresh, R.open_long, R75.restart, R.market_posts
STATE_WRITTEN = ("enabled", "pending", "positions", "trades", "lastNet", "leftovers")
NOT_PERSISTED = {"lastRun": "上次背景那輪的時間，畫面顯示用，重啟後自然更新",
                 "loadError": "讀取失敗的狀態本身，每次開機重讀（r37）",
                 "errors": "錯誤清單，畫面顯示用"}


def T():
    return R.T


def state_path():
    p = os.path.join(tempfile.mkdtemp(), "state.json")
    T().STATE_FILE = p
    return p


def runtime_keys(name):
    src = open(os.path.join(ROOT, "backend", "trader.py"), encoding="utf-8").read()
    ks = set()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Store) and isinstance(n.value, ast.Name) \
                and n.value.id == name and isinstance(n.slice, ast.Constant):
            ks.add(n.slice.value)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "setdefault" \
                and isinstance(n.func.value, ast.Name) and n.func.value.id == name and len(n.args) >= 1:
            first = n.args[0]
            if isinstance(first, ast.Constant):
                ks.add(first.value)
    return ks


@case("r86-1", "自動下單關閉（停止中）重啟後還是關的；當日斷路器擋下的狀態重啟後還在")
def _():
    ex, clock = fresh(keep_save=True)
    path = state_path()
    T().auto_roll_day()
    T().AUTO.update({"on": False, "blocked": "當日已虧損 -3.10R，達停損上限", "closedR": -3.1})
    T().save_state()
    restart(ex, path)
    if T().AUTO.get("on") is not False:
        return f"停止中的自動下單重啟後變成 {T().AUTO.get('on')}"
    if not T().AUTO.get("blocked"):
        return "斷路器擋下的狀態重啟後不見了（部署一次就悄悄解除）"
    T().AUTO["on"] = True
    ok, why = T().auto_can_trade("XUSDT")
    if ok:
        return "重啟後斷路器沒擋"


@case("r86-2", "狀態檔讀不到：當成停止中（自動、手動都不開新倉），不是當成全新開始")
def _():
    ex, clock = fresh(keep_save=True)
    path = state_path()
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"state": {"positions": {"XUSDT"')
    ok_load, err = T().load_state(path)
    need(not ok_load and T().STATE.get("loadError"), f"壞檔應讀取失敗（前提）：{ok_load}、{err}")
    T().AUTO["on"] = True
    T().auto_roll_day()
    ok, why = T().auto_can_trade("XUSDT")
    n0 = len(ex.calls)
    r = T().open_position("X", "LONG", 100.0, None, stop_pct=5)
    if ok:
        return "讀不到狀態檔，自動下單照樣放行"
    if r.get("ok") or R.entry_sent(ex, n0):
        return f"讀不到狀態檔，手動開倉照樣送單：{r}"


@case("r86-3", "已經決定出場、還沒平完（待平倉＋只成交一部分）：重啟後標記都在，重試照樣把剩下的平掉、出場價含兩段")
def _():
    ex, clock = fresh(keep_save=True)
    path = state_path()
    r0 = open_long(ex)
    ex.market_new["close"] = "partial"
    ex.mark["XUSDT"] = 100.0
    T().close_position("XUSDT")
    pos = T().STATE["positions"].get("XUSDT") or {}
    need(pos.get("pendingClose") and pos.get("closePartial"), f"應是待平倉＋部分成交（前提）：{pos.get('pendingClose')}／{pos.get('closePartial')}")
    restart(ex, path)
    pos = T().STATE["positions"].get("XUSDT") or {}
    if not pos.get("pendingClose"):
        return "重啟後「已經決定出場」的待平倉不見了（不再重試平倉）"
    if not pos.get("closePartial"):
        return "重啟後「只成交一部分」的標記不見了（最後出場價只會算後半段）"
    ex.market_new.pop("close")
    ex.mark["XUSDT"] = 104.0
    T().retry_pending_closes()
    need(ex.pos.get(("XUSDT", "LONG"), [0])[0] <= 1e-9, "重試後交易所上還有部位（前提）")
    sells = [f for f in ex.fills if f["side"] == "SELL"]
    need(len(sells) >= 2 and T().STATE["trades"], "應有兩段平倉成交並結帳（前提）")
    want = sum(float(f["qty"]) * float(f["price"]) for f in sells) / sum(float(f["qty"]) for f in sells)
    t = T().STATE["trades"][-1]
    if t.get("exit") is None or abs(float(t["exit"]) - want) > 1e-6:
        return f"重啟後出場價 {t.get('exit')}，應為兩段加權 {want:.6f}"
    if abs(float(t.get("qty") or 0) - r0["qty"]) > 1e-9:
        return f"結帳數量 {t.get('qty')}，應為 {r0['qty']}"


@case("r86-4", "探針：部位、平倉紀錄上塞一個從來沒出現過的鍵，重啟後讀得回來（以後有人在讀或寫的一方加過濾，這項會失敗）")
def _():
    ex, clock = fresh(keep_save=True)
    path = state_path()
    open_long(ex)
    T().STATE["positions"]["XUSDT"]["__probe_pos__"] = 7
    T().AUTO["__probe_auto__"] = 8
    T().STATE["trades"].append({"id": "probe", "symbol": "YUSDT", "__probe_trade__": 9})
    T().save_state()
    restart(ex, path)
    pos = T().STATE["positions"].get("XUSDT") or {}
    t = next((x for x in T().STATE["trades"] if x.get("id") == "probe"), {})
    if pos.get("__probe_pos__") != 7:
        return "部位上新長出來的鍵重啟後不見了"
    if t.get("__probe_trade__") != 9:
        return "平倉紀錄上新長出來的鍵重啟後不見了"
    if "__probe_auto__" in T().AUTO:
        return "自動下單狀態收了預設值以外的鍵（r85 的過濾被拿掉了，舊檔的垃圾會混進來）"


@case("r86-5", "寫的一方的白名單：程式執行時寫進最上層狀態的鍵都在存檔清單裡，或列在「刻意不存」並寫理由；豁免清單不留過期的")
def _():
    ex, clock = fresh()
    keys = runtime_keys("STATE")
    need(keys, "掃不到執行時寫進 STATE 的鍵（前提）")
    missing = sorted(k for k in keys if k not in STATE_WRITTEN and k not in NOT_PERSISTED)
    if missing:
        return f"執行時寫進 STATE、存檔卻不寫的鍵：{missing}（重啟就丟了；要存就加進存檔清單，不存就列理由）"
    stale = sorted(k for k in NOT_PERSISTED if k not in keys and k not in T().STATE)
    if stale:
        return f"「刻意不存」清單裡有程式已經不用的鍵：{stale}"
    cfg_written = set(T().PERSIST_CFG)
    import main as M
    web = set(getattr(M, "TRADE_CFG_SPEC", {}))
    need(web, "取不到網頁能改的設定清單（前提）")
    lost = sorted(web - cfg_written)
    if lost:
        return f"網頁能改、存檔卻不寫的設定：{lost}（改了重啟就回到預設）"


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✓' if not err else '✕'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
