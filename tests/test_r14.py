"""BINANCE_LESSONS r12 → r14 逐段差異的檢查項目（crypto-screener 從 r12 回來，含 r13、r14）。

照用法第 5 點的 6 種測錯方式寫：
  1. 兩個來源刻意設不同值（訊號價 100、成交均價 100.4、交易所既有部位均價 90）
  2. 守衛至少跑 4 輪，確實碰到「連 3 輪」那一行
  3. 告警看出口（推播），不只看錯誤區
  4. 每一項都檢查「有沒有送單」，不只看結果碰巧對
  5. 時間用可控的時鐘，不用 0 這種會被 `or` 蓋掉的假值
  6. 告警要帶注入的錯誤內容；另外攔下程式寫到錯誤輸出的內容，確認沒有 NameError 之類的程式錯誤（第 14 條）

    python3 -m tests.test_r14
"""
import os
import sys
import importlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import trader as T                                              # noqa: E402
from tests.fake_exchange import Ex, Clock, need, entry_sent, titled  # noqa: E402

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
    T._filters.update({"XUSDT": {"status": "TRADING", "tick": 0.01, "step": 0.1, "minQty": 0.1, "minNotional": 5},
                       "YUSDT": {"status": "TRADING", "tick": 0.01, "step": 0.1, "minQty": 0.1, "minNotional": 5}})
    T._filters_ts = 9e18
    T._algo_supported[0] = True
    ex = Ex(mode)
    T._request_raw = ex
    return ex, clock


def alerts():
    return T.drain_alerts()


def market_calls(ex, reduce=None):
    out = [c for c in ex.calls if c[1] == "/fapi/v1/order" and c[2].get("type") == "MARKET"]
    if reduce is not None:
        out = [c for c in out if bool(c[2].get("reduceOnly")) == reduce]
    return out


def open_long(ex, mark=100.4):
    ex.mark["XUSDT"] = mark
    n0 = len(ex.calls)
    r = T.open_position("X", "LONG", 100.0, None, stop_pct=5)
    need(entry_sent(ex, n0), f"進場單沒有送出：{r.get('error')}")   # 前提（用法第 5 點 r17）
    return r


# ════ 第 1 條（r13）════════════════════════════════════════

@case("1-b", "退回舊端點要有期限，期限過了重新試 Algo")
def _():
    ex, clock = fresh()
    ex.algo_404, ex.legacy_accept = True, True
    st, _, via = T.place_conditional({"symbol": "XUSDT", "side": "SELL", "type": "STOP_MARKET", "stopPrice": 95.0,
                                      "workingType": "MARK_PRICE", **T._close_all("LONG")})
    if via != "legacy" or st != 200:
        return f"測試前提不成立：404 後應退回舊端點，實際 {via} HTTP {st}"
    ex.algo_404 = False
    clock.now += 11 * 60
    st, _, via = T.place_conditional({"symbol": "XUSDT", "side": "SELL", "type": "STOP_MARKET", "stopPrice": 95.0,
                                      "workingType": "MARK_PRICE", **T._close_all("LONG")})
    if via != "algo":
        return f"退回 11 分鐘後仍走 {via}，沒有重新試 Algo"


@case("1-c", "退回期間舊端點回 -4120：立刻改回 Algo 重送這一張")
def _():
    ex, clock = fresh()
    ex.algo_404, ex.legacy_accept = True, True
    T.place_conditional({"symbol": "XUSDT", "side": "SELL", "type": "STOP_MARKET", "stopPrice": 95.0,
                         "workingType": "MARK_PRICE", **T._close_all("LONG")})
    ex.algo_404, ex.legacy_accept = False, False              # 交易所說：要用 Algo
    n0 = len([c for c in ex.calls if c[1] == "/fapi/v1/algoOrder"])
    st, _, via = T.place_conditional({"symbol": "XUSDT", "side": "SELL", "type": "STOP_MARKET", "stopPrice": 95.0,
                                      "workingType": "MARK_PRICE", **T._close_all("LONG")})
    n1 = len([c for c in ex.calls if c[1] == "/fapi/v1/algoOrder"])
    if st != 200 or via != "algo" or n1 <= n0:
        return f"舊端點回 -4120 後沒有改回 Algo 重送：{via} HTTP {st}，Algo 送單 {n1 - n0} 次"


@case("1-d", "退回期間查停損要兩個端點都查：舊端點的停損不見時要補掛")
def _():
    ex, clock = fresh()
    ex.algo_404, ex.legacy_accept = True, True
    open_long(ex)
    p = T.STATE["positions"]["XUSDT"]
    if not any(o.get("via") == "legacy" and o["type"] == "STOP_MARKET" for o in p["orders"]):
        return "測試前提不成立：停損應掛在舊端點"
    ex.legacy.clear()                                         # 舊端點的停損被撤了
    alerts()
    n0 = len(ex.calls)                                        # 只算守衛開始之後（修正：原本會算到開倉時那張）
    for _ in range(4):
        T.guard_positions()
    replaced = [c for c in ex.calls[n0:] if c[1] == "/fapi/v1/order" and c[2].get("type") == "STOP_MARKET"]
    if not replaced:
        return "Algo 查詢 404 就整個跳過，舊端點的停損不見了也沒補掛"


@case("1-e", "退回期間舊端點的停損還在：不能誤判成不在而補掛")
def _():
    ex, clock = fresh()
    ex.algo_404, ex.legacy_accept = True, True
    open_long(ex)
    before = len([c for c in ex.calls if c[2].get("type") == "STOP_MARKET"])
    n0 = len(ex.calls)
    for _ in range(4):
        T.guard_positions()
    after = len([c for c in ex.calls if c[2].get("type") == "STOP_MARKET"])
    need(any(c[1] == "/fapi/v1/openOrders" for c in ex.calls[n0:]),
         "守衛沒有查舊端點（Algo 查詢失敗就整個跳過，等於沒檢查）")
    if after != before:
        return f"舊端點停損還在，卻補掛了 {after - before} 次"


# ════ 第 3 條（r13、r14）══════════════════════════════════

@case("3-a", "pending 沒看到部位不能 120 秒就丟；180 秒後才判定未成交並通知")
def _():
    ex, clock = fresh()
    T.STATE["pending"]["XUSDT"] = {"side": "LONG", "qty": 1.0, "stopPct": 5, "note": "", "params": {},
                                   "ts": int(clock.now * 1000)}
    clock.now += 150
    n0 = len(ex.calls)
    T.sync_positions()
    need(any(c[1] == "/fapi/v2/positionRisk" for c in ex.calls[n0:]), "對帳沒有跑（前提）")
    if "XUSDT" not in T.STATE["pending"]:
        return "150 秒時交易所還沒反映就把 pending 丟了"
    alerts()
    clock.now += 40
    T.sync_positions()
    a = alerts()
    if "XUSDT" in T.STATE["pending"]:
        return "190 秒仍未判定"
    nf = titled(a, "送出的進場單判定未成交")
    if len(nf) != 1 or "XUSDT" not in nf[0]["text"]:
        return f"判定未成交時沒有通知：{[x['title'] for x in a]}"


@case("3-b", "pending 期間同幣不能再下單，持倉數也要算 pending")
def _():
    ex, clock = fresh()
    T.AUTO["on"] = True
    T.auto_roll_day()
    ok0, why0 = T.auto_can_trade("XUSDT")
    need(ok0, f"對照組：沒有 pending 時本來就不能下單（{why0}），下面的「不能」可能是別的原因")
    T.STATE["pending"]["XUSDT"] = {"side": "LONG", "qty": 1.0, "ts": int(clock.now * 1000)}
    ok, why = T.auto_can_trade("XUSDT")
    if ok:
        return "pending 中的同一個幣仍可再下單"
    T.CFG["maxPositions"] = 1
    ok, why = T.auto_can_trade("YUSDT")
    if ok:
        return "持倉上限 1、已有 1 筆 pending，仍可開新幣"


@case("3-c", "認領時數量、均價取交易所那一列，並當場掛停損（兩個來源刻意不同值）")
def _():
    ex, clock = fresh()
    T.STATE["pending"]["XUSDT"] = {"side": "LONG", "qty": 1.0, "stop": 95.0, "stopPct": 5, "note": "",
                                   "params": {k: T.CFG.get(k) for k in T.PARAM_KEYS},
                                   "ts": int(clock.now * 1000)}
    ex.pos[("XUSDT", "LONG")] = [0.7, 101.3]
    clock.now += 40
    T.sync_positions()
    p = T.STATE["positions"].get("XUSDT")
    if not p:
        return "沒有認領"
    if abs(p["qty"] - 0.7) > 1e-9 or abs(p["entry"] - 101.3) > 1e-9:
        return f"數量／均價沒取交易所：{p['qty']} @ {p['entry']}"
    if not any(v.get("type") == "STOP_MARKET" for v in ex.algo.values()):
        return "認領後沒有當場掛停損"


@case("3-d", "送單前記下這一側原有數量：成交數量＝現在減基準，均價取這張單的成交價")
def _():
    ex, clock = fresh()
    ex.pos[("XUSDT", "LONG")] = [2.0, 90.0]                   # 送單前就有的同側部位（別的專案或孤兒倉）
    r = open_long(ex, mark=100.4)
    if not r.get("ok"):
        return f"開倉失敗：{r.get('error')}"
    p = T.STATE["positions"]["XUSDT"]
    if abs(p["qty"] - r["sizing"]["qty"]) > 1e-9:
        return f"帳上數量 {p['qty']} 含了送單前就有的 2.0（這張單只買 {r['sizing']['qty']}）"
    if abs(p["entry"] - 100.4) > 1e-9:
        return f"進場價 {p['entry']} 是合併均價，不是這張單的成交價 100.4"


@case("3-e", "有基準部位時認領：數量＝現在減基準，通知講明均價是合併過的")
def _():
    ex, clock = fresh()
    T.STATE["pending"]["XUSDT"] = {"side": "LONG", "qty": 1.0, "stopPct": 5, "note": "", "base": 2.0,
                                   "params": {k: T.CFG.get(k) for k in T.PARAM_KEYS},
                                   "ts": int(clock.now * 1000)}
    ex.pos[("XUSDT", "LONG")] = [3.0, 93.3]
    clock.now += 40
    alerts()
    T.sync_positions()
    p = T.STATE["positions"].get("XUSDT")
    if not p or abs(p["qty"] - 1.0) > 1e-9:
        return f"認領數量應為 3.0 − 基準 2.0 ＝ 1.0，實際 {p and p['qty']}"
    alerts()
    if not any("合併" in w for w in p.get("warnings") or []):          # 指定看這個部位自己的警告（第 21 種）
        return "有基準部位時沒講明均價是合併過的"


# ════ 第 7 條（r13）：手動平倉只認自己那一側 ═══════════════

@case("7-i", "自己那一側已經不在：回錯誤、不送單、不動帳上紀錄")
def _():
    ex, clock = fresh()
    open_long(ex)
    ex.pos[("XUSDT", "LONG")] = [0, 0]                        # 剛被停損
    n0 = len(market_calls(ex))
    c0 = len(ex.calls)
    r = T.close_position("XUSDT")
    need(any(c[1] == "/fapi/v2/positionRisk" and c[2].get("symbol") for c in ex.calls[c0:]),
         "平倉前沒有查部位（前提：「不送單」要是因為確認了那一側不在）")
    if len(market_calls(ex)) != n0:
        return "那一側已不在，仍送出了平倉單"
    if r.get("ok") or "XUSDT" not in T.STATE["positions"]:
        return "那一側已不在，卻記成手動平倉（應交給對帳）"


@case("7-j", "單向共用帳號：自己的已被停損、別人同側還有部位時，不能把別人的平掉")
def _():
    ex, clock = fresh()
    ex.pos[("XUSDT", "LONG")] = [2.0, 90.0]                   # 別的專案同側
    open_long(ex)
    q_mine = T.STATE["positions"]["XUSDT"]["qty"]
    ex.pos[("XUSDT", "LONG")][0] -= q_mine                    # 自己的被停損，剩別人的 2.0
    n0 = len(market_calls(ex))
    c0 = len(ex.calls)
    T.close_position("XUSDT")
    need(any(c[1] == "/fapi/v2/positionRisk" and c[2].get("symbol") for c in ex.calls[c0:]), "平倉前沒有查部位（前提）")
    if len(market_calls(ex)) != n0:
        return f"送出了平倉單，會平掉別人的部位（交易所剩 {ex.pos[('XUSDT', 'LONG')][0]}）"


# ════ 第 8 條（r13 補充、r14）═════════════════════════════

@case("8-a", "平倉被拒、再查部位也失敗：當成沒平掉")
def _():
    ex, clock = fresh()
    open_long(ex)
    ex.reject_market.add("XUSDT")
    ex.risk_fail_after_market = True
    n0 = len(ex.calls)
    r = T.close_position("XUSDT")
    need(any(c[2].get("type") == "MARKET" and c[2].get("reduceOnly") for c in ex.calls[n0:]), "平倉單沒有送出（前提）")
    need(any(c[1] == "/fapi/v2/positionRisk" for c in ex.calls[n0:][1:]), "送單後沒有再查部位（前提：要走的是「再查也失敗」）")
    if r.get("ok") or "XUSDT" not in T.STATE["positions"]:
        return "查不到部位被當成已經沒了"


@case("8-b", "平倉回應逾時但其實成交：記成已平倉，不發「平倉失敗」")
def _():
    ex, clock = fresh()
    open_long(ex)
    orig = ex.__call__

    def lost(m, p, params, s, t):
        st, d = orig(m, p, params, s, t)
        if p == "/fapi/v1/order" and params.get("type") == "MARKET" and params.get("reduceOnly"):
            return 0, {"error": "timed out"}
        return st, d
    T._request_raw = lost
    alerts()
    n0 = len(ex.calls)
    r = T.close_position("XUSDT")
    a = alerts()
    need(any(c[2].get("type") == "MARKET" and c[2].get("reduceOnly") for c in ex.calls[n0:]), "平倉單沒有送出（前提）")
    if not r.get("ok") or "XUSDT" in T.STATE["positions"]:
        return "成交了卻沒記成已平倉"
    if any(x["title"].startswith("⚠ 平倉失敗") for x in a):
        return f"成交了卻告警平倉失敗：{[x['title'] for x in a]}"


@case("8-d", "平倉沒平掉：記待平倉、每輪重試、照節奏告警，成功時結帳並發恢復")
def _():
    ex, clock = fresh()
    open_long(ex)
    ex.reject_market.add("XUSDT")
    alerts()
    T.close_position("XUSDT")
    p = T.STATE["positions"].get("XUSDT")
    if not p or not p.get("pendingClose"):
        return "平倉失敗沒有記待平倉旗標"
    titles = [x["title"] for x in alerts()]
    for _ in range(5):
        T.retry_pending_closes()
        titles += [x["title"] for x in alerts()]
    fails = [t for t in titles if t.startswith("⚠ 平倉失敗，部位仍在（第 ")]
    if "⚠ 平倉失敗，部位仍在（第 1 次）" not in fails or "⚠ 平倉失敗，部位仍在（第 5 次）" not in fails:
        return f"待平倉告警沒照節奏（第 1、5 次）：{titles}"
    ex.reject_market.discard("XUSDT")
    T.retry_pending_closes()
    a = alerts()
    if "XUSDT" in T.STATE["positions"]:
        return "交易所接受後仍沒結帳"
    done = titled(a, "已平倉")
    if len(done) != 1 or "已補上" not in done[0]["text"]:
        return f"平掉時沒發恢復：{[x['title'] for x in a]}"


@case("8-e", "移損時價格已穿過：送平倉單的那一刻，交易所上要有停損")
def _():
    ex, clock = fresh()
    open_long(ex)
    p = T.STATE["positions"]["XUSDT"]
    ex.reject_algo = lambda params: ((400, {"code": -2021, "msg": "Order would immediately trigger."})
                                     if float(params.get("triggerPrice") or 0) > 99.5 else None)
    seen = []
    orig = ex.__call__

    def spy(m, path, params, s, t):
        if path == "/fapi/v1/order" and (params or {}).get("type") == "MARKET" and (params or {}).get("reduceOnly"):
            seen.append(sum(1 for v in ex.algo.values() if v.get("type") == "STOP_MARKET"))
        return orig(m, path, params, s, t)
    T._request_raw = spy
    T.move_to_breakeven(p, 106.0)
    if not seen:
        return "沒有送出平倉單"
    if seen[0] == 0:
        return "送平倉單時交易所上沒有任何停損（先撤停損、後平倉）"


@case("8-e2", "手動平倉：撤停損一定在平倉確認之後")
def _():
    ex, clock = fresh()
    open_long(ex)
    T.close_position("XUSDT")
    seq = [("M" if c[2].get("type") == "MARKET" else "D") for c in ex.calls
           if (c[1] == "/fapi/v1/order" and c[2].get("type") == "MARKET") or c[0] == "DELETE"]
    if not seq or seq[0] != "M":
        return f"順序應為先平倉後撤單，實際 {seq}"


@case("8-f", "待平倉期間，其他出場路徑不能再送平倉單")
def _():
    ex, clock = fresh()
    open_long(ex)
    p = T.STATE["positions"]["XUSDT"]
    p["pendingClose"] = {"reason": "手動平倉", "attempts": 1, "lastErr": "x", "since": int(clock.now * 1000)}
    ex.reject_algo = lambda params: ((400, {"code": -2021, "msg": "Order would immediately trigger."})
                                     if float(params.get("triggerPrice") or 0) > 99.5 else None)
    n0 = len(market_calls(ex))
    T.move_to_breakeven(p, 106.0)
    T.CFG["guardClose"] = True
    for o in list(ex.algo):
        if ex.algo[o].get("type") == "STOP_MARKET":
            ex.algo.pop(o)
    ex.reject_algo = lambda params: (400, {"code": -1000, "msg": "busy"}) if params.get("type") == "STOP_MARKET" else None
    for _ in range(4):
        T.guard_positions()
    if len(market_calls(ex)) != n0:
        return f"待平倉期間其他路徑又送了 {len(market_calls(ex)) - n0} 張平倉單"
    # 對照組：同樣設定、沒有待平倉旗標時，移損穿價那條路徑確實會送平倉單
    ex2, _c = fresh()
    open_long(ex2)
    p2 = T.STATE["positions"]["XUSDT"]
    ex2.reject_algo = lambda params: ((400, {"code": -2021, "msg": "Order would immediately trigger."})
                                      if float(params.get("triggerPrice") or 0) > 99.5 else None)
    m0 = len(market_calls(ex2))
    T.move_to_breakeven(p2, 106.0)
    need(len(market_calls(ex2)) > m0, "對照組：沒有待平倉時移損穿價也沒送平倉單，上面的「沒送」可能是別的原因")


@case("8-g", "全量部位表偶發回空清單：要逐幣確認，不能直接判定全部平倉")
def _():
    ex, clock = fresh()
    open_long(ex)
    ex.full_list_empty = 1
    n0 = len([c for c in ex.calls if c[0] == "DELETE"])
    c0 = len(ex.calls)
    T.sync_positions()
    need(any(c[1] == "/fapi/v2/positionRisk" and not c[2].get("symbol") for c in ex.calls[c0:]),
         "對帳沒有查全量表（前提）")
    if "XUSDT" not in T.STATE["positions"]:
        return "部位表回空清單就把部位記成平倉"
    if len([c for c in ex.calls if c[0] == "DELETE"]) != n0:
        return "部位表回空清單就撤了停損"


@case("8-h", "有基準部位時偵測部分出場：用自己的數量比對")
def _():
    ex, clock = fresh()
    ex.pos[("XUSDT", "LONG")] = [2.0, 90.0]
    r = open_long(ex)
    p = T.STATE["positions"]["XUSDT"]
    mine = r["sizing"]["qty"]                                 # 修正：不從帳上讀（帳上可能已混了基準部位）
    need([o for o in p["orders"] if o["type"] == "TAKE_PROFIT_MARKET"], "要取的清單是空的（前提，r35：先確認有東西再索引）")
    tp = [o for o in p["orders"] if o["type"] == "TAKE_PROFIT_MARKET"][0]
    half = T.round_step(mine * T.CFG["tp1Portion"], 0.1)       # 這張單應出一半的數量
    ex.algo.pop(tp["id"])
    ex.pos[("XUSDT", "LONG")][0] -= half
    T.sync_positions()
    if abs(T.STATE["positions"]["XUSDT"]["qty"] - (mine - half)) > 1e-9:
        return f"有基準部位時沒偵測到出一半：帳上 {T.STATE['positions']['XUSDT']['qty']}，應為 {mine - half}"


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✕' if err else '✓'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
