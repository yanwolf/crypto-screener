"""BINANCE_LESSONS 第 7、8 條的情境測試（r4 新增的四點）。

每一個情境都是其他兩個專案真的踩過、或 crypto-screener 自查時發現的：
  7a 對帳用「幣＋方向」：單向與雙向模式下，別的專案同幣反向的部位不能讓自己以為還在場
  7b 模式不符：快取作廢、重新偵測失敗就反轉，原單重送一次
  7c 守衛只認自己記錄過 id 的停損：別人的停損、自己的移動停利都不能算
  8  移損失敗記下想要的停損、每輪重試直到成功；價格穿過就出場；撤舊失敗不掛新

    python3 -m tests.test_lessons
"""
import os
import sys
import time
import importlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
import trader as T                                              # noqa: E402

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)


def fresh():
    importlib.reload(T)
    T.save_state = lambda: None
    T.CFG.update({"key": "k", "secret": "s", "dryRun": False, "breakevenR": 1.0})
    T._filters["XUSDT"] = {"tick": 0.01, "step": 0.1, "minQty": 0.1, "minNotional": 5, "status": "TRADING"}
    T._filters_ts = 9e18
    T._algo_supported[0] = True


def pos(side="LONG", stop_id=111):
    entry, stop = 100.0, (95.0 if side == "LONG" else 105.0)
    return {"symbol": "XUSDT", "side": side, "qty": 2.0, "entry": entry, "stop": stop,
            "exits": T.plan_exits(entry, stop, side), "opened": 0,
            "orders": [{"type": "STOP_MARKET", "id": stop_id, "via": "algo"},
                       {"type": "TRAILING_STOP_MARKET", "id": 113, "via": "algo"}]}


# ── 7a 對帳用幣＋方向 ───────────────────────────────────────
for mode, rows, label in (
    ("單向", [{"symbol": "XUSDT", "positionAmt": "-5", "positionSide": "BOTH", "entryPrice": "100"}],
     "單向：我的多單已停損，別的專案有同幣空單"),
    ("雙向", [{"symbol": "XUSDT", "positionAmt": "0", "positionSide": "LONG", "entryPrice": "0"},
             {"symbol": "XUSDT", "positionAmt": "-5", "positionSide": "SHORT", "entryPrice": "100"}],
     "雙向：我的多單已停損，別的專案有同幣空單"),
):
    fresh()
    T.STATE["positions"] = {"XUSDT": pos("LONG")}
    T._request = lambda m, p, params=None, signed=False, timeout=15, rows=rows: (
        (200, rows) if p == "/fapi/v2/positionRisk" else (200, {"markPrice": "99"}))
    r = T.sync_positions()
    check("XUSDT" in r.get("closed", []), f"7a {label} → 應記為平倉，結果仍在場")

fresh()
T.STATE["positions"] = {"XUSDT": pos("LONG")}
T._request = lambda m, p, params=None, signed=False, timeout=15: (
    (200, [{"symbol": "XUSDT", "positionAmt": "2", "positionSide": "BOTH", "entryPrice": "100"}])
    if p == "/fapi/v2/positionRisk" else (200, {}))
check(T.sync_positions().get("closed") == [], "7a 單向：自己的多單還在，卻被記為平倉")

# ── 7b 模式不符：快取作廢、偵測失敗就反轉 ─────────────────────
fresh()
T._mode.update({"hedge": False, "ts": time.time()})       # 快取以為是單向
sent = []


def fake_raw(m, p, params, signed, timeout):
    sent.append(dict(params))
    if p == "/fapi/v1/positionSide/dual":
        return 500, {"msg": "timeout"}                      # 重新偵測失敗
    if p == "/fapi/v1/order":
        return (200, {"orderId": 1}) if "positionSide" in params else (400, {"code": -4061, "msg": "mismatch"})
    return 200, {}


T._request_raw = fake_raw
st, _ = T._request("POST", "/fapi/v1/order", {"symbol": "XUSDT", "side": "BUY", "type": "MARKET",
                                              "quantity": 1, **T._ps("LONG")}, signed=True)
orders = [s for s in sent if "type" in s]
check(st == 200 and len(orders) == 2 and orders[1].get("positionSide") == "LONG",
      f"7b 偵測失敗時應反轉快取後重送成功：HTTP {st}，送出 {len(orders)} 次")
check(T._mode["hedge"] is True, "7b 重送後快取仍停在舊模式")

# ── 7c 守衛只認自己記錄過 id 的停損 ──────────────────────────
fresh()
p = pos("LONG", stop_id=111)
others = [{"algoId": 999, "orderType": "STOP_MARKET", "side": "SELL", "positionSide": "BOTH"},   # 別的專案
          {"algoId": 113, "orderType": "TRAILING_STOP_MARKET", "side": "SELL"}]                  # 自己的移動停利
check(not T.has_stop_order(others, p), "7c 自己的停損已不在，卻被別人的停損或自己的移動停利蓋過")
check(T.has_stop_order(others + [{"algoId": 111, "orderType": "STOP_MARKET"}], p), "7c 自己的停損在卻沒認出來")
legacy = dict(p, orders=[])
check(T.has_stop_order([{"algoId": 5, "orderType": "STOP_MARKET", "side": "SELL", "positionSide": "BOTH"}], legacy),
      "7c 沒記 id 的舊部位應退而用類型＋方向判斷")
check(not T.has_stop_order([{"algoId": 5, "orderType": "STOP_MARKET", "side": "BUY", "positionSide": "BOTH"}], legacy),
      "7c 舊部位：反方向的停損不是自己的")

fresh()
T.STATE["positions"] = {"XUSDT": pos("LONG", stop_id=111)}
T.open_algo_orders = lambda sym: (others, True)
T._request = lambda m, p, params=None, signed=False, timeout=15: (200, {"algoId": 222})
for _ in range(3):
    ev = T.guard_positions()
check(ev and ev[0]["action"] == "restored", "7c 連續三輪確認不在後應補掛")
ids = [o["id"] for o in T.STATE["positions"]["XUSDT"]["orders"] if o["type"] == "STOP_MARKET"]
check(ids == [222], f"7c 補掛後帳上應只剩新停損 id 222，實際 {ids}")

# ── 8 移損失敗：記下想要的停損、每輪重試直到成功 ─────────────
fresh()
p = pos("LONG")
be = p["exits"]["breakeven"]
state = {"fail": 1}


def ex8(m, path, params=None, signed=False, timeout=15):
    if m == "DELETE":
        return 200, {}
    if path == "/fapi/v1/algoOrder":
        trig = float(params.get("triggerPrice") or 0)
        if trig > 99 and state["fail"] > 0:                  # 新停損（成本附近）第一次被拒
            state["fail"] -= 1
            return 400, {"code": -4130, "msg": "An open stop order already exists"}
        return 200, {"algoId": 300 + int(trig)}
    return 200, {}


T._request = ex8
e1 = T.move_to_breakeven(p, be + 0.5)
check(e1 and e1.get("retry") and e1.get("first"), f"8 第一次失敗應回報 retry/first，實際 {e1}")
check(p.get("wantStop") and p["stop"] == 95.0, "8 失敗後應記下想要的停損、原停損不變")
e2 = T.move_to_breakeven(p, be - 3.0)                       # 價格已回落到 1R 以下
check(e2 and e2.get("ok") and e2.get("recovered") and e2.get("fails") == 1,
      f"8 價格回落到 1R 下方仍應重試並成功，實際 {e2}")
check(p.get("beMoved") and "wantStop" not in p, "8 成功後應清掉 wantStop")

fresh()
p = pos("LONG")
closed = []
T.record_close = lambda pp, px, reason: closed.append(reason)
T.mark_price = lambda s: 99.0
T._request = lambda m, path, params=None, signed=False, timeout=15: (
    (200, {}) if m == "DELETE" or path == "/fapi/v1/order"
    else (400, {"code": -2021, "msg": "Order would immediately trigger."}))
e3 = T.move_to_breakeven(p, p["exits"]["breakeven"] + 0.5)
check(e3 and e3.get("exited") and closed, f"8 價格已穿過想要的停損（-2021）應直接出場，實際 {e3}")

fresh()
p = pos("LONG")
placed = []
T._request = lambda m, path, params=None, signed=False, timeout=15: (
    (400, {"code": -1000, "msg": "internal error"}) if m == "DELETE"
    else (placed.append(1) or (200, {"algoId": 1})))
e4 = T.move_to_breakeven(p, p["exits"]["breakeven"] + 0.5)
check(e4 and e4.get("retry") and not placed, "8 撤不掉舊停損時不該掛新的")
check(any(o["id"] == 111 for o in p["orders"]), "8 撤舊失敗時舊 id 要留在帳上，平倉時一起撤")

if FAIL:
    print(f"✕ 第 7、8 條情境測試：{len(FAIL)} 項失敗")
    for f in FAIL:
        print("   " + f)
    sys.exit(1)
print("✓ 第 7、8 條情境測試通過（對帳方向 3、模式反轉 1、守衛 id 6、移損重試 4）")
