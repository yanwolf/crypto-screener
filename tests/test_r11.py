"""BINANCE_LESSONS r8 → r11 逐段差異的檢查項目。

依清單「怎麼用」第 5 點：先把新舊兩版逐段 diff、每段拆成可對程式檢查的項目，
測試先在修改前的程式上跑，確認會失敗，再修改到全部通過。

mock 只放在最底層的 _request_raw（交易所 API 回應），被測的偵測、快取、下單、
對帳、記帳函式都走真的程式碼（清單第 5 點：測試不能 mock 掉被測的那一段）。

    python3 -m tests.test_r11
"""
import os
import sys
import importlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
import trader as T                                              # noqa: E402

RESULTS = []
from tests.harness import make_case                          # noqa: E402


case = make_case(RESULTS)                                    # 共用框架（tests/harness.py）


from tests.fake_exchange import FakeEx, need, entry_sent  # noqa: E402


def fresh(mode="oneway"):
    importlib.reload(T)
    T.save_state = lambda: None
    T.CFG.update({"key": "k", "secret": "s", "dryRun": False, "leverage": 3, "riskPct": 0.5,
                  "breakevenR": 1.0, "maxPositions": 8})
    T._filters.update({"XUSDT": {"status": "TRADING", "tick": 0.01, "step": 0.1, "minQty": 0.1, "minNotional": 5}})
    T._filters_ts = 9e18
    T._algo_supported[0] = True
    ex = FakeEx(mode)
    T._request_raw = ex
    return ex


def alerts():
    fn = getattr(T, "drain_alerts", None)
    if fn is None:
        raise AttributeError("沒有 drain_alerts：告警不是在失敗發生的函式裡產生")
    return fn()


def open_long(ex, qty_hint=None):
    ex.mark["XUSDT"] = 100.0
    n0 = len(ex.calls)
    r = T.open_position("X", "LONG", 100.0, None, stop_pct=5)
    need(entry_sent(ex, n0), f"進場單沒有送出：{r.get('error')}")   # 前提（用法第 5 點 r17）
    return r


# ════ 第 7 條 ══════════════════════════════════════════════

@case("7-a", "快取過期、偵測失敗時仍送單（有舊值用舊值）")
def _():
    ex = fresh("hedge")
    T._mode.update({"hedge": True, "ts": 0})                 # 過期
    ex.dual_fail = True
    st, _ = T._request("POST", "/fapi/v1/order", {"symbol": "XUSDT", "side": "BUY", "type": "MARKET",
                                                  "quantity": 1, **T._ps("LONG")}, signed=True)
    duals = sum(1 for c in ex.calls if c[1] == "/fapi/v1/positionSide/dual")
    orders = [c for c in ex.calls if c[1] == "/fapi/v1/order"]
    if duals < 1:
        return "測試沒走到偵測分支"
    if st != 200 or len(orders) != 1:
        return f"單沒有照舊值送出：HTTP {st}，送單 {len(orders)} 次"


@case("7-b", "從沒偵測成功過、偵測失敗時假設單向並送出")
def _():
    ex = fresh("oneway")
    T._mode.update({"hedge": None, "ts": 0})
    ex.dual_fail = True
    st, _ = T._request("POST", "/fapi/v1/order", {"symbol": "XUSDT", "side": "BUY", "type": "MARKET",
                                                  "quantity": 1, **T._ps("LONG")}, signed=True)
    first = [c for c in ex.calls if c[1] == "/fapi/v1/order"][0][2]
    if "positionSide" in first or st != 200:
        return f"沒有先假設單向：第一張帶 positionSide={'positionSide' in first}，HTTP {st}"


@case("7-c", "自檢把偵測失敗當錯誤（只有自檢這麼做）")
def _():
    ex = fresh()
    ex.dual_fail = True
    import preflight
    importlib.reload(preflight)
    res = {r["item"]: r["status"] for r in preflight.check()}
    if res.get("持倉模式") != "fail":
        return f"自檢沒有把偵測失敗標成 fail：{res.get('持倉模式')}"


@case("7-d", "重送前不能把未驗證的反轉值寫進快取")
def _():
    ex = fresh("hedge")
    T._mode.update({"hedge": False, "ts": 0})
    ex.dual_fail = True
    seen = []
    orig = ex.__call__

    def spy(m, p, params, s, t):
        if p == "/fapi/v1/order" and "positionSide" in (params or {}):
            seen.append(T._mode["hedge"])                      # 重送的那一刻，快取是什麼
        return orig(m, p, params, s, t)
    T._request_raw = spy
    T._request("POST", "/fapi/v1/order", {"symbol": "XUSDT", "side": "BUY", "type": "MARKET",
                                         "quantity": 1, **T._ps("LONG")}, signed=True)
    need(seen, "沒有重送（前提）")
    if seen[0] is True:
        return "重送之前就把反轉值（雙向）寫進了快取"


@case("7-e", "重送成功才把快取寫成成功的假設")
def _():
    ex = fresh("hedge")
    T._mode.update({"hedge": False, "ts": 0})
    ex.dual_fail = True
    st, _ = T._request("POST", "/fapi/v1/order", {"symbol": "XUSDT", "side": "BUY", "type": "MARKET",
                                                  "quantity": 1, **T._ps("LONG")}, signed=True)
    if st != 200 or T._mode["hedge"] is not True:
        return f"重送成功後快取應為雙向，實際 {T._mode['hedge']}（HTTP {st}）"


@case("7-f", "重送也失敗就清掉快取，不留沒驗證過的值")
def _():
    ex = fresh("hedge")
    T._mode.update({"hedge": False, "ts": 0})
    ex.dual_fail = True
    ex.reject_market.add("XUSDT")
    ex.mode = "weird"                                         # 兩種寫法都被拒
    ex._mode_ok = lambda params, k: (False, -4061)
    T._request("POST", "/fapi/v1/order", {"symbol": "XUSDT", "side": "BUY", "type": "MARKET",
                                         "quantity": 1, **T._ps("LONG")}, signed=True)
    need(len([c for c in ex.calls if c[1] == "/fapi/v1/order"]) >= 2, "沒有重送（前提：要測的是重送失敗之後）")
    if T._mode["hedge"] is not None:
        return f"重送失敗後快取應清空，實際仍是 {T._mode['hedge']}"


@case("7-g", "平倉不依賴另外偵測模式；模式猜錯也會把平倉單送出去")
def _():
    ex = fresh("oneway")
    open_long(ex)
    T._mode.update({"hedge": True, "ts": 9e18})              # 快取猜錯成雙向
    ex.dual_fail = True
    n0 = len([c for c in ex.calls if c[1] == "/fapi/v1/order" and c[2].get("type") == "MARKET"])
    T.close_position("XUSDT")
    sent = [c for c in ex.calls if c[1] == "/fapi/v1/order" and c[2].get("type") == "MARKET"][n0:]
    left = ex.pos.get(("XUSDT", "LONG"), [0])[0]
    if not sent:
        return "沒有送出平倉單"
    if left > 0:
        return f"平倉單沒有成功送出，交易所還有 {left}"


@case("7-h", "確認成交時要用幣＋方向（單向時看正負號）")
def _():
    ex = fresh("oneway")
    ex.pos[("XUSDT", "SHORT")] = [5.0, 100.0]                 # 別的專案同幣空單
    q, _ = T.wait_position("XUSDT", 1.0, tries=1, gap=0, side="LONG")
    if q > 0:
        return f"把別人的空單 {q} 當成自己的多單已成交"
    # 對照組（否定句斷言藏在正向描述裡，r19）：自己的多單真的在時，同一個呼叫要找得到
    ex.pos[("XUSDT", "LONG")] = [1.0, 100.0]
    q2, _ = T.wait_position("XUSDT", 1.0, tries=1, gap=0, side="LONG")
    need(q2 > 0, "對照組：自己的多單在時也找不到，上面的「沒當成已成交」可能是查詢本身失敗")


# ════ 第 8 條：計數與恢復的位置 ═══════════════════════════

def _pos_with_failing_breakeven(ex):
    open_long(ex)
    p = T.STATE["positions"]["XUSDT"]
    ex.reject_algo = lambda params: ((400, {"code": -1000, "msg": "busy"})
                                     if float(params.get("triggerPrice") or 0) > 99.5 else None)
    return p


@case("8-1a", "移損第一次失敗就在移損函式裡告警（不論呼叫端）")
def _():
    ex = fresh()
    p = _pos_with_failing_breakeven(ex)
    alerts()
    T.move_to_breakeven(p, 106.0, force=True)                 # 反向訊號路徑的呼叫方式
    a = alerts()
    hit = [x for x in a if "XUSDT" in x["text"] and "第 1 次" in x["title"] + x["text"]]
    if not hit:
        return f"第一次失敗沒有告警：{[x['title'] for x in a]}"
    t = hit[0]["text"]
    for field in ("想要的停損", "目前停損", "busy"):
        if field not in t:
            return f"告警缺少「{field}」"


@case("8-1b", "殘留單第一次撤不掉就告警（手動平倉路徑也要）")
def _():
    ex = fresh()
    open_long(ex)
    alerts()
    ex_del = ex.__call__

    def del_fail(m, p, params, s, t):
        if m == "DELETE":
            return 400, {"code": -1000, "msg": "busy"}
        return ex_del(m, p, params, s, t)
    T._request_raw = del_fail
    T.close_position("XUSDT")
    a = alerts()
    hit = [x for x in a if "殘留" in x["title"] and "第 1 次" in x["title"] + x["text"]]
    if not hit:
        return f"手動平倉時殘留單第一次撤不掉沒有告警：{[x['title'] for x in a]}"
    if not all("busy" in x["text"] for x in hit):
        return f"告警裡的錯誤不是注入的那一個：{hit[0]['text'][:120]}"


@case("8-2a", "補掛回報已存在（誤報）時，先前的失敗要發恢復並歸零")
def _():
    ex = fresh()
    open_long(ex)
    p = T.STATE["positions"]["XUSDT"]
    for o in list(ex.algo):
        if ex.algo[o].get("type") == "STOP_MARKET":
            ex.algo.pop(o)
    ex.reject_algo = lambda params: (400, {"code": -1000, "msg": "busy"}) if params.get("type") == "STOP_MARKET" else None
    for _ in range(4):
        T.guard_positions()
    alerts()
    ex.reject_algo = lambda params: ((400, {"code": -4130, "msg": "An open stop order already existing"})
                                     if params.get("type") == "STOP_MARKET" else None)
    T.guard_positions()
    a = alerts()
    if not any("恢復" in x["title"] or "已補上" in x["text"] for x in a):
        return f"誤報時沒有發恢復：{[x['title'] for x in a]}"
    if T._replace_fails.get("XUSDT"):
        return f"誤報後補掛失敗次數沒歸零：{T._replace_fails.get('XUSDT')}"


@case("8-2b", "移損失敗中部位被平掉：發收尾通知")
def _():
    ex = fresh()
    p = _pos_with_failing_breakeven(ex)
    T.move_to_breakeven(p, 106.0)
    alerts()
    ex.pos[("XUSDT", "LONG")] = [0, 0]                        # 停損觸發，部位沒了
    T.sync_positions()
    a = alerts()
    if not any("XUSDT" in x["text"] and ("結束" in x["title"] or "已平倉" in x["text"]) and "移損" in x["title"] + x["text"]
               for x in a):
        return f"移損失敗狀態隨平倉消失，沒有收尾通知：{[x['title'] for x in a]}"


@case("8-2c", "補掛失敗中部位被平掉：發收尾通知，下次同幣從第 1 次算")
def _():
    ex = fresh()
    open_long(ex)
    for o in list(ex.algo):
        if ex.algo[o].get("type") == "STOP_MARKET":
            ex.algo.pop(o)
    ex.reject_algo = lambda params: (400, {"code": -1000, "msg": "busy"}) if params.get("type") == "STOP_MARKET" else None
    for _ in range(5):
        T.guard_positions()
    alerts()
    ex.pos[("XUSDT", "LONG")] = [0, 0]
    T.sync_positions()
    a = alerts()
    if not any("XUSDT" in x["text"] and "補掛" in x["title"] + x["text"] and
               ("結束" in x["title"] or "已平倉" in x["text"]) for x in a):
        return f"補掛失敗狀態隨平倉消失，沒有收尾通知：{[x['title'] for x in a]}"
    if T._replace_fails.get("XUSDT"):
        return f"平倉後補掛失敗次數還留著 {T._replace_fails.get('XUSDT')}，下次同幣會接著數"


# ════ 第 8 條：已成交先通知（減碼）════════════════════════

@case("8-3a", "2R 出一半成交後：通知、帳上數量更新")
def _():
    ex = fresh()
    open_long(ex)
    p = T.STATE["positions"]["XUSDT"]
    q0 = p["qty"]
    tp = [o for o in p["orders"] if o["type"] == "TAKE_PROFIT_MARKET"][0]
    half = float(ex.algo[tp["id"]]["quantity"])
    ex.algo.pop(tp["id"])                                     # 停利觸發
    ex.pos[("XUSDT", "LONG")][0] -= half
    ex.mark["XUSDT"] = p["exits"]["tp1"]
    alerts()
    T.sync_positions()
    a = alerts()
    if not any("部分" in x["title"] or "減碼" in x["title"] for x in a):
        return f"出一半成交沒有通知：{[x['title'] for x in a]}"
    if abs(T.STATE["positions"]["XUSDT"]["qty"] - (q0 - half)) > 1e-9:
        return f"帳上數量沒更新：{T.STATE['positions']['XUSDT']['qty']}，應為 {q0 - half}"


@case("8-3b", "出一半後再出場，損益＝一半在目標價＋剩下在出場價")
def _():
    ex = fresh()
    open_long(ex)
    p = T.STATE["positions"]["XUSDT"]
    q0, entry, tp1 = p["qty"], p["entry"], p["exits"]["tp1"]
    tp = [o for o in p["orders"] if o["type"] == "TAKE_PROFIT_MARKET"][0]
    half = float(ex.algo[tp["id"]]["quantity"])
    ex.algo.pop(tp["id"])
    ex.pos[("XUSDT", "LONG")][0] -= half
    ex.mark["XUSDT"] = tp1
    T.sync_positions()
    exit_px = entry                                           # 剩下的在成本出場
    ex.pos[("XUSDT", "LONG")] = [0, 0]
    ex.mark["XUSDT"] = exit_px
    T.sync_positions()
    t = T.STATE["trades"][-1]
    want = half * (tp1 - entry) + (q0 - half) * (exit_px - entry)
    if abs(t["pnl"] - want) > 0.01:
        return f"損益 {t['pnl']:.2f}，應為 {want:.2f}（一半在 {tp1:g}、一半在 {exit_px:g}）"


# ════ 第 8 條：成交後的例外不能改寫「已成交」════════════════

@case("8-4a", "進場成交後，後續步驟丟例外：不回報失敗、帳上有部位、今日開倉有算")
def _():
    ex = fresh()
    ex.mark["XUSDT"] = 100.0
    T.AUTO["on"] = True
    T.auto_roll_day()
    before = T.AUTO["opened"]
    orig = T.place_conditional

    def boom(params):
        if params.get("type") == "TAKE_PROFIT_MARKET":
            raise RuntimeError("寫紀錄時爆了")
        return orig(params)
    T.place_conditional = boom
    n0 = len(ex.calls)
    r = T.auto_open("X", "LONG", 100.0, None, stop_pct=5)
    need(entry_sent(ex, n0), f"進場單沒有送出：{r.get('error')}")
    if not (r.get("ok") or r.get("filled")):
        return f"已成交卻回報失敗：{r.get('error')}"
    if "XUSDT" not in T.STATE["positions"]:
        return "已成交但帳上沒有部位"
    if T.AUTO["opened"] != before + 1:
        return "已成交但今日開倉數沒增加"


@case("8-4b", "進場回應逾時（狀態 0）但其實成交：保留 pending 讓對帳認領")
def _():
    ex = fresh()
    ex.mark["XUSDT"] = 100.0
    ex.entry_timeout = True
    n0 = len(ex.calls)
    r = T.open_position("X", "LONG", 100.0, None, stop_pct=5)
    need(entry_sent(ex, n0), f"進場單沒有送出：{r.get('error')}")
    if "XUSDT" not in (T.STATE.get("pending") or {}) and "XUSDT" not in T.STATE["positions"]:
        return f"回應逾時就清掉 pending，交易所上的部位沒人管：{r.get('error')}"


@case("8-4c", "手動平倉被拒：不能記成已平倉、不能撤掉停損")
def _():
    ex = fresh()
    open_long(ex)
    stops_before = len(ex.algo)
    ex.reject_market.add("XUSDT")
    n0 = len(ex.calls)
    r = T.close_position("XUSDT")
    need(any(c[2].get("type") == "MARKET" and c[2].get("reduceOnly") for c in ex.calls[n0:]), "平倉單沒有送出（前提）")
    if "XUSDT" not in T.STATE["positions"]:
        return "平倉單被拒，帳上卻記成已平倉"
    if len(ex.algo) < stops_before:
        return f"平倉單被拒，停損卻被撤了（{stops_before} → {len(ex.algo)} 張）"
    if r.get("ok"):
        return "平倉單被拒卻回報成功"


@case("8-4d", "守衛強制平倉被拒：不能記成已平倉")
def _():
    ex = fresh()
    open_long(ex)
    T.CFG["guardClose"] = True
    for o in list(ex.algo):
        if ex.algo[o].get("type") == "STOP_MARKET":
            ex.algo.pop(o)
    ex.reject_algo = lambda params: (400, {"code": -1000, "msg": "busy"}) if params.get("type") == "STOP_MARKET" else None
    ex.reject_market.add("XUSDT")
    n0 = len(ex.calls)
    for _ in range(4):
        T.guard_positions()
    need(any(c[2].get("type") == "MARKET" and c[2].get("reduceOnly") for c in ex.calls[n0:]),
         "守衛沒有走到強制平倉（前提）")
    if "XUSDT" not in T.STATE["positions"]:
        return "強制平倉單被拒，帳上卻記成已平倉"


@case("8-4e", "移損時價格已穿過、平倉又被拒：不記成已平倉，舊停損要掛回")
def _():
    ex = fresh()
    open_long(ex)
    p = T.STATE["positions"]["XUSDT"]
    old = p["stop"]
    ex.reject_algo = lambda params: ((400, {"code": -2021, "msg": "Order would immediately trigger."})
                                     if float(params.get("triggerPrice") or 0) > 99.5 else None)
    ex.reject_market.add("XUSDT")
    n0 = len(ex.calls)
    T.move_to_breakeven(p, 106.0)
    need(any(c[0] == "DELETE" for c in ex.calls[n0:]), "移損沒有撤舊停損（前提：要測的是撤了之後）")
    need(any(c[2].get("type") == "MARKET" and c[2].get("reduceOnly") for c in ex.calls[n0:]), "沒有送出平倉單（前提）")
    if "XUSDT" not in T.STATE["positions"]:
        return "平倉單被拒，帳上卻記成已平倉"
    # 第 13 種：舊停損在測試前就存在，所以要看「這一步之後」有沒有重新掛上，不看最終狀態
    replaced = [c[2] for c in ex.calls[n0:] if c[1] == "/fapi/v1/algoOrder" and c[0] == "POST"
                and c[2].get("type") == "STOP_MARKET" and abs(float(c[2].get("triggerPrice") or 0) - old) < 1e-6]
    live_stops = [v for v in ex.algo.values() if v.get("type") == "STOP_MARKET"]
    if not replaced or not any(abs(float(v["triggerPrice"]) - old) < 1e-6 for v in live_stops):
        return f"舊停損撤了、新的沒掛、平倉失敗 → 裸倉（交易所停損：{[v.get('triggerPrice') for v in live_stops]}）"


@case("8-4f", "進場瞬間已穿過停損、平倉又被拒：保留紀錄讓後續接手")
def _():
    ex = fresh()
    ex.mark["XUSDT"] = 100.0
    orig = ex.__call__

    def crash_after_fill(m, p, params, s, t):
        st, d = orig(m, p, params, s, t)
        if p == "/fapi/v1/order" and params.get("type") == "MARKET" and not params.get("reduceOnly"):
            ex.mark["XUSDT"] = 90.0                           # 成交瞬間暴跌，穿過停損
        return st, d
    T._request_raw = crash_after_fill
    ex.reject_market.add("XUSDT")
    n0 = len(ex.calls)
    r = T.open_position("X", "LONG", 100.0, None, stop_pct=5)
    need(entry_sent(ex, n0), f"進場單沒有送出：{r.get('error')}")
    if ex.pos.get(("XUSDT", "LONG"), [0])[0] > 0 and "XUSDT" not in T.STATE["positions"] \
            and "XUSDT" not in (T.STATE.get("pending") or {}):
        return "平倉單被拒，部位還在交易所，帳上與 pending 都沒有紀錄"


@case("8-4g", "停損掛不上、平倉又被拒：部位要留在帳上")
def _():
    ex = fresh()
    ex.mark["XUSDT"] = 100.0
    ex.reject_algo = lambda params: (400, {"code": -1000, "msg": "busy"}) if params.get("type") == "STOP_MARKET" else None
    ex.reject_market.add("XUSDT")
    n0 = len(ex.calls)
    r = T.open_position("X", "LONG", 100.0, None, stop_pct=5)
    need(entry_sent(ex, n0), f"進場單沒有送出：{r.get('error')}")
    if ex.pos.get(("XUSDT", "LONG"), [0])[0] > 0 and "XUSDT" not in T.STATE["positions"]:
        return "停損掛不上且平倉被拒，部位還在交易所，帳上卻移除了"


# ════ 第 1 條（本次對照時由測試帶出，不在 r8→r11 差異內）═════════

@case("1-a", "Algo 端點回暫時性錯誤（-1000）或參數錯（-1013），不能永久改走舊端點")
def _():
    ex = fresh()
    n = {"i": 0}

    def once(params):
        n["i"] += 1
        return (400, {"code": -1000, "msg": "An unknown error occurred"}) if n["i"] == 1 else None
    ex.reject_algo = once
    T.place_conditional({"symbol": "XUSDT", "side": "SELL", "type": "STOP_MARKET", "stopPrice": 95.0,
                         "workingType": "MARK_PRICE", **T._close_all("LONG")})
    need(n["i"] == 1, "第一張沒有收到 -1000（前提）")
    st, _, via = T.place_conditional({"symbol": "XUSDT", "side": "SELL", "type": "STOP_MARKET", "stopPrice": 95.0,
                                      "workingType": "MARK_PRICE", **T._close_all("LONG")})
    if T._algo_supported[0] is False or via != "algo" or st != 200:
        return f"一次 -1000 之後就永久改走舊端點：下一張走 {via}、HTTP {st}"


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✕' if err else '✓'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
