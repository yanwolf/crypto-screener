"""出場規則一致性測試（BINANCE_LESSONS 第 11 條）。

crypto-screener 沒有回測引擎：出場全部委託交易所條件單執行。
所以這裡鎖住的不是「回測 vs 實盤」，而是同一組出場位階在程式裡被算了好幾次的地方：

  1. plan_exits 算出的位階 ＝ 實際送到交易所的 triggerPrice / activatePrice / callbackRate / quantity
  2. 移損到成本的觸發點 ＝ plan_exits 的 breakeven；搬過去的停損 ＝ 成本附近
  3. 影子追蹤（被上限擋掉的訊號回算）用的停損與目標 ＝ plan_exits
  4. 對帳認領的部位用的位階 ＝ plan_exits

任何一處改了而另一處沒跟上，這支就會失敗。改動下單或出場相關程式後執行：
    python3 -m tests.test_parity
"""
import os
import random
import sys
import importlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
import trader as T                                              # noqa: E402

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)


def fresh(hedge=False):
    importlib.reload(T)
    T.save_state = lambda: None
    T.CFG.update({"key": "k", "secret": "s", "dryRun": False, "leverage": 3, "riskPct": 0.5})
    T._mode.update({"hedge": hedge, "ts": 9e18})
    T._filters_ts = 9e18
    T._algo_supported[0] = None


def exchange(sym, mark, qty_holder, side="LONG"):
    """side：這一輪開倉方向。單向模式下空單要回負數、雙向模式下要回對應的 positionSide，
    跟真實幣安一致（以前一律回正數的 BOTH，程式改成依方向比對後才發現）。"""
    sent = []

    def fake(m, p, params=None, signed=False, timeout=15):
        params = dict(params or {})
        sent.append((m, p, params))
        if p == "/fapi/v2/account":
            return 200, {"totalWalletBalance": "1000", "totalMarginBalance": "1000", "availableBalance": "1000",
                         "totalPositionInitialMargin": "0", "totalUnrealizedProfit": "0"}
        if p == "/fapi/v1/premiumIndex":
            return 200, {"markPrice": str(mark)}
        if p == "/fapi/v1/order" and params.get("type") == "MARKET":
            qty_holder[0] = float(params["quantity"])
            return 200, {"orderId": 1}
        if p == "/fapi/v2/positionRisk":
            q = qty_holder[0]
            hedge = bool(T._mode.get("hedge"))
            amt = q if side == "LONG" else -q
            return 200, [{"symbol": sym, "positionAmt": str(amt), "entryPrice": str(mark),
                          "positionSide": side if hedge else "BOTH",
                          "markPrice": str(mark), "unRealizedProfit": "0"}]
        if p == "/fapi/v1/algoOrder":
            return 200, {"algoId": len(sent)}
        return 200, {}
    return fake, sent


def near(a, b, tick):
    return abs(float(a) - float(b)) <= tick * 0.5 + 1e-12


# ── 1. 送到交易所的參數 ＝ plan_exits ─────────────────────────
random.seed(11)
CHECKED = 0
for case in range(60):
    side = random.choice(["LONG", "SHORT"])
    mark = random.choice([0.02477, 0.3975, 9.02, 59.4, 2.43])
    tick = 10 ** -(len(str(mark).split(".")[1]) + 1)
    step = random.choice([0.1, 1.0, 0.001])
    sp = random.uniform(2, 12)
    fresh(hedge=random.choice([False, True]))
    T.CFG["tp1R"] = random.choice([1.5, 2.0, 2.5])
    T.CFG["trailR"] = random.choice([0.3, 0.5, 0.8])
    sym = "XUSDT"
    T._filters[sym] = {"status": "TRADING", "tick": tick, "step": step, "minQty": step, "minNotional": 5}
    q = [0.0]
    T._request_raw, sent = exchange(sym, mark, q, side)
    r = T.open_position("X", side, mark, None, stop_pct=sp)
    if not r.get("ok"):
        if "低於最小下單量" in (r.get("error") or ""):
            continue                     # 數量取整為 0 → 正確拒單，不是一致性問題
        FAIL.append(f"[{case}] 開倉失敗：{r.get('error')}")
        continue
    CHECKED += 1
    ex = T.plan_exits(r["entry"], r["stop"], side)
    algo = {p.get("type"): p for m, path, p in sent if path == "/fapi/v1/algoOrder" and m == "POST"}
    sl, tp, tr = algo.get("STOP_MARKET"), algo.get("TAKE_PROFIT_MARKET"), algo.get("TRAILING_STOP_MARKET")
    check(sl and near(sl["triggerPrice"], r["stop"], tick), f"[{case}] 停損觸發價 {sl and sl['triggerPrice']} ≠ 帳上 {r['stop']}")
    rt = lambda v: T.round_step(v, tick)
    want_tp_qty = T.round_step(r["qty"] * T.CFG["tp1Portion"], step)
    if want_tp_qty > 0:
        check(tp and abs(float(tp["triggerPrice"]) - rt(ex["tp1"])) < tick * 1e-3,
              f"[{case}] 停利觸發價 {tp and tp['triggerPrice']} ≠ plan_exits 取整 {rt(ex['tp1'])}")
        check(tp and abs(float(tp["quantity"]) - want_tp_qty) < 1e-9, f"[{case}] 停利數量不符")
    else:
        check(tp is None, f"[{case}] 半倉取整為 0 卻仍送出停利單")
    check(tr and abs(float(tr["activatePrice"]) - rt(ex["trailActivate"])) < tick * 1e-3,
          f"[{case}] 移動停利啟動價 {tr and tr['activatePrice']} ≠ plan_exits 取整 {rt(ex['trailActivate'])}")
    # 只比「送出值 ＝ 程式算出的值」，不自行假設交易所的上下限與精度
    check(tr and abs(float(tr["callbackRate"]) - float(ex["trailCallback"])) < 1e-9,
          f"[{case}] 回撤比例 {tr and tr['callbackRate']} ≠ plan_exits {ex['trailCallback']}")
    check(abs(r["exits"]["R"] - abs(r["entry"] - r["stop"])) < 1e-9,
          f"[{case}] 帳上 R {r['exits']['R']} ≠ |進場 − 實際停損| {abs(r['entry'] - r['stop'])}")
    check(all(o.get("id") for o in r["orders"]), f"[{case}] 帳上有訂單沒記 id（無法精準撤單）")

# ── 2. 移損到成本 ────────────────────────────────────────────
for side in ("LONG", "SHORT"):
    fresh()
    sgn = 1 if side == "LONG" else -1
    entry, stop = 100.0, 100.0 - sgn * 5
    T._filters["YUSDT"] = {"tick": 0.01, "step": 0.1, "minQty": 0.1, "minNotional": 5, "status": "TRADING"}
    pos = {"symbol": "YUSDT", "side": side, "qty": 1.0, "entry": entry, "stop": stop,
           "exits": T.plan_exits(entry, stop, side), "orders": [{"type": "STOP_MARKET", "id": 9, "via": "algo"}]}
    T._request = lambda m, p, params=None, signed=False, timeout=15: (200, {"algoId": 10})
    be = pos["exits"]["breakeven"]
    check(T.move_to_breakeven(dict(pos), be - sgn * 0.02) is None, f"{side} 未到 breakeven 就移損")
    ev = T.move_to_breakeven(dict(pos), be + sgn * 0.02)
    check(ev and ev.get("ok"), f"{side} 到 breakeven 沒有移損")
    check(ev and (ev["new"] - entry) * sgn >= 0 and abs(ev["new"] - entry) < entry * 0.005,
          f"{side} 移損後停損 {ev and ev.get('new')} 不在成本附近")

# ── 3. 影子追蹤的位階 ＝ plan_exits ──────────────────────────
for tp1R in (1.5, 2.0, 2.5):
    fresh()
    T.CFG["tp1R"] = tp1R
    entry, sp = 100.0, 5.0
    ex = T.plan_exits(entry, entry * (1 - sp / 100), "LONG")
    T.MISSED[:] = [{"sym": "Z", "cid": "z", "side": "LONG", "price": entry, "stopPct": sp, "score": 80,
                    "why": "", "ts": 0, "result": None, "r": None}]
    path = [(0, entry)] + [(i * 3600000, entry + (ex["tp1"] - entry) * i / 5) for i in range(1, 7)]
    T.missed_evaluate(lambda cid: path)
    m = T.MISSED[0]
    check(m["result"] == "tp2" and abs(m["r"] - tp1R) < 1e-9,
          f"影子追蹤 tp1R={tp1R}：結果 {m['result']} {m['r']}R，應為 tp2 {tp1R}R")

# ── 4. 對帳認領的位階 ＝ plan_exits ──────────────────────────
fresh()
T._filters["WUSDT"] = {"tick": 0.001, "step": 0.1, "minQty": 0.1, "minNotional": 5, "status": "TRADING"}
T.STATE["pending"] = {"WUSDT": {"side": "LONG", "qty": 10, "stop": None, "stopPct": 5, "note": "",
                                "params": {k: T.CFG.get(k) for k in T.PARAM_KEYS}, "ts": 1}}
T._request = lambda m, p, params=None, signed=False, timeout=15: (200, {"algoId": 5})
T.adopt_pending({("WUSDT", "LONG"): {"positionAmt": "10", "entryPrice": "9.02"}})
p = T.STATE["positions"].get("WUSDT")
check(p is not None, "對帳沒有認領 pending 部位")
if p:
    ex = T.plan_exits(p["entry"], p["stop"], "LONG")
    check(p["exits"] == ex, "認領部位的出場位階與 plan_exits 不一致")
    check(abs(p["stop"] - 9.02 * 0.95) < 0.001, f"認領部位停損 {p['stop']} ≠ 進場 × (1 − 5%)")

if FAIL:
    print(f"✕ 一致性測試：{len(FAIL)} 項失敗")
    for f in FAIL[:12]:
        print("   " + f)
    sys.exit(1)
print(f"✓ 一致性測試通過（送單參數 {CHECKED} 組、移損 2 向、影子追蹤 3 組 tp1R、對帳認領 1 組）")
