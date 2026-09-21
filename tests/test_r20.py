"""BINANCE_LESSONS r18 → r20 逐段差異的檢查項目（crypto-screener 從 r18 回來，含 r19、r20）。

否定句斷言（「不能掛」「沒有撤」）程式什麼都沒做也會成立（用法第 5 點 r19），所以一律附：
  - 前提：要測的那一步真的走到了（守衛真的跑滿三輪、移損真的到了門檻）
  - 對照組：同樣的設定、只拿掉被測的條件時，程式確實會做那件事（用法第 5 點 r19）
斷言只看「這一步之後新發生的事」（測錯方式第 13 種）；每個情境都從 fresh() 開始（第 14 種）。

    python3 -m tests.test_r20
"""
import os
import sys
import importlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, ROOT)
import trader as T                                              # noqa: E402
from tests.fake_exchange import Ex17, Clock, need, entry_sent, injected_at_step, titled  # noqa: E402

RESULTS = []
from tests.harness import make_case                          # noqa: E402


case = make_case(RESULTS)                                    # 共用框架（tests/harness.py）


def fresh(mode="oneway"):
    importlib.reload(T)
    clock = Clock()
    T.time = clock
    T.save_state = lambda: None
    T.CFG.update({"key": "k", "secret": "s", "dryRun": False, "leverage": 3, "riskPct": 0.5,
                  "breakevenR": 1.0, "maxPositions": 8})
    for s in ("XUSDT", "YUSDT", "BADUSDT"):
        T._filters[s] = {"status": "TRADING", "tick": 0.01, "step": 0.1, "minQty": 0.1, "minNotional": 5}
    T._filters_ts = 9e18
    T._algo_supported[0] = True
    ex = Ex17(mode)
    T._request_raw = ex
    return ex, clock


def open_long(ex, sym="X", mark=100.4):
    ex.mark[sym + "USDT"] = mark
    n0 = len(ex.calls)
    r = T.open_position(sym, "LONG", 100.0, None, stop_pct=5)
    need(entry_sent(ex, n0), f"進場單沒有送出：{r.get('error')}")
    need(sym + "USDT" in T.STATE["positions"], "進場後帳上沒有部位")
    return r


def stops_sent(ex, since):
    return [c[2] for c in ex.calls[since:]
            if c[1] in ("/fapi/v1/algoOrder", "/fapi/v1/order") and c[0] == "POST" and c[2].get("type") == "STOP_MARKET"]


def drop_stop(ex):
    for o in list(ex.algo):
        if ex.algo[o].get("type") == "STOP_MARKET":
            ex.algo.pop(o)


# ════ 第 2 條 r19、r20：任何「掛一張新停損」前都要確認部位（扣基準）════

@case("2-f", "守衛補掛前確認部位：部位已被平掉（對帳還沒偵測到）→ 不補掛；對照組：部位還在 → 補掛")
def _():
    ex, clock = fresh()
    open_long(ex)
    drop_stop(ex)
    ex.pos[("XUSDT", "LONG")] = [0, 0]                        # 已被平掉，但帳上還在
    n0 = len(ex.calls)
    evs = []
    for _ in range(4):
        evs += T.guard_positions()
    need(sum(1 for c in ex.calls[n0:] if c[1] == "/fapi/v1/openAlgoOrders") >= 4, "守衛沒有跑滿 4 輪")
    # 第 16 種：「沒補」要是因為確認的結果是「沒了」，不是「查不到」
    need(any(e.get("action") == "skip" and e.get("why") == "gone" for e in evs), f"確認的結果不是 gone：{evs}")
    if stops_sent(ex, n0):
        return "部位已被平掉，守衛仍補掛了停損（孤兒 reduce-only 單）"
    # 對照組
    ex2, _c = fresh()
    open_long(ex2)
    drop_stop(ex2)
    m0 = len(ex2.calls)
    for _ in range(4):
        T.guard_positions()
    need(stops_sent(ex2, m0), "對照組：部位還在時守衛也沒補掛，上面的「沒補」可能是別的原因")


@case("2-f2", "守衛補掛前查部位查不到：這輪不動、不算補掛失敗")
def _():
    ex, clock = fresh()
    open_long(ex)
    drop_stop(ex)
    for _ in range(2):
        T.guard_positions()
    T.drain_alerts()
    ex.symbol_empty = 1                                       # 第三輪要補掛時，確認查詢回空清單
    n0 = len(ex.calls)
    evs = T.guard_positions()
    ok_, why_ = injected_at_step(ex, n0)
    need(ok_, f"注入沒有打在補掛前的確認上（第 18 種）：{why_}")
    need(any(c[1] == "/fapi/v2/positionRisk" and c[2].get("symbol") for c in ex.calls[n0:]),
         "守衛補掛前沒有查部位")
    need(any(e.get("action") == "skip" and e.get("why") == "unknown" for e in evs), f"確認的結果不是 unknown：{evs}")
    if stops_sent(ex, n0):
        return "查不到部位仍補掛"
    if T._replace_fails.get("XUSDT"):
        return f"查不到被算成補掛失敗（{T._replace_fails.get('XUSDT')} 次）"


@case("2-g", "移損掛新停損前確認部位：已被平掉 → 不撤舊、不掛新；對照組：部位還在 → 移損")
def _():
    ex, clock = fresh()
    open_long(ex)
    p = T.STATE["positions"]["XUSDT"]
    ex.pos[("XUSDT", "LONG")] = [0, 0]
    n0 = len(ex.calls)
    ev = T.move_to_breakeven(p, 106.0)
    need(any(c[1] == "/fapi/v2/positionRisk" and c[2].get("symbol") for c in ex.calls[n0:]),
         "移損前沒有查部位")
    need(ev and ev.get("skipped") == "gone", f"確認的結果不是 gone：{ev}")
    if stops_sent(ex, n0):
        return "部位已被平掉，移損仍掛了新停損（孤兒單）"
    if any(c[0] == "DELETE" for c in ex.calls[n0:]):
        return "部位已被平掉，移損仍撤了舊停損（交給對帳即可）"
    ex2, _c = fresh()
    open_long(ex2)
    p2 = T.STATE["positions"]["XUSDT"]
    m0 = len(ex2.calls)
    ev = T.move_to_breakeven(p2, 106.0)
    need(ev and ev.get("ok") and stops_sent(ex2, m0), f"對照組：部位還在時也沒移損：{ev}")


@case("2-g2", "移損前查部位查不到：不撤舊、不掛新、不算失敗")
def _():
    ex, clock = fresh()
    open_long(ex)
    p = T.STATE["positions"]["XUSDT"]
    ex.symbol_empty = 1
    n0 = len(ex.calls)
    ev = T.move_to_breakeven(p, 106.0)
    ok_, why_ = injected_at_step(ex, n0)
    need(ok_, f"注入沒有打在移損前的確認上（第 18 種）：{why_}")
    need(any(c[1] == "/fapi/v2/positionRisk" and c[2].get("symbol") for c in ex.calls[n0:]),
         "移損前沒有查部位")
    need(ev and ev.get("skipped") == "unknown", f"確認的結果不是 unknown：{ev}")
    if any(c[0] == "DELETE" for c in ex.calls[n0:]) or stops_sent(ex, n0):
        return "查不到部位仍動了停損"
    if p.get("beFails"):
        return f"查不到被算成移損失敗（{p['beFails']} 次）"


@case("2-h", "有基準部位時，停損用自己的數量（reduce-only），不能用 closePosition 平掉整側")
def _():
    ex, clock = fresh()
    ex.pos[("XUSDT", "LONG")] = [2.0, 90.0]                   # 同側別人的部位
    n0 = len(ex.calls)
    r = open_long(ex)
    own = r["sizing"]["qty"]
    first = stops_sent(ex, n0)
    need(first, "第一次停損沒有送出")
    bad = [s for s in first if s.get("closePosition")]
    if bad:
        return "第一次掛的停損用 closePosition，觸發時會把同側別人的 2.0 一起平掉"
    if abs(float(first[0].get("quantity") or 0) - own) > 1e-9:
        return f"停損數量 {first[0].get('quantity')}，應為自己的 {own}"
    # 守衛補掛也一樣
    drop_stop(ex)
    m0 = len(ex.calls)
    for _ in range(4):
        T.guard_positions()
    again = stops_sent(ex, m0)
    need(again, "守衛沒有補掛（前提）")
    if any(s.get("closePosition") for s in again):
        return "守衛補掛的停損用 closePosition"


@case("2-i", "自檢遇全量部位表空清單：不能把帳上的部位報成「交易所沒有」、不能把它的停損列成孤兒單")
def _():
    ex, clock = fresh()
    open_long(ex)
    ex.full_missing = True
    import preflight
    importlib.reload(preflight)
    res = preflight.check()
    need(any(r["item"] == "查持倉" for r in res), "自檢沒有查持倉")
    txt = " ".join(r["msg"] for r in res)
    orphans = [o for r in res for o in (r.get("orphans") or [])]
    if any(o["symbol"] == "XUSDT" for o in orphans):
        return "空清單時把自己部位的停損列成孤兒單（旁邊有撤單按鈕）"
    if "帳上有" in txt and "XUSDT" in txt and "交易所沒有" in txt:
        return "空清單時把帳上部位報成「交易所沒有」"


# ════ 第 8 條 r19、r20：同一部位各步各自 try、出錯次數清掉、維護出錯出場判斷照跑 ════

@case("8-l", "移損：一個部位丟例外，其他部位照樣移損（出錯的排在前面）", allow=("BADUSDT",))
def _():
    ex, clock = fresh()
    open_long(ex)
    good = T.STATE["positions"].pop("XUSDT")
    T.STATE["positions"]["BADUSDT"] = {"symbol": "BADUSDT", "side": "LONG", "qty": 1, "entry": 100,
                                       "exits": {"breakeven": "壞掉的資料"}}   # 型別錯，比較時會丟 TypeError
    T.STATE["positions"]["XUSDT"] = good
    ex.pos[("BADUSDT", "LONG")] = [1.0, 100.0]                # 修正：交易所上也要有，移損才會真的處理到它
    ex.mark["BADUSDT"] = 106.0
    ex.mark["XUSDT"] = 106.0
    n0 = len(ex.calls)
    try:
        T.manage_positions()
    except Exception:
        pass
    need(any(c[1] == "/fapi/v2/positionRisk" for c in ex.calls[n0:]), "移損沒有跑")
    if not stops_sent(ex, n0):
        return "一個部位丟例外，排在後面的部位沒有移損"
    me = titled(T.drain_alerts(), "⚠ 移損出錯（第 1 次）")
    if len(me) != 1 or "BADUSDT" not in me[0]["text"]:
        return "出錯的部位沒有告警（第 14 條）"


@case("8-l2", "待平倉重試：一個部位丟例外，其他部位照樣重試", allow=("BADUSDT",))
def _():
    ex, clock = fresh()
    open_long(ex)
    good = T.STATE["positions"].pop("XUSDT")
    good["pendingClose"] = {"reason": "手動平倉", "attempts": 1, "lastErr": "x", "since": 0}
    T.STATE["positions"]["BADUSDT"] = {"symbol": "BADUSDT", "pendingClose": {"reason": "x", "attempts": 1}}  # 缺欄位
    T.STATE["positions"]["XUSDT"] = good
    n0 = len(ex.calls)
    raised = []
    try:
        T.retry_pending_closes()
    except Exception as e:
        raised.append(e)
    need(raised or any(k[1] == "BADUSDT" for k in getattr(T, "_pos_errors", {})),
         "出錯的部位其實沒有丟例外（前提：要測的是它丟例外之後）")
    if not any(c[1] == "/fapi/v1/order" and c[2].get("type") == "MARKET" and c[2].get("reduceOnly")
               for c in ex.calls[n0:]):
        return "一個部位丟例外，排在後面的待平倉沒有重試"


@case("8-m", "守衛出錯次數跟著部位結束清掉，並發收尾通知", allow=("XUSDT",))
def _():
    ex, clock = fresh()
    open_long(ex)
    orig = T._guard_one
    T._guard_one = lambda sym, pos, events: (_ for _ in ()).throw(RuntimeError("檢查壞了"))
    T.guard_positions()
    T._guard_one = orig
    need(T._guard_errors.get("XUSDT") == 1, "守衛出錯沒有被計數（前提）")
    T.drain_alerts()
    ex.pos[("XUSDT", "LONG")] = [0, 0]
    T.sync_positions()
    need("XUSDT" not in T.STATE["positions"], "部位沒有結束（前提）")
    if T._guard_errors.get("XUSDT"):
        return "部位結束後守衛出錯次數還留著，同幣下一筆會接著數"
    end = titled(T.drain_alerts(), "出錯狀態結束：部位已平倉")
    if len(end) != 1 or "XUSDT" not in end[0]["text"]:
        return "出錯狀態隨部位結束消失，沒有收尾通知"


def _main():
    import main as M
    importlib.reload(M)
    M.trader = T
    pushed = []
    M.push_all = lambda title, text: pushed.append((title, text))
    return M, pushed


@case("8-m2", "背景迴圈步驟出錯次數：恢復時通知並歸零")
def _():
    ex, clock = fresh()
    M, pushed = _main()
    open_long(ex)
    real = T.manage_positions
    T.manage_positions = lambda: (_ for _ in ()).throw(RuntimeError("移損壞了"))
    M.position_round(20)
    need(M._step_errors.get("移損") == 1, "步驟出錯沒有被計數（前提）")
    T.manage_positions = real
    pushed.clear()
    M.position_round(20)
    if M._step_errors.get("移損"):
        return f"步驟恢復後出錯次數沒歸零（{M._step_errors.get('移損')}）"
    if len(titled(pushed, "部位監看［移損］恢復")) != 1:
        return f"步驟恢復時沒有通知：{[t for t, _ in pushed]}"


@case("8-n", "維護步驟出錯（平倉通知丟例外）：同一輪的守衛與待平倉重試照樣跑")
def _():
    ex, clock = fresh()
    M, pushed = _main()
    open_long(ex, "X")
    open_long(ex, "Y")
    T.STATE["positions"]["YUSDT"]["pendingClose"] = {"reason": "手動平倉", "attempts": 1, "lastErr": "x", "since": 0}
    drop_stop(ex)
    ex.pos[("XUSDT", "LONG")] = [0, 0]                        # X 被平掉 → 對帳會記平倉並發通知
    M.notify_trade_close = lambda t: (_ for _ in ()).throw(RuntimeError("通知模組壞了"))
    n0 = len(ex.calls)
    for _ in range(4):
        M.position_round(20)
    need(any(t.get("symbol") == "XUSDT" for t in T.STATE["trades"]), "對帳沒有記到 X 的平倉（前提）")
    if not any(c[1] == "/fapi/v1/order" and c[2].get("type") == "MARKET" and c[2].get("reduceOnly")
               and c[2].get("symbol") == "YUSDT" for c in ex.calls[n0:]):
        return "平倉通知丟例外，這一輪的待平倉重試（出場）沒有跑"
    if not any(c[1] == "/fapi/v1/openAlgoOrders" for c in ex.calls[n0:]):
        return "平倉通知丟例外，守衛沒有跑"


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✕' if err else '✓'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
