"""BINANCE_LESSONS r27 → r29 逐段差異的檢查項目（crypto-screener 從 r27 回來，含 r28、r29）。

    python3 -m tests.test_r29
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


def _main():
    import main as M
    importlib.reload(M)
    M.trader = T
    pushed = []
    M.push_all = lambda title, text: pushed.append((title, text))
    return M, pushed


# ════ 第 2 條 r29：回傳原因的語法樹檢查 ══════════════════════

@case("2-l", "回傳原因檢查：明寫 return None 算不帶值、沒有 else 的 if 算掉出去；每個函式至少解析到 2 個 return")
def _():
    ex, clock = fresh()                                       # 全域檢查自成情境
    import subprocess
    out = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "check_returns.py")],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    need("組人造函式自我驗證通過" in out, f"檢查器沒有真的跑完：{out[-200:]}")
    if "每個函式至少 2 個 return" not in out:
        need(out.strip(), "要取的清單是空的（前提，r35：先確認有東西再索引）")
        return f"檢查器沒有「每個函式解析到多個 return」的前提（第 19 種）：{out.strip().splitlines()[-1]}"
    if "問題 0 項" not in out:
        need(out.strip(), "要取的清單是空的（前提，r35：先確認有東西再索引）")
        return out.strip().splitlines()[-1]


# ════ 第 8 條 r28：估算不能把未知包裝成已知 ═════════════════

@case("8-u", "手動平倉：出場價用這張單的實際成交均價，不是標記價")
def _():
    ex, clock = fresh()
    open_long(ex)
    ex.mark["XUSDT"] = 104.0
    n0 = len(ex.calls)
    r = T.close_position("XUSDT")
    need(r.get("ok") and any(c[2].get("type") == "MARKET" and c[2].get("reduceOnly") for c in ex.calls[n0:]),
         f"平倉沒有完成（前提）：{r}")
    need(ex.fills, "要取的清單是空的（前提，r35：先確認有東西再索引）")
    fill = float(ex.fills[-1]["price"])
    need(abs(fill - 104.0) > 1e-9, "模擬成交價跟標記價一樣，分不出來（前提）")
    need(T.STATE["trades"], "要取的清單是空的（前提，r35：先確認有東西再索引）")
    t = T.STATE["trades"][-1]
    if t.get("exit") is None or abs(float(t["exit"]) - fill) > 1e-9:
        return f"出場價 {t.get('exit')}，應為實際成交均價 {fill}（標記價是 104.0）"


@case("8-u2", "交易所端停損觸發：出場價從成交明細讀，不是偵測當下的標記價")
def _():
    ex, clock = fresh()
    r = open_long(ex)
    ex.mark["XUSDT"] = 97.0                                   # 偵測當下的標記價
    ex.trigger("XUSDT", "LONG", r["qty"], 95.3)               # 實際在 95.3 成交
    T.sync_positions()
    need(any(t.get("symbol") == "XUSDT" for t in T.STATE["trades"]), "沒有結帳（前提）")
    t = T.STATE["trades"][-1]
    if t.get("exit") is None or abs(float(t["exit"]) - 95.3) > 1e-9:
        return f"出場價 {t.get('exit')}，應為成交明細的 95.3（標記價 97.0）"


@case("8-u3", "成交明細查不到：出場價與損益記為未知，不能用標記價或進場價補（補進場價＝損益剛好 0）")
def _():
    ex, clock = fresh()
    r = open_long(ex)
    ex.trades_fail = True
    ex.trigger("XUSDT", "LONG", r["qty"], 95.3)
    T.drain_alerts()
    T.sync_positions()
    need(any(t.get("symbol") == "XUSDT" for t in T.STATE["trades"]), "沒有結帳（前提）")
    t = T.STATE["trades"][-1]
    if t.get("pnl") is not None or t.get("exit") is not None:
        return f"查不到成交價，卻記了出場價 {t.get('exit')}、損益 {t.get('pnl')}（假的已知）"
    if any(a["title"].startswith("⚠ ") and "出錯" in a["title"] for a in T.drain_alerts()):
        return "查不到成交價是正常的「未知」，不該當成程式出錯告警"


@case("8-u4", "部分出場：用成交明細的價格；查不到時那一段記未知，最後整筆損益也是未知（不能當 0 加總）")
def _():
    ex, clock = fresh()
    r = open_long(ex)
    p = T.STATE["positions"]["XUSDT"]
    tp = next(o for o in p["orders"] if o["type"] == "TAKE_PROFIT_MARKET")
    half = float(ex.algo[tp["id"]]["quantity"])
    ex.algo.pop(tp["id"])
    ex.trigger("XUSDT", "LONG", half, 110.7)                  # 停利實際在 110.7 成交（觸發價另有其值）
    T.sync_positions()
    part = (T.STATE["positions"]["XUSDT"].get("partials") or [None])[-1]
    need(part is not None, "沒有偵測到部分出場（前提）")
    if part.get("px") is None or abs(float(part["px"]) - 110.7) > 1e-9:
        return f"部分出場價 {part.get('px')}，應為成交明細的 110.7（不是停利觸發價或標記價）"
    # 第二筆：查不到成交明細
    ex2, _c = fresh()
    r2 = open_long(ex2)
    p2 = T.STATE["positions"]["XUSDT"]
    tp2 = next(o for o in p2["orders"] if o["type"] == "TAKE_PROFIT_MARKET")
    half2 = float(ex2.algo[tp2["id"]]["quantity"])
    ex2.algo.pop(tp2["id"])
    ex2.trades_fail = True
    ex2.trigger("XUSDT", "LONG", half2, 110.7)
    T.sync_positions()
    part2 = (T.STATE["positions"]["XUSDT"].get("partials") or [None])[-1]
    need(part2 is not None, "沒有偵測到部分出場（前提）")
    if part2.get("pnl") is not None:
        return f"查不到成交價，部分出場卻記了損益 {part2.get('pnl')}"
    ex2.trades_fail = False
    ex2.trigger("XUSDT", "LONG", r2["qty"] - half2, 101.0)
    T.sync_positions()
    need(T.STATE["trades"], "要取的清單是空的（前提，r35：先確認有東西再索引）")
    t = T.STATE["trades"][-1]
    need(t.get("symbol") == "XUSDT", "沒有結帳（前提）")
    if t.get("pnl") is not None:
        return f"有一段損益未知，整筆卻記了 {t.get('pnl')}（未知那段被當成 0 加總）"


# ════ 第 8 條 r29：風控遇到損益未知 ═════════════════════════

@case("8-w", "損益未知：不算進每日虧損，但筆數放進風控狀態；冷卻訊息不能寫「剛虧損」")
def _():
    ex, clock = fresh()
    T.AUTO["on"] = True
    T.auto_roll_day()
    r = open_long(ex)
    ex.trades_fail = True
    ex.trigger("XUSDT", "LONG", r["qty"], 95.3)
    before = T.AUTO["closedR"]
    T.sync_positions()
    need(T.STATE["trades"] and T.STATE["trades"][-1].get("pnl") is None, "這筆沒有記成損益未知（前提）")
    if T.AUTO["closedR"] != before:
        return f"未知的損益被加進了每日已實現（{before} → {T.AUTO['closedR']}）"
    if T.AUTO.get("unknownToday") != 1:
        return f"風控狀態沒有記下未知筆數：{T.AUTO.get('unknownToday')}"
    ok, why = T.auto_can_trade("XUSDT")
    need(not ok, "冷卻沒有生效（前提）")
    if "虧損" in why:
        return f"損益未知卻寫成虧損：{why}"


# ════ 第 8 條 r28：or 預設 用在時間戳 ═══════════════════════

@case("8-x", "自檢：pending 的時間戳是 0 時要算成很久以前（逾時），不能被 or 換成「現在」而藏起來")
def _():
    ex, clock = fresh()
    T.STATE["pending"]["XUSDT"] = {"side": "LONG", "qty": 1.0, "ts": 0}
    import preflight
    importlib.reload(preflight)
    res = {r["item"]: r for r in preflight.check()}
    need("未認領的送單" in res, "自檢沒有檢查 pending（前提）")
    if res["未認領的送單"]["status"] == "ok":
        return f"時間戳 0 的 pending 被當成剛送出：{res['未認領的送單']['msg']}"


# ════ 用法第 5 點 r28、r29 ═════════════════════════════════

@case("20-a", "平倉通知的格式化真的執行了（前提：沒被 mock 掉）", allow=())
def _():
    ex, clock = fresh()
    M, pushed = _main()
    ran = []
    real = M.notify_trade_close

    def spy(t):
        ran.append(t)
        return real(t)
    M.notify_trade_close = spy
    r = open_long(ex)
    ex.trigger("XUSDT", "LONG", r["qty"], 95.3)
    M.position_round(20)
    need(ran, "平倉通知的格式化函式根本沒執行（第 20 種）")
    if len(titled(pushed, "虧損平倉")) != 1:
        return f"通知沒有送出：{[t for t, _ in pushed]}"


@case("29-a", "錯誤掃描攔得到不同模組寫的錯誤（至少 trader、main 兩個）", infra=True)
def _():
    ex, clock = fresh()
    from tests.harness import selftest_modules
    got = selftest_modules()
    need(isinstance(got, set), "自我驗證沒有回傳攔到的模組（前提）")
    if not {"trader", "main"} <= got:
        return f"只攔到 {sorted(got)}，至少要 trader 與 main"


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✓' if not err else '✕'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
