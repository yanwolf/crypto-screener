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
import trader as T
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.fake_exchange import realistic                     # noqa: E402

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
T.open_algo_orders = lambda sym, **kw: (others, True)
T._request = realistic(lambda m, p, params=None, signed=False, timeout=15: (200, {"algoId": 222}))
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


T._request = realistic(ex8, positions={"XUSDT": p})
e1 = T.move_to_breakeven(p, be + 0.5)
check(e1 and e1.get("retry") and e1.get("attempt") == 1 and e1.get("alert"),
      f"8 第一次失敗應回報 attempt=1 並告警，實際 {e1}")
check(e1 and e1.get("want") and e1.get("current") == 95.0, "8 告警要帶想要的停損與目前停損")
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
_held = {"q": p["qty"]}


def _ex3(m, path, params=None, signed=False, timeout=15):
    # 模擬環境要夠真實（用法第 5 點）：查部位回傳真的部位；只有新停損（成本附近）回 -2021，舊停損掛回去會成功
    params = params or {}
    if path == "/fapi/v2/positionRisk":
        return 200, [{"symbol": "XUSDT", "positionAmt": str(_held["q"]), "positionSide": "BOTH", "entryPrice": "100"}]
    if m == "DELETE":
        return 200, {}
    if path == "/fapi/v1/algoOrder":
        trig = float(params.get("triggerPrice") or 0)
        return (400, {"code": -2021, "msg": "Order would immediately trigger."}) if trig > 99 else (200, {"algoId": 7})
    if path == "/fapi/v1/order" and params.get("type") == "MARKET":
        _held["q"] = 0
        return 200, {"orderId": 1}
    return 200, {}


T._request = _ex3
e3 = T.move_to_breakeven(p, p["exits"]["breakeven"] + 0.5)
check(e3 and e3.get("exited") and closed, f"8 價格已穿過想要的停損（-2021）應直接出場，實際 {e3}")

fresh()
p = pos("LONG")
placed = []
T._request = realistic(lambda m, path, params=None, signed=False, timeout=15: (
    (400, {"code": -1000, "msg": "internal error"}) if m == "DELETE"
    else (placed.append(1) or (200, {"algoId": 1}))), positions={"XUSDT": p})
e4 = T.move_to_breakeven(p, p["exits"]["breakeven"] + 0.5)
check(e4 and e4.get("retry") and not placed, "8 撤不掉舊停損時不該掛新的")
check(any(o["id"] == 111 for o in p["orders"]), "8 撤舊失敗時舊 id 要留在帳上，平倉時一起撤")


# ── r7 第 7 條：快取從沒偵測成功（None）且偵測失敗，仍要反轉這張單的假設重送 ──
fresh()
T._mode.update({"hedge": None, "ts": 0})
sent = []


def raw_nocache(m, p, params, signed, timeout):
    sent.append(dict(params))
    if p == "/fapi/v1/positionSide/dual":
        return 500, {"msg": "timeout"}                      # 永遠偵測失敗
    if p == "/fapi/v1/order":
        return (200, {"orderId": 1}) if "positionSide" in params else (400, {"code": -4061, "msg": "mismatch"})
    return 200, {}


T._request_raw = raw_nocache
st, _ = T._request("POST", "/fapi/v1/order", {"symbol": "XUSDT", "side": "BUY", "type": "MARKET",
                                              "quantity": 1, **T._ps("LONG")}, signed=True)
orders = [s for s in sent if "type" in s]
check(st == 200 and len(orders) == 2 and "positionSide" not in orders[0] and orders[1].get("positionSide") == "LONG",
      f"r7-7 快取為空、偵測失敗：應以第一張的假設反轉後重送成功，實際 HTTP {st}，送出 {[('positionSide' in o) for o in orders]}")

# 反方向：當雙向送出、被 -1106 拒（其實是單向）
fresh()
T._mode.update({"hedge": True, "ts": time.time()})
sent = []


def raw_1106(m, p, params, signed, timeout):
    sent.append(dict(params))
    if p == "/fapi/v1/positionSide/dual":
        return 500, {"msg": "timeout"}
    if p == "/fapi/v1/order":
        return (400, {"code": -4061, "msg": "mismatch"}) if "positionSide" in params else (200, {"orderId": 1})
    return 200, {}


T._request_raw = raw_1106
st, _ = T._request("POST", "/fapi/v1/order", {"symbol": "XUSDT", "side": "SELL", "type": "MARKET",
                                              "quantity": 1, **T._reduce("LONG")}, signed=True)
orders = [s for s in sent if "type" in s]
check(st == 200 and orders[-1].get("reduceOnly") == "true" and "positionSide" not in orders[-1],
      "r7-7 當雙向送出被拒：重送應改成單向寫法（reduceOnly、不帶 positionSide）")

# ── 告警節奏：第 1、5、30 次，之後每 120 次 ─────────────────────
due = [n for n in range(1, 400) if T.alert_due(n)]
check(due == [1, 5, 30, 150, 270, 390], f"告警節奏應為 1,5,30,150,270,390，實際 {due}")

# ── 第 2 條：守衛補掛失敗依節奏告警、補上時通知 ─────────────────
fresh()
T.STATE["positions"] = {"XUSDT": pos("LONG", stop_id=111)}
T.open_algo_orders = lambda sym, **kw: ([], True)                 # 停損不在
T._request = realistic(lambda m, p, params=None, signed=False, timeout=15: (400, {"code": -1000, "msg": "busy"}))
alerts = []
for _ in range(2 + 31):                                     # 前 2 輪只累計不補掛，之後 31 次補掛失敗
    for ev in T.guard_positions():
        if ev["action"] == "alert" and ev["alert"]:
            alerts.append(ev["attempt"])
check(alerts == [1, 5, 30], f"第 2 條補掛失敗告警應在第 1、5、30 次，實際 {alerts}")
T._request = realistic(lambda m, p, params=None, signed=False, timeout=15: (200, {"algoId": 777}))
ev = T.guard_positions()
check(ev and ev[0]["action"] == "restored" and ev[0]["fails"] == 31, f"補上時應回報先前失敗次數，實際 {ev}")

# ── 第 13 條：殘留單撤不掉 → 待撤清單每輪重試 ─────────────────
fresh()
p_ = pos("LONG")
T.STATE["positions"] = {"XUSDT": p_}
T.STATE["leftovers"] = []
T.mark_price = lambda s: 99.0
T._request = lambda m, path, params=None, signed=False, timeout=15: (
    (400, {"code": -1000, "msg": "busy"}) if m == "DELETE" else (200, {}))
T.record_close(p_, 99.0, "交易所出場")
check(len(T.STATE["leftovers"]) == 2 and all(l["attempts"] == 1 for l in T.STATE["leftovers"]),
      f"平倉時撤不掉的兩張應進待撤清單（attempts=1），實際 {T.STATE['leftovers']}")
alerted = []
for _ in range(4):
    for ev in T.retry_leftovers():
        if ev["action"] == "alert" and ev["alert"]:
            alerted.append(ev["attempts"])
check(alerted == [5, 5], f"殘留單第 5 次應告警（兩張各一次），實際 {alerted}")
T._request = lambda m, path, params=None, signed=False, timeout=15: (400, {"code": -2011, "msg": "Unknown order sent."})
rec = [ev for ev in T.retry_leftovers() if ev["action"] == "recovered"]
check(len(rec) == 2 and T.STATE["leftovers"] == [], "查無此單（已被觸發或撤掉）應視為完成並清出待撤清單")

# r6：恢復時一律再發一次——即使第一次重試就成功（失敗次數仍是 1，平倉當下已告警過）
fresh()
p1 = pos("LONG")
T.STATE["positions"] = {"XUSDT": p1}
T.STATE["leftovers"] = []
T._request = lambda m, path, params=None, signed=False, timeout=15: (
    (400, {"code": -1000, "msg": "busy"}) if m == "DELETE" else (200, {}))
T.record_close(p1, 99.0, "交易所出場")
first = [a for a in T.drain_alerts() if "殘留單" in a["title"]]
check(first and "第 1 次" in first[0]["title"], f"第 13 條：平倉當下撤不掉應立即告警第 1 次，實際 {[a['title'] for a in first]}")
T._request = lambda m, path, params=None, signed=False, timeout=15: (200, {})
T.retry_leftovers()
rec = [a for a in T.drain_alerts() if "已撤掉" in a["title"]]
check(len(rec) == 2 and all("失敗 1 次" in a["text"] for a in rec),
      f"第 13 條：第一次重試就撤掉也要發恢復，實際 {[a['title'] for a in rec]}")

if FAIL:
    print(f"✕ 第 7、8 條情境測試：{len(FAIL)} 項失敗")
    for f in FAIL:
        print("   " + f)
    sys.exit(1)
print("✓ 第 2、7、8、13 條情境測試通過（對帳方向 3、模式反轉 3、守衛 id 6、移損重試 5、"
      "告警節奏 1、補掛節奏 2、待撤清單 3）")
