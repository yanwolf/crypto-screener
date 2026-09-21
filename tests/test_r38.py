"""BINANCE_LESSONS r36 → r38 逐段差異的檢查項目（crypto-screener 從 r36 回來，含 r37、r38）。

新介面（load_state 的回傳值、retry_load_state）一律先用既有介面斷言行為，最後才檢查新介面，
在舊版程式上會是斷言失敗，不是測試崩掉（用法第 5 點 r38）。

    python3 -m tests.test_r38
"""
import io
import os
import sys
import json
import tempfile
import threading
import importlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, ROOT)
import trader as T                                              # noqa: E402
from tests.fake_exchange import Ex17, Clock, need, entry_sent, titled  # noqa: E402

RESULTS = []
from tests.harness import make_case                             # noqa: E402
case = make_case(RESULTS)


def fresh(mode="oneway", keep_save=False, ex=None):
    importlib.reload(T)
    clock = Clock()
    T.time = clock
    if not keep_save:
        T.save_state = lambda: None
    T.CFG.update({"key": "k", "secret": "s", "dryRun": False, "leverage": 3, "riskPct": 0.5,
                  "breakevenR": 1.0, "maxPositions": 8})
    T._filters["XUSDT"] = {"status": "TRADING", "tick": 0.01, "step": 0.1, "minQty": 0.1, "minNotional": 5}
    T._filters_ts = 9e18
    T._algo_supported[0] = True
    ex = ex or Ex17(mode)
    T._request_raw = ex
    return ex, clock


def open_long(ex, mark=100.4):
    ex.mark["XUSDT"] = mark
    n0 = len(ex.calls)
    r = T.open_position("X", "LONG", 100.0, None, stop_pct=5)
    need(entry_sent(ex, n0), f"進場單沒有送出：{r.get('error')}")
    need("XUSDT" in T.STATE["positions"], "進場後帳上沒有部位（前提）")
    return r


def corrupt_file():
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"state": {"positions": {"XUSDT": {"side": "LONG"')      # 寫到一半當機留下的壞檔
    try:
        json.load(open(path, encoding="utf-8"))
        ok = True
    except ValueError:
        ok = False
    need(not ok, "狀態檔其實解析得了（前提：要測的是壞檔）")
    return path


# ════ 第 8 條 r37、r38：讀檔失敗 ≠ 沒有資料 ═══════════════════

@case("8-ah", "狀態檔壞了：暫停開新倉、壞檔另存、推播；不能靜靜回到空白照樣開倉")
def _():
    ex, clock = fresh(keep_save=True)
    T.AUTO["on"] = True
    T.auto_roll_day()
    path = corrupt_file()
    r = T.load_state(path)
    ok, why = T.auto_can_trade("XUSDT")
    if ok:
        return "狀態檔讀不到，照樣可以開新倉（以為自己空手）"
    ex.mark["XUSDT"] = 100.4
    n0 = len(ex.calls)
    T.open_position("X", "LONG", 100.0, None, stop_pct=5)
    if entry_sent(ex, n0):
        return "狀態檔讀不到，手動開倉照樣送單"
    if not any(f.startswith("state.json.bad-") for f in os.listdir(os.path.dirname(path))):
        return "壞檔沒有另存一份"
    if not [a for a in T.drain_alerts() if a["title"].startswith("⚠ 狀態檔讀不到")]:
        return "讀不到沒有推播"
    if not (isinstance(r, tuple) and r[0] is False):
        return f"load_state 的回傳分不出「讀取失敗」與「沒有資料」：{r}"


@case("8-ah2", "狀態檔不存在（第一次啟動）：正常開始，不能被當成讀取失敗")
def _():
    ex, clock = fresh(keep_save=True)
    T.AUTO["on"] = True
    T.auto_roll_day()
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    need(not os.path.exists(path), "檔案存在（前提）")
    T.load_state(path)
    ok, why = T.auto_can_trade("XUSDT")
    if not ok:
        return f"第一次啟動被當成讀取失敗：{why}"


@case("8-ah3", "讀取失敗期間不覆寫原檔；人工修好之後每輪重試讀到、恢復開新倉並通知")
def _():
    ex, clock = fresh(keep_save=True)
    T.AUTO["on"] = True
    T.auto_roll_day()
    path = corrupt_file()
    bad = open(path, encoding="utf-8").read()
    T.load_state(path)
    T.save_state()
    if open(path, encoding="utf-8").read() != bad:
        return "讀取失敗期間存檔把原檔蓋掉了（證據沒了，下一次重試會讀到空白）"
    with open(path, "w", encoding="utf-8") as f:                      # 人工修好
        json.dump({"state": {"positions": {}, "trades": [], "pending": {}}}, f)
    retry = getattr(T, "retry_load_state", None)
    if retry is None:
        return "沒有每輪重試讀取的機制（retry_load_state）"
    T.drain_alerts()
    retry()
    ok, why = T.auto_can_trade("XUSDT")
    if not ok:
        return f"修好之後沒有恢復開新倉：{why}"
    if len(titled(T.drain_alerts(), "狀態檔已讀到，恢復開新倉")) != 1:
        return "恢復時沒有通知"


@case("8-ai", "存檔寫到一半出錯：原檔要完好（先寫暫存檔再換名）")
def _():
    ex, clock = fresh(keep_save=True)
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    T.STATE_FILE = path
    T.save_state()
    need(os.path.exists(path), "第一次存檔沒有寫出檔案（前提）")
    good = open(path, encoding="utf-8").read()
    real_dump = T.json.dump

    def half_dump(obj, f, *a, **k):
        f.write('{"state": {"posi')
        raise OSError("磁碟滿了")
    T.json.dump = half_dump
    try:
        T.save_state()
    finally:
        T.json.dump = real_dump
    if open(path, encoding="utf-8").read() != good:
        return "存檔寫到一半出錯，原檔被寫壞了"


@case("8-aj", "存檔失敗、推播也失敗，在監控鎖與交易所規格鎖裡面發生：不能死鎖（執行緒＋逾時）")
def _():
    ex, clock = fresh(keep_save=True)
    import main as M
    importlib.reload(M)
    M.trader = T
    M.notify_telegram = lambda *a: (_ for _ in ()).throw(RuntimeError("推播掛了"))
    T.STATE_FILE = os.path.join(tempfile.mkdtemp(), "不存在的資料夾", "state.json")
    done = []

    def run():
        with M._mon_lock, T._lock:
            T.save_state()
            for a in T.drain_alerts():
                M.push_all(a["title"], a["text"])
        done.append(1)
    buf, old = io.StringIO(), sys.stderr
    sys.stderr = buf
    try:
        th = threading.Thread(target=run, daemon=True)
        th.start()
        th.join(5)
    finally:
        sys.stderr = old
    if not done:
        return "5 秒內沒有結束：存檔失敗的通知路徑死鎖"


@case("8-ak", "成交明細超過一頁：要分頁拿完，不能只用第一頁算出「確定的」錯數字")
def _():
    ex, clock = fresh("hedge")
    T._mode.update({"hedge": True, "ts": clock.now})
    T.FILLS_PAGE = 50                                         # 讓分頁真的發生（舊版沒有這個常數，設了也無害）
    r = open_long(ex)
    for i in range(150):                                      # 別的專案同幣開空的成交，塞在界線之後
        ex._fill("XUSDT", "SELL", "SHORT", 0.1, 90.0 + i * 0.01)
    ex.trigger("XUSDT", "LONG", r["qty"], 97.0)
    need(len([f for f in ex.fills if f["time"] >= 0]) > 150, "成交筆數不夠多（前提）")
    T.sync_positions()
    need(T.STATE["trades"] and T.STATE["trades"][-1].get("symbol") == "XUSDT", "沒有結帳（前提）")
    t = T.STATE["trades"][-1]
    if t.get("exit") is None or abs(float(t["exit"]) - 97.0) > 1e-9:
        return f"出場價 {t.get('exit')}，應為 97.0（只查一頁，平倉那筆掉出去了）"


@case("8-al", "推播失敗不能靜靜吞掉（以前 except: pass）")
def _():
    ex, clock = fresh()
    import main as M
    importlib.reload(M)
    M.notify_telegram = lambda *a: (_ for _ in ()).throw(RuntimeError("推播掛了"))
    buf, old = io.StringIO(), sys.stderr
    sys.stderr = buf
    try:
        M.push_all("測試", "內容")
    finally:
        sys.stderr = old
    if "推播失敗" not in buf.getvalue():
        return "推播失敗完全沒有訊息"


@case("8-am", "自檢：持倉紀錄還沒載入時講明「無法對帳」")
def _():
    ex, clock = fresh(keep_save=True)
    T.load_state(corrupt_file())
    import preflight
    importlib.reload(preflight)
    res = {r["item"]: r for r in preflight.check()}
    need(res, "自檢沒有結果（前提）")
    item = res.get("持倉紀錄")
    if not item or item["status"] != "fail" or "無法對帳" not in item["msg"]:
        return f"自檢沒有講明持倉紀錄還沒載入：{item}"


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✓' if not err else '✕'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
