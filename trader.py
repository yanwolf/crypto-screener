"""
幣安合約模擬網（Testnet）下單模組

設計原則：
1. 金鑰只存在伺服器環境變數，永遠不送到瀏覽器。
2. 預設鎖死在 Testnet。要打正式網必須同時設 ALLOW_LIVE=1 與 --live，
   避免一個環境變數手滑就打到真錢。
3. 下單前一定做可交易性檢查：有沒有永續合約、能不能下、精度與最小名目金額。
4. 部位大小由「單筆願意虧多少」反推，不是由金額反推。
   停損距離越遠、部位越小，讓每一筆的虧損上限一致，這是大賺小賠的前提。
5. 出場採 R 倍數：固定停損 1R，到 2R 先出一半，剩下用移動停利讓利潤跑。

與 engine.py 一樣，這裡不做任何預測，只執行訊號給出的計畫。
"""

import hashlib
import hmac
import json
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

TESTNET_BASE = "https://testnet.binancefuture.com"
LIVE_BASE = "https://fapi.binance.com"

CFG = {
    "key": "", "secret": "",
    "live": False,                 # 預設 Testnet
    "riskPct": 0.5,                # 單筆風險：帳戶權益的 %
    "maxPositions": 5,             # 同時最多幾個部位
    "leverage": 3,
    "stopAtrMult": 1.5,            # 停損 = 進場價 ∓ 1.5×ATR
    "tp1R": 2.0,                   # 第一目標：2R 出一半
    "tp1Portion": 0.5,
    "trailCallback": 1.2,          # 移動停利回撤 %
    "trailActivateR": 2.0,         # 到 2R 才啟動移動停利
    "minNotional": 5.0,
    "dryRun": False,               # True 時只計算不送單，供離線驗證
}

# 會存檔的設定項。live / key / secret 一律不存。
PERSIST_CFG = ("riskPct", "maxPositions", "leverage", "stopAtrMult",
               "tp1R", "tp1Portion", "trailCallback", "trailActivateR")

_filters = {}                      # symbol → 精度與限制
_filters_ts = 0
_lock = threading.Lock()
_time_offset = [0]                 # 伺服器與幣安的時鐘差

STATE = {
    "enabled": False,
    "positions": {},               # symbol → 部位紀錄
    "trades": [],                  # 已平倉紀錄
    "errors": [],
    "lastRun": None,
}

STATE_FILE = None


# ── 基礎工具 ────────────────────────────────────────────────

def base_url():
    return LIVE_BASE if CFG["live"] else TESTNET_BASE


def _sign(params: dict) -> str:
    q = urllib.parse.urlencode(params)
    sig = hmac.new(CFG["secret"].encode(), q.encode(), hashlib.sha256).hexdigest()
    return q + "&signature=" + sig


def _request(method, path, params=None, signed=False, timeout=15):
    """回傳 (status, data)。data 解析失敗時是原始文字。"""
    params = dict(params or {})
    if signed:
        if not CFG["key"] or not CFG["secret"]:
            return 401, {"error": "missing_credentials"}
        params["timestamp"] = int(time.time() * 1000) + _time_offset[0]
        params.setdefault("recvWindow", 10000)
        body = _sign(params)
    else:
        body = urllib.parse.urlencode(params)

    url = base_url() + path
    headers = {"User-Agent": "crypto-screener-trader/1.0"}
    if CFG["key"]:
        headers["X-MBX-APIKEY"] = CFG["key"]

    if method == "GET":
        req = urllib.request.Request(url + ("?" + body if body else ""), headers=headers)
    else:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=body.encode(), headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"{}")
        except Exception:
            return e.code, {"error": raw.decode(errors="replace")[:300]}
    except Exception as e:
        return 0, {"error": str(e)[:300]}


def sync_time():
    """幣安要求時間戳落在 recvWindow 內。容器時鐘漂移會造成 -1021 錯誤，
    所以啟動時先對時。"""
    st, d = _request("GET", "/fapi/v1/time")
    if st == 200 and isinstance(d, dict) and "serverTime" in d:
        _time_offset[0] = int(d["serverTime"]) - int(time.time() * 1000)
        return _time_offset[0]
    return None


# ── 精度與可交易性 ──────────────────────────────────────────

_filters_err = [None]


def load_filters(force=False):
    """抓 exchangeInfo，建立每個交易對的精度與下限。
    失敗時記錄原因，讓呼叫端能分辨「查不到」與「不存在」。"""
    global _filters_ts
    with _lock:
        if _filters and not force and time.time() - _filters_ts < 3600:
            return _filters
    st, d = _request("GET", "/fapi/v1/exchangeInfo", timeout=25)
    if st != 200 or not isinstance(d, dict):
        _filters_err[0] = (d.get("error") or d.get("msg") or f"HTTP {st}") if isinstance(d, dict) else f"HTTP {st}"
        return _filters
    _filters_err[0] = None
    out = {}
    for s in d.get("symbols", []):
        if s.get("contractType") != "PERPETUAL" or s.get("quoteAsset") != "USDT":
            continue
        f = {"status": s.get("status"), "base": s.get("baseAsset"),
             "tick": None, "step": None, "minQty": None, "minNotional": None}
        for flt in s.get("filters", []):
            t = flt.get("filterType")
            if t == "PRICE_FILTER":
                f["tick"] = float(flt["tickSize"])
            elif t == "LOT_SIZE":
                f["step"] = float(flt["stepSize"])
                f["minQty"] = float(flt["minQty"])
            elif t in ("MIN_NOTIONAL", "NOTIONAL"):
                f["minNotional"] = float(flt.get("notional") or flt.get("minNotional") or 0)
        out[s["symbol"]] = f
    with _lock:
        _filters.clear()
        _filters.update(out)
        _filters_ts = time.time()
    return _filters


def round_step(v, step):
    """往下取到步進的整數倍。用字串處理避免浮點誤差把數量推過界，
    幣安對精度非常嚴格，多一位小數就會被拒單。"""
    if not step or step <= 0:
        return v
    n = math.floor(round(v / step, 9)) * step
    dec = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return float(f"{n:.{dec}f}")


def check_tradable(symbol_base: str):
    """下單前的可交易性檢查。回傳 (ok, symbol, 說明, filters)。

    刻意區分三種結果，因為「連不到交易所」和「這個幣沒有合約」
    是完全不同的問題，混為一談會讓人誤判。
    """
    f = load_filters()
    sym = symbol_base.upper() + "USDT"
    if not f:
        return False, sym, (
            "取不到幣安合約清單，無法確認是否有這檔合約"
            + (f"（{_filters_err[0]}）" if _filters_err[0] else "")
            + "。這是連線問題，不代表沒有合約。"), None
    if sym not in f:
        return False, sym, f"幣安沒有 {sym} 永續合約（清單共 {len(f)} 檔）", None
    info = f[sym]
    if info.get("status") != "TRADING":
        return False, sym, f"{sym} 目前狀態為 {info.get('status')}，無法下單", info
    return True, sym, "可交易", info


def mark_price(symbol):
    st, d = _request("GET", "/fapi/v1/premiumIndex", {"symbol": symbol})
    if st == 200 and isinstance(d, dict):
        try:
            return float(d["markPrice"])
        except Exception:
            return None
    return None


def account_equity():
    st, d = _request("GET", "/fapi/v2/account", signed=True)
    if st != 200 or not isinstance(d, dict):
        return None, (d.get("msg") or d.get("error") or f"HTTP {st}") if isinstance(d, dict) else str(st)
    try:
        return float(d["totalWalletBalance"]), None
    except Exception:
        return None, "回應缺少 totalWalletBalance"


# ── 部位大小 ────────────────────────────────────────────────

def size_position(equity, entry, stop, info, risk_pct=None, lev=None):
    """由「單筆願意虧多少」反推數量，而不是由金額反推。

    停損距離越遠 → 數量越小 → 每筆最大虧損維持一致。
    這是大賺小賠能成立的前提：虧損端必須被壓在固定值。
    回傳 (qty, 說明dict)。qty 為 0 代表這筆不該下。
    """
    risk_pct = CFG["riskPct"] if risk_pct is None else risk_pct
    lev = CFG["leverage"] if lev is None else lev
    dist = abs(entry - stop)
    out = {"equity": equity, "entry": entry, "stop": stop, "dist": dist,
           "riskAmt": None, "qty": 0.0, "notional": 0.0, "reason": None}
    if not (equity and entry and dist > 0):
        out["reason"] = "缺少權益、進場價或停損距離"
        return 0.0, out

    risk_amt = equity * risk_pct / 100.0
    out["riskAmt"] = risk_amt
    qty = risk_amt / dist

    step = (info or {}).get("step") or 0.001
    qty = round_step(qty, step)
    notional = qty * entry

    # 名目金額上限：不得超過權益 × 槓桿
    cap = equity * lev
    if notional > cap:
        qty = round_step(cap / entry, step)
        notional = qty * entry
        out["reason"] = "受槓桿上限縮減"

    min_qty = (info or {}).get("minQty") or 0
    min_not = max((info or {}).get("minNotional") or 0, CFG["minNotional"])
    if qty < min_qty or notional < min_not:
        out["qty"] = qty
        out["notional"] = notional
        out["reason"] = (f"數量 {qty} 低於最小下單量 {min_qty}" if qty < min_qty
                         else f"名目金額 {notional:.2f} 低於最小值 {min_not}")
        return 0.0, out

    out["qty"] = qty
    out["notional"] = notional
    return qty, out


def plan_exits(entry, stop, side):
    """由停損距離推出各級目標。R = 1 倍停損距離。"""
    r = abs(entry - stop)
    sgn = 1 if side == "LONG" else -1
    return {
        "R": r,
        "stop": stop,
        "tp1": entry + sgn * r * CFG["tp1R"],
        "trailActivate": entry + sgn * r * CFG["trailActivateR"],
        "trailCallback": CFG["trailCallback"],
    }




# ── 條件單（停損／停利／移動停利）──────────────────────────
#
# 幣安自 2025-12-09 起把條件單搬到 Algo 服務，舊的 /fapi/v1/order
# 會用 -4120 拒絕 STOP_MARKET 這類型別。
# 新端點的參數名稱也不同：stopPrice → triggerPrice，
# activationPrice → activatePrice，且必須帶 algoType=CONDITIONAL。
#
# 這裡優先打新端點，遇到「端點不存在」才退回舊寫法，
# 讓不同版本的正式網與模擬網都能運作。

_algo_supported = [None]        # None = 還不確定，True/False = 已測知


def place_conditional(params: dict):
    """送出條件單。回傳 (status, data, 用了哪個端點)。"""
    algo = dict(params)
    algo["algoType"] = "CONDITIONAL"
    if "stopPrice" in algo:
        algo["triggerPrice"] = algo.pop("stopPrice")
    if "activationPrice" in algo:
        algo["activatePrice"] = algo.pop("activationPrice")

    if _algo_supported[0] is not False:
        st, d = _request("POST", "/fapi/v1/algoOrder", algo, signed=True)
        if st == 200:
            _algo_supported[0] = True
            return st, d, "algo"
        code = d.get("code") if isinstance(d, dict) else None
        # -1121 之類的參數錯不代表端點不存在，只有 404 或未知端點才退回
        if st == 404 or code in (-1000, -1013) or "Unknown" in str(d):
            _algo_supported[0] = False
        else:
            return st, d, "algo"

    legacy = dict(params)
    st, d = _request("POST", "/fapi/v1/order", legacy, signed=True)
    return st, d, "legacy"


def cancel_conditional(symbol):
    """兩種端點都清一次，避免殘留掛單擋住下一筆。"""
    out = []
    st, d = _request("DELETE", "/fapi/v1/algoOpenOrders", {"symbol": symbol}, signed=True)
    out.append(("algo", st))
    st2, d2 = _request("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol}, signed=True)
    out.append(("legacy", st2))
    return out


# ── 下單 ────────────────────────────────────────────────────

def set_leverage(symbol, lev):
    return _request("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": int(lev)}, signed=True)




def wait_position(symbol, want_qty, tries=12, gap=0.5):
    """等部位出現在帳戶上，回傳 (實際數量, 進場均價)。

    市價單成交與 positionRisk 更新之間有延遲，直接掛條件單會被拒。
    這裡輪詢到部位出現為止，最多約 6 秒。
    """
    for i in range(tries):
        st, d = _request("GET", "/fapi/v2/positionRisk", {"symbol": symbol}, signed=True)
        if st == 200 and isinstance(d, list):
            for p in d:
                try:
                    amt = abs(float(p.get("positionAmt") or 0))
                    ep = float(p.get("entryPrice") or 0)
                except Exception:
                    continue
                if amt > 0:
                    return amt, (ep or None)
        time.sleep(gap)
    return 0.0, None


def open_position(symbol_base, side, entry_hint, stop, info=None, note=""):
    """進場：市價單 + 停損單 + 部分停利 + 移動停利。

    停損一定在進場後立刻掛出。如果掛停損失敗，會立刻market平掉剛進的倉，
    因為沒有停損的部位違反這套系統的前提。
    """
    ok, sym, msg, finfo = check_tradable(symbol_base)
    if not ok:
        return {"ok": False, "error": msg}
    info = info or finfo

    equity, err = account_equity()
    if equity is None:
        return {"ok": False, "error": f"取不到帳戶權益：{err}"}

    px = mark_price(sym) or entry_hint
    if not px:
        return {"ok": False, "error": "取不到市價"}

    qty, detail = size_position(equity, px, stop, info)
    if qty <= 0:
        return {"ok": False, "error": f"部位大小不合格：{detail.get('reason')}", "detail": detail}

    exits = plan_exits(px, stop, side)
    if CFG["dryRun"]:
        return {"ok": True, "dryRun": True, "symbol": sym, "side": side,
                "qty": qty, "entry": px, "exits": exits, "sizing": detail}

    set_leverage(sym, CFG["leverage"])
    order_side = "BUY" if side == "LONG" else "SELL"
    close_side = "SELL" if side == "LONG" else "BUY"

    # newOrderRespType=RESULT 讓市價單回傳成交結果而非只回 ACK，
    # 這樣才拿得到實際成交均價。
    st, entry_res = _request("POST", "/fapi/v1/order", {
        "symbol": sym, "side": order_side, "type": "MARKET", "quantity": qty,
        "newOrderRespType": "RESULT",
    }, signed=True)
    if st != 200:
        return {"ok": False, "error": f"進場失敗：{entry_res.get('msg') or entry_res}"}

    # 等部位真的出現在帳戶上再掛條件單。
    # 幣安的成交與部位更新之間有延遲，太早掛 closePosition=true 的單會被拒，
    # 錯誤訊息是「TIF GTE can only be used with open positions」。
    actual_qty, actual_entry = wait_position(sym, qty)
    if actual_qty <= 0:
        return {"ok": False, "error": "進場單已送出，但 6 秒內查不到部位，請到幣安確認後手動處理"}
    qty = actual_qty
    if actual_entry:
        px = actual_entry            # 用實際成交均價重算出場位階
        exits = plan_exits(px, stop, side)

    tick = (info or {}).get("tick") or 0.01
    stop_px = round_step(stop, tick)
    sub, errs = [], []

    # 掛停損前用「當下」的標記價再檢查一次。
    # 訊號產生到實際下單之間可能隔了幾分鐘，波動大的幣可能已經跌破預定停損；
    # 這時掛單會被拒（Order would immediately trigger），
    # 而且更重要的是——這筆的前提已經不成立，不該留倉。
    live = mark_price(sym) or px
    buf = 0.002                      # 0.2% 緩衝，避免掛在剛好觸發的邊緣
    breached = (stop_px >= live * (1 - buf)) if side == "LONG" else (stop_px <= live * (1 + buf))
    if breached:
        _request("POST", "/fapi/v1/order", {
            "symbol": sym, "side": close_side, "type": "MARKET",
            "quantity": qty, "reduceOnly": "true",
        }, signed=True)
        return {"ok": False, "error": (
            f"下單瞬間價格已越過預定停損（現價 {live:g}，停損 {stop_px:g}），"
            f"進場前提不成立，已立即平倉不留倉位")}

    # 停損：closePosition 確保無論部位多大都全平
    st2, r2, ep2 = place_conditional({
        "symbol": sym, "side": close_side, "type": "STOP_MARKET",
        "stopPrice": stop_px, "closePosition": "true", "workingType": "MARK_PRICE",
    })
    if st2 != 200:
        errs.append(f"停損掛單失敗：{r2.get('msg') or r2}")
        # 沒有停損就不留倉
        _request("POST", "/fapi/v1/order", {
            "symbol": sym, "side": close_side, "type": "MARKET",
            "quantity": qty, "reduceOnly": "true",
        }, signed=True)
        return {"ok": False, "error": "；".join(errs) + "　已立即平倉，避免無停損部位"}
    sub.append({"type": "STOP_MARKET", "id": r2.get("algoId") or r2.get("orderId"),
                "px": stop_px, "via": ep2})

    # 第一目標：出一半，讓剩下的部位零成本奔跑
    step = (info or {}).get("step") or 0.001
    tp_qty = round_step(qty * CFG["tp1Portion"], step)
    if tp_qty > 0:
        st3, r3, ep3 = place_conditional({
            "symbol": sym, "side": close_side, "type": "TAKE_PROFIT_MARKET",
            "stopPrice": round_step(exits["tp1"], tick), "quantity": tp_qty,
            "reduceOnly": "true", "workingType": "MARK_PRICE",
        })
        if st3 == 200:
            sub.append({"type": "TAKE_PROFIT_MARKET", "id": r3.get("algoId") or r3.get("orderId"),
                        "px": round_step(exits["tp1"], tick), "qty": tp_qty, "via": ep3})
        else:
            errs.append(f"停利掛單失敗：{r3.get('msg') or r3}")

    # 移動停利：到 2R 才啟動，讓趨勢單有機會走遠
    trail_qty = round_step(qty - tp_qty, step)
    if trail_qty > 0:
        st4, r4, ep4 = place_conditional({
            "symbol": sym, "side": close_side, "type": "TRAILING_STOP_MARKET",
            "quantity": trail_qty, "callbackRate": CFG["trailCallback"],
            "activationPrice": round_step(exits["trailActivate"], tick),
            "reduceOnly": "true", "workingType": "MARK_PRICE",
        })
        if st4 == 200:
            sub.append({"type": "TRAILING_STOP_MARKET", "id": r4.get("algoId") or r4.get("orderId"),
                        "activate": round_step(exits["trailActivate"], tick), "via": ep4})
        else:
            errs.append(f"移動停利掛單失敗：{r4.get('msg') or r4}")

    pos = {
        "symbol": sym, "side": side, "qty": qty, "entry": px,
        "stop": stop_px, "exits": exits, "sizing": detail,
        "orders": sub, "opened": int(time.time() * 1000),
        "note": note, "warnings": errs,
    }
    STATE["positions"][sym] = pos
    save_state()
    return {"ok": True, **pos}




# ── 自動下單的風險閘門 ──────────────────────────────────────
#
# 自動下單會在沒人看著的時候開倉，所以閘門比手動嚴格。
# 這些限制是硬性的，任何一條不過就不下單。

AUTO = {
    "on": False,
    "maxPerDay": 6,            # 每日最多開幾筆
    "dailyLossR": -3.0,        # 當日累計虧損達 3R 就停止當天所有下單
    "cooldownMin": 120,        # 同一檔幣平倉後多久才能再進
    "minScore": 62,            # 自動下單的分數門檻，比手動的 58 嚴格
    "day": None,               # 當前計算中的日期
    "opened": 0,               # 今日已開倉數
    "closedR": 0.0,            # 今日已實現 R
    "lastClose": {},           # symbol → 最後平倉時間
    "blocked": None,           # 當日被停用的原因
}


def _today():
    return time.strftime("%Y-%m-%d", time.gmtime())


def auto_roll_day():
    """跨日重置計數。自動下單的限制以 UTC 日為單位。"""
    d = _today()
    if AUTO["day"] != d:
        AUTO["day"] = d
        AUTO["opened"] = 0
        AUTO["closedR"] = 0.0
        AUTO["blocked"] = None
        save_state()


def auto_can_trade(symbol):
    """回傳 (可否下單, 原因)。這裡只管風險額度，不管訊號好壞。"""
    auto_roll_day()
    if not AUTO["on"]:
        return False, "自動下單未啟用"
    if not (CFG["key"] and CFG["secret"]):
        return False, "未設定幣安金鑰"
    if AUTO["blocked"]:
        return False, AUTO["blocked"]
    if AUTO["closedR"] <= AUTO["dailyLossR"]:
        AUTO["blocked"] = f"當日已虧損 {AUTO['closedR']:.2f}R，達停損上限，今日停止下單"
        save_state()
        return False, AUTO["blocked"]
    if AUTO["opened"] >= AUTO["maxPerDay"]:
        return False, f"今日已開 {AUTO['opened']} 筆，達上限"
    if len(STATE["positions"]) >= CFG["maxPositions"]:
        return False, f"同時持倉已達 {CFG['maxPositions']} 筆上限"
    if symbol in STATE["positions"]:
        return False, "這檔已有部位"
    last = AUTO["lastClose"].get(symbol)
    if last and (time.time() - last) < AUTO["cooldownMin"] * 60:
        left = int((AUTO["cooldownMin"] * 60 - (time.time() - last)) / 60)
        return False, f"剛平倉，冷卻中還剩 {left} 分鐘"
    return True, None


def auto_open(symbol_base, side, entry, stop, note="", on_event=None):
    """自動開倉。通過風險閘門才會真的送單。"""
    sym = symbol_base.upper() + "USDT"
    ok, why = auto_can_trade(sym)
    if not ok:
        return {"ok": False, "skipped": True, "error": why}

    r = open_position(symbol_base, side, entry, stop, note=note)
    if r.get("ok"):
        AUTO["opened"] += 1
        save_state()
        if on_event:
            on_event("open", r)
    return r


def close_position(symbol, reason="手動平倉"):
    pos = STATE["positions"].get(symbol)
    if not pos:
        return {"ok": False, "error": "沒有這個部位"}
    close_side = "SELL" if pos["side"] == "LONG" else "BUY"
    if not CFG["dryRun"]:
        _request("POST", "/fapi/v1/order", {
            "symbol": symbol, "side": close_side, "type": "MARKET",
            "quantity": pos["qty"], "reduceOnly": "true",
        }, signed=True)
        cancel_conditional(symbol)
    px = mark_price(symbol) or pos["entry"]
    record_close(pos, px, reason)
    return {"ok": True, "symbol": symbol, "exit": px}


def record_close(pos, exit_px, reason):
    sgn = 1 if pos["side"] == "LONG" else -1
    pnl = (exit_px - pos["entry"]) * sgn * pos["qty"]
    r = pos["exits"]["R"] * pos["qty"]
    STATE["trades"].append({
        "symbol": pos["symbol"], "side": pos["side"], "qty": pos["qty"],
        "entry": pos["entry"], "exit": exit_px, "pnl": pnl,
        "rMultiple": (pnl / r) if r else None,
        "opened": pos["opened"], "closed": int(time.time() * 1000),
        "reason": reason, "note": pos.get("note", ""),
    })
    STATE["positions"].pop(pos["symbol"], None)
    AUTO["lastClose"][pos["symbol"]] = time.time()
    rm = STATE["trades"][-1].get("rMultiple")
    if rm is not None:
        auto_roll_day()
        AUTO["closedR"] += rm
    save_state()


def sync_positions():
    """跟幣安對帳：本地記著但交易所已無部位的，代表被停損或停利成交了。"""
    if CFG["dryRun"]:
        return {"closed": []}
    st, d = _request("GET", "/fapi/v2/positionRisk", signed=True)
    if st != 200 or not isinstance(d, list):
        return {"error": "對帳失敗"}
    live = {}
    for p in d:
        try:
            amt = float(p.get("positionAmt") or 0)
        except Exception:
            amt = 0.0
        if abs(amt) > 0:
            live[p["symbol"]] = p
    closed = []
    for sym in list(STATE["positions"].keys()):
        if sym not in live:
            pos = STATE["positions"][sym]
            px = mark_price(sym) or pos["entry"]
            record_close(pos, px, "交易所出場（停損或停利觸發）")
            closed.append(sym)
    STATE["lastRun"] = int(time.time() * 1000)
    return {"closed": closed, "open": list(live.keys())}




_live_cache = {"ts": 0, "data": {}}


def live_positions(max_age=10):
    """向幣安取各部位的即時損益。

    positionRisk 一次回傳所有部位的標記價與未實現損益，
    不必逐檔查價。畫面會頻繁重整，所以短暫快取避免打太密。
    """
    now = time.time()
    if now - _live_cache["ts"] < max_age:
        return _live_cache["data"]
    if not (CFG["key"] and CFG["secret"]) or CFG["dryRun"]:
        return {}
    st, d = _request("GET", "/fapi/v2/positionRisk", signed=True)
    if st != 200 or not isinstance(d, list):
        return _live_cache["data"]
    out = {}
    for p in d:
        try:
            amt = float(p.get("positionAmt") or 0)
            if abs(amt) <= 0:
                continue
            out[p["symbol"]] = {
                "mark": float(p.get("markPrice") or 0),
                "pnl": float(p.get("unRealizedProfit") or 0),
                "entry": float(p.get("entryPrice") or 0),
                "qty": abs(amt),
                "liq": float(p.get("liquidationPrice") or 0) or None,
            }
        except (TypeError, ValueError):
            continue
    _live_cache["ts"] = now
    _live_cache["data"] = out
    return out


def enrich_positions():
    """把即時損益併進本地部位紀錄，並換算成 R 倍數。"""
    live = live_positions()
    rows = []
    for p in STATE["positions"].values():
        r = dict(p)
        L = live.get(p["symbol"])
        if L:
            r["mark"] = L["mark"]
            r["pnl"] = round(L["pnl"], 4)
            r["liq"] = L["liq"]
            # R = 進場到停損的價格距離；乘上數量就是這筆的 1R 金額
            unit = (p.get("exits") or {}).get("R")
            risk_amt = (unit or 0) * (p.get("qty") or 0)
            r["rMultiple"] = round(L["pnl"] / risk_amt, 2) if risk_amt else None
            # 距停損還有多遠，用來判斷這筆還剩多少緩衝
            if p.get("stop"):
                span = abs(L["mark"] - p["stop"])
                r["toStopPct"] = round(100 * span / L["mark"], 2) if L["mark"] else None
        rows.append(r)
    return rows


# ── 績效統計 ────────────────────────────────────────────────

def performance():
    """以 R 倍數為核心。大賺小賠的關鍵不是勝率，是平均獲利 R 要明顯大於
    平均虧損 R，所以這裡把兩者分開列出。"""
    t = STATE["trades"]
    if not t:
        return {"count": 0}
    wins = [x for x in t if (x["pnl"] or 0) > 0]
    losses = [x for x in t if (x["pnl"] or 0) <= 0]
    rs = [x["rMultiple"] for x in t if x.get("rMultiple") is not None]
    win_r = [x["rMultiple"] for x in wins if x.get("rMultiple") is not None]
    loss_r = [x["rMultiple"] for x in losses if x.get("rMultiple") is not None]

    equity_curve, peak, dd = 0.0, 0.0, 0.0
    for x in t:
        equity_curve += x["pnl"] or 0
        peak = max(peak, equity_curve)
        dd = min(dd, equity_curve - peak)

    avg_w = sum(win_r) / len(win_r) if win_r else 0.0
    avg_l = sum(loss_r) / len(loss_r) if loss_r else 0.0
    wr = len(wins) / len(t)
    return {
        "count": len(t),
        "wins": len(wins), "losses": len(losses),
        "winRate": round(wr * 100, 1),
        "pnl": round(sum(x["pnl"] or 0 for x in t), 2),
        "avgWinR": round(avg_w, 2), "avgLossR": round(avg_l, 2),
        # 賺賠比：平均獲利 R ÷ 平均虧損 R。大賺小賠要讓這個數字大於 2
        "payoff": round(abs(avg_w / avg_l), 2) if avg_l else None,
        # 期望值：每冒 1R 風險平均賺回多少 R
        "expectancyR": round(wr * avg_w + (1 - wr) * avg_l, 3),
        "totalR": round(sum(rs), 2) if rs else None,
        "maxDrawdown": round(dd, 2),
        "best": round(max(rs), 2) if rs else None,
        "worst": round(min(rs), 2) if rs else None,
    }


# ── 狀態持久化 ──────────────────────────────────────────────

def save_state():
    if not STATE_FILE:
        return
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"state": {k: STATE[k] for k in ("enabled", "positions", "trades")},
                       "auto": AUTO,
                       # 金鑰與網路別刻意不存：金鑰只該在環境變數，
                       # 網路別只該由啟動參數決定，避免存檔把正式網狀態帶回來
                       "cfg": {k: CFG[k] for k in PERSIST_CFG}}, f)
    except Exception:
        pass


def load_state(path):
    global STATE_FILE
    STATE_FILE = path
    try:
        with open(path) as f:
            d = json.load(f)
        st = d.get("state", d)          # 相容舊格式
        STATE["enabled"] = st.get("enabled", False)
        STATE["positions"] = st.get("positions", {})
        STATE["trades"] = st.get("trades", [])
        for k, v in (d.get("auto") or {}).items():
            if k in AUTO:
                AUTO[k] = v
        for k, v in (d.get("cfg") or {}).items():
            if k in PERSIST_CFG and v is not None:
                CFG[k] = v
    except Exception:
        pass


def configure(**kw):
    for k, v in kw.items():
        if k in CFG and v is not None:
            CFG[k] = v
    save_state()            # 介面上的調整要留得住，不能只改記憶體
    return {k: (bool(v) if k in ("live", "dryRun") else v)
            for k, v in CFG.items() if k not in ("key", "secret")}


def status():
    pos = enrich_positions()
    return {
        "enabled": STATE["enabled"],
        "net": "LIVE 正式網" if CFG["live"] else "TESTNET 模擬網",
        "hasCreds": bool(CFG["key"] and CFG["secret"]),
        "positions": pos,
        "openPnl": round(sum((r.get("pnl") or 0) for r in pos), 2),
        "openR": round(sum((r.get("rMultiple") or 0) for r in pos), 2),
        "perf": performance(),
        "cfg": {k: v for k, v in CFG.items() if k not in ("key", "secret")},
        "lastRun": STATE["lastRun"],
        "symbols": len(_filters),
        "auto": {k: AUTO[k] for k in
                 ("on", "maxPerDay", "dailyLossR", "cooldownMin", "minScore",
                  "opened", "closedR", "blocked", "day")},
    }
