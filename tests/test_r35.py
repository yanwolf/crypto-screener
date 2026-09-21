"""BINANCE_LESSONS r33 → r35 逐段差異的檢查項目（crypto-screener 從 r33 回來，含 r34、r35）。

    python3 -m tests.test_r35
"""
import os
import sys
import json
import tempfile
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


# ════ 第 8 條 r34：認領時缺值帶進界線；起始界線不用開倉時間 ════

@case("8-ab", "認領時 pending 時間戳是 0：界線不能退成 0，這個幣歷史上的平倉成交不能被算成這筆的出場")
def _():
    ex, clock = fresh()
    ex._fill("XUSDT", "SELL", "BOTH", 1.0, 50.0, realized=-5.0)   # 很久以前另一筆部位的平倉成交
    T.STATE["pending"]["XUSDT"] = {"side": "LONG", "qty": 1.0, "stopPct": 5, "note": "", "base": 0.0,
                                   "params": {k: T.CFG.get(k) for k in T.PARAM_KEYS}, "ts": 0}
    ex.pos[("XUSDT", "LONG")] = [1.0, 100.2]
    T.sync_positions()
    need("XUSDT" in T.STATE["positions"], "沒有認領（前提）")
    ex.trigger("XUSDT", "LONG", 1.0, 95.0)
    T.sync_positions()
    need(T.STATE["trades"] and T.STATE["trades"][-1].get("symbol") == "XUSDT", "沒有結帳（前提）")
    t = T.STATE["trades"][-1]
    if t.get("exit") is not None and abs(float(t["exit"]) - 95.0) > 1e-9:
        return f"出場價 {t.get('exit')}：把歷史上 50.0 那筆平倉成交也算進來了（界線退成 0）"
    if t.get("exit") is None:
        return "界線應在認領當下記下，出場價應為 95.0，實際記成未知"


@case("8-ac", "交易所時鐘比本機慢 10 秒：界線在開倉當下用成交 id 記，平倉成交不能因時間戳早於「開倉時間」被篩掉")
def _():
    ex, clock = fresh()
    ex.clock_lag_ms = 10000
    r = open_long(ex)
    p = T.STATE["positions"]["XUSDT"]
    boundary = p.get("fillsFromId")
    ex.trigger("XUSDT", "LONG", r["qty"], 96.0)
    need(ex.fills and ex.fills[-1]["time"] < p["opened"], "模擬的平倉成交時間戳沒有早於開倉時間（前提）")
    T.sync_positions()
    need(T.STATE["trades"] and T.STATE["trades"][-1].get("symbol") == "XUSDT", "沒有結帳（前提）")
    t = T.STATE["trades"][-1]
    if t.get("exit") is None or abs(float(t["exit"]) - 96.0) > 1e-9:
        return f"出場價 {t.get('exit')}，應為 96.0（用開倉時間當界線時被篩掉）"
    if not isinstance(boundary, int):
        return f"開倉當下沒有記下成交 id 界線：{boundary}"


@case("8-ad", "雙向模式：別的專案同幣開空（也是 SELL）不能被算成自己多單的平倉")
def _():
    ex, clock = fresh("hedge")
    T._mode.update({"hedge": True, "ts": clock.now})
    r = open_long(ex)
    ex._fill("XUSDT", "SELL", "SHORT", r["qty"], 80.0)         # 別的專案開空
    ex.trigger("XUSDT", "LONG", r["qty"], 97.0)                # 自己的多單被停損
    need(len(ex.fills) >= 2 and ex.fills[-1]["positionSide"] == "LONG" and ex.fills[-2]["positionSide"] == "SHORT",
         "模擬成交不對（前提）")
    T.sync_positions()
    need(T.STATE["trades"] and T.STATE["trades"][-1].get("symbol") == "XUSDT", "沒有結帳（前提）")
    t = T.STATE["trades"][-1]
    if t.get("exit") is None or abs(float(t["exit"]) - 97.0) > 1e-9:
        return f"出場價 {t.get('exit')}，應為 97.0（把別人 80.0 的開空算進來了）"


# ════ 第 8 條 r35：只在記憶體的期限、界線要存進資料庫 ══════════

@case("8-ae", "服務重啟：開倉當下記的界線要存進狀態檔；重啟後照樣用它算出場價")
def _():
    ex, clock = fresh(keep_save=True)
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    T.STATE_FILE = path
    ex.clock_lag_ms = 10000
    r = open_long(ex)
    saved = json.load(open(path, encoding="utf-8"))["state"]["positions"].get("XUSDT") or {}
    need(saved, "狀態檔裡沒有這個部位（前提）")
    if not isinstance(saved.get("fillsFromId"), int):
        return f"界線沒有存進狀態檔：{saved.get('fillsFromId')}"
    fresh(ex=ex)                                              # 重啟：模組重新載入，記憶體清空
    T.load_state(path)
    need("XUSDT" in T.STATE["positions"], "重啟後沒有還原部位（前提）")
    ex.trigger("XUSDT", "LONG", r["qty"], 96.5)
    T.sync_positions()
    need(T.STATE["trades"] and T.STATE["trades"][-1].get("symbol") == "XUSDT", "沒有結帳（前提）")
    t = T.STATE["trades"][-1]
    if t.get("exit") is None or abs(float(t["exit"]) - 96.5) > 1e-9:
        return f"重啟後出場價 {t.get('exit')}，應為 96.5"


@case("8-af", "服務重啟：「不明而沒有期限」的 pending（時間戳缺值）重啟後要被確認，不能永遠卡著")
def _():
    ex, clock = fresh(keep_save=True)
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    T.STATE_FILE = path
    T.STATE["pending"]["XUSDT"] = {"side": "LONG", "qty": 1.0, "stopPct": 5, "note": "", "base": 0.0,
                                   "params": {k: T.CFG.get(k) for k in T.PARAM_KEYS}}     # 沒有 ts
    T.save_state()
    ex.pos[("XUSDT", "LONG")] = [1.0, 100.2]                   # 其實成交了
    fresh(ex=ex)
    T.load_state(path)
    need("XUSDT" in T.STATE["pending"], "重啟後沒有還原 pending（前提）")
    T.sync_positions()
    if "XUSDT" in T.STATE["pending"]:
        return "重啟後的 pending 沒有期限，永遠沒被確認"
    if "XUSDT" not in T.STATE["positions"]:
        return "交易所上有部位，卻沒有認領"


@case("8-ag", "存檔失敗不能沒有訊息（以前 except: pass）")
def _():
    ex, clock = fresh(keep_save=True)
    T.STATE_FILE = os.path.join(tempfile.mkdtemp(), "不存在的資料夾", "state.json")
    T.drain_alerts()
    T.save_state()
    al = T.drain_alerts()
    if len(titled(al, "⚠ 存檔出錯（第 1 次）")) != 1:
        return f"存檔失敗沒有告警：{[a['title'] for a in al]}"


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✓' if not err else '✕'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
