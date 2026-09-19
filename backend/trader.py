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

# 進出場邏輯有實質改動時把這個版本號往上加，新交易會帶著它，
# 績效就能分版本比較，不必靠記憶回想「那筆是改版前還是改版後」。
STRATEGY_VERSION = "v2"         # 程式碼層的改動才手動加；參數改動會自動反映在標籤裡

# 這些參數會影響交易結果；任何一個改了，就該算不同的策略
PARAM_KEYS = ("stopMode", "stopAtrMult", "maxStopPct", "minStopPct",
              "breakevenR", "trailR", "tp1R", "tp1Portion", "trailActivateR",
              "riskPct", "usablePct")


def strategy_label(params=None, min_score=None):
    """由程式版本 + 參數快照組成人看得懂的標籤。
    同一組參數的交易會落在同一桶，參數一改就自動分桶。"""
    p = params or {k: CFG.get(k) for k in PARAM_KEYS}
    ms = min_score if min_score is not None else AUTO.get("minScore")
    mode = {"tighter": "取近", "atr": "ATR", "ma": "均線"}.get(p.get("stopMode"), p.get("stopMode"))
    return (f"{STRATEGY_VERSION}·{mode}{p.get('stopAtrMult')}×/{p.get('maxStopPct'):g}-{p.get('minStopPct'):g}%"
            f"·移損{p.get('breakevenR'):g}R·回撤{p.get('trailR'):g}R"
            f"·TP{p.get('tp1R'):g}R@{p.get('tp1Portion'):g}"
            f"·險{p.get('riskPct'):g}%/{p.get('usablePct'):g}%·門檻{ms}")

TESTNET_BASE = "https://testnet.binancefuture.com"
LIVE_BASE = "https://fapi.binance.com"

CFG = {
    "key": "", "secret": "",
    "live": False,                 # 預設 Testnet
    "riskPct": 0.5,                # 單筆風險：帳戶權益的 %
    "maxPositions": 5,             # 同時最多幾個部位
    "leverage": 3,
    "stopAtrMult": 1.5,            # ATR 停損倍數
    "stopMode": "tighter",         # ma / atr / tighter（均線與 ATR 取近的）
    "maxStopPct": 12.0,            # 停損距離上限 %
    "minStopPct": 1.5,             # 停損距離下限 %
    "tp1R": 2.0,                   # 第一目標：2R 出一半
    "tp1Portion": 0.5,
    "trailCallback": 1.2,          # （舊）固定回撤 %，僅在 trailR 為 0 時使用
    "trailR": 0.5,                 # 移動停利回撤，以 R 為單位；不同波動的幣才會一致
    "breakevenR": 1.0,             # 到幾 R 把停損移到成本；0 = 關閉
    "guardClose": False,           # 停損補不回來時是否強制平倉。預設只警告
    "conflictTighten": False,      # 反向訊號通過閘門時，把持有部位的停損拉到成本
    "useTier": True,               # 風險基準用本金階梯而非實際餘額
    "usablePct": 75,               # 保證金總額上限＝階梯本金 × 此比例
    "trailActivateR": 2.0,         # 到 2R 才啟動移動停利
    "minNotional": 5.0,
    "dryRun": False,               # True 時只計算不送單，供離線驗證
}

# 會存檔的設定項。live / key / secret 一律不存。
PERSIST_CFG = ("riskPct", "maxPositions", "leverage", "stopAtrMult",
               "tp1R", "tp1Portion", "trailCallback", "trailActivateR",
               "trailR", "breakevenR", "guardClose", "stopMode", "maxStopPct", "minStopPct",
               "conflictTighten", "useTier", "usablePct")

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


MODE_MISMATCH = {-4061, -1106}   # -4061 positionSide 與帳戶模式不符；-1106 帶了不該帶的參數


def _request(method, path, params=None, signed=False, timeout=15, _retried=False):
    """回傳 (status, data)。data 解析失敗時是原始文字。

    帳戶持倉模式可能在快取的 5 分鐘內被別的程式切換，這時下單會被 -4061 / -1106 拒絕。
    遇到就強制重新偵測模式、依 _pos/_kind 重組方向參數、原單重送一次，不等快取過期。
    """
    params = dict(params or {})
    meta = {k: params.pop(k) for k in list(params) if k.startswith("_")}
    st, d = _request_raw(method, path, params, signed, timeout)
    code = d.get("code") if isinstance(d, dict) else None
    if (not _retried and meta.get("_kind") and code in MODE_MISMATCH):
        hedge = hedge_mode(force=True)
        for k in ("positionSide", "reduceOnly", "closePosition"):
            params.pop(k, None)
        params.update(_mode_keys(meta["_kind"], meta["_pos"], hedge))
        return _request(method, path, params, signed, timeout, _retried=True)
    return st, d


def _request_raw(method, path, params, signed, timeout):
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


def position_mode():
    """單向或雙向持倉。兩種都支援：下單參數由 hedge_mode() 自動適配。
    回傳 'oneway' / 'hedge' / None。"""
    st, d = _request("GET", "/fapi/v1/positionSide/dual", signed=True)
    if st == 200 and isinstance(d, dict) and "dualSidePosition" in d:
        return "hedge" if d["dualSidePosition"] else "oneway"
    return None


_bal = {"ts": 0, "data": None}


def account_balance(max_age=20):
    """合約錢包的完整餘額。短暫快取，避免每次下單與對帳都重查。

    wallet  錢包餘額（不含未實現）
    equity  保證金餘額 = 錢包 + 未實現損益，這才是帳戶真正的價值
    avail   可用餘額（扣掉已佔用的保證金）
    used    已佔用保證金
    """
    if _bal["data"] and time.time() - _bal["ts"] < max_age:
        return _bal["data"], None
    st, d = _request("GET", "/fapi/v2/account", signed=True)
    if st != 200 or not isinstance(d, dict):
        return None, (d.get("msg") or d.get("error") or f"HTTP {st}") if isinstance(d, dict) else str(st)
    try:
        f = lambda k: float(d.get(k) or 0)
        out = {"wallet": f("totalWalletBalance"), "equity": f("totalMarginBalance"),
               "avail": f("availableBalance"), "used": f("totalPositionInitialMargin"),
               "upnl": f("totalUnrealizedProfit")}
        if not out["equity"]:
            out["equity"] = out["wallet"] + out["upnl"]
        _bal["ts"] = time.time()
        _bal["data"] = out
        return out, None
    except Exception:
        return None, "回應欄位解析失敗"


def account_equity():
    """部位大小用的權益。用錢包餘額而非保證金餘額：
    浮盈浮虧會讓部位大小隨行情漂移，那不是我們要的。"""
    b, err = account_balance()
    return (b["wallet"] if b else None), err


# ── 部位大小 ────────────────────────────────────────────────



# ── 本金階梯與可動用比例 ──────────────────────────────────
#
# 兩個用途，跟 pump-dump-hunter 同一套想法：
# 1. 階梯：部位大小不隨每一塊錢連續變動，而是踩在級距上。
#    同一批交易的 1R 金額才會一致，統計才可比；獲利也不會立刻自動放大部位。
# 2. 可動用比例：保證金總額不得超過階梯本金的 75%，留緩衝給浮虧與手續費。
#    crypto-screener 的部位由風險反推，不是由資金分配，所以這裡是「上限」而非「配額」。

CAPITAL_TIERS = [500, 1000, 1500, 2000, 3000, 5000, 8000, 12000, 20000, 30000, 50000]


def tier_capital(wallet):
    """取不超過錢包餘額的最大級距；低於最小級距就用實際餘額。"""
    if not wallet:
        return None
    ok = [x for x in CAPITAL_TIERS if x <= wallet]
    return float(ok[-1]) if ok else float(wallet)


def capital_state():
    """回傳本金階梯與保證金使用狀況。"""
    b, err = account_balance()
    if not b:
        return None, err
    base = tier_capital(b["wallet"])
    usable = base * CFG.get("usablePct", 75) / 100.0
    return {"wallet": b["wallet"], "tier": base, "usablePct": CFG.get("usablePct", 75),
            "usable": round(usable, 2), "used": b["used"],
            "free": round(usable - b["used"], 2),
            "perPosCap": round(usable / max(1, CFG["maxPositions"]), 2)}, None


def size_position(equity, entry, stop, info, risk_pct=None, lev=None, margin_cap=None):
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

    # 風險基準用階梯本金，不是實際餘額：同一級距內每筆的 1R 金額固定
    base = tier_capital(equity) if CFG.get("useTier", True) else equity
    out["tier"] = base
    risk_amt = base * risk_pct / 100.0
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

    # 保證金上限：單筆不得超過配額，也不得讓總保證金超過可動用額度
    if margin_cap is not None and margin_cap > 0:
        max_notional = margin_cap * lev
        if notional > max_notional:
            qty = round_step(max_notional / entry, step)
            notional = qty * entry
            out["reason"] = f"受保證金上限縮減（可用 {margin_cap:.0f} U × {lev}x）"

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
    out["margin"] = round(notional / lev, 2) if lev else None
    return qty, out


def plan_exits(entry, stop, side):
    """由停損距離推出各級目標。R = 1 倍停損距離。

    移動停利的回撤用 R 定義再換算成百分比：
    同樣 1.2%，對停損距離 17% 的幣只有 0.07R（雜訊就掃出場），
    對 3% 的幣卻是 0.4R。固定百分比在不同波動的幣之間根本不一致。
    """
    r = abs(entry - stop)
    sgn = 1 if side == "LONG" else -1
    if CFG.get("trailR", 0) > 0 and entry > 0:
        cb = CFG["trailR"] * r / entry * 100.0
        cb = max(0.1, min(10.0, round(cb, 2)))     # 幣安限制 0.1–10%
    else:
        cb = CFG["trailCallback"]
    return {
        "R": r,
        "stop": stop,
        "tp1": entry + sgn * r * CFG["tp1R"],
        "trailActivate": entry + sgn * r * CFG["trailActivateR"],
        "trailCallback": cb,
        "breakeven": (entry + sgn * r * CFG["breakevenR"]) if CFG.get("breakevenR", 0) > 0 else None,
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

def max_leverage(symbol):
    """這個交易對目前帳戶能用的最高槓桿。新子帳戶常被限制在 5x 或更低，
    小市值幣本身的分層也可能只給 10x 以下。"""
    st, d = _request("GET", "/fapi/v1/leverageBracket", {"symbol": symbol}, signed=True)
    try:
        rows = d if isinstance(d, list) else [d]
        brackets = rows[0].get("brackets") or []
        return max(int(b.get("initialLeverage") or 0) for b in brackets) or None
    except Exception:
        return None


def set_leverage(symbol, lev):
    """設定槓桿。被拒時退而求其次用帳戶允許的最高值，
    回傳 (實際槓桿, 說明)。沉默失敗會讓保證金佔用與強平距離都跟預期不符。"""
    st, d = _request("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": int(lev)}, signed=True)
    if st == 200:
        return int(lev), None
    mx = max_leverage(symbol)
    if mx and mx < lev:
        st2, _ = _request("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": int(mx)}, signed=True)
        if st2 == 200:
            return int(mx), f"帳戶或該交易對上限 {mx}x，已改用 {mx}x（設定值 {lev}x）"
    msg = str((d or {}).get("msg") or d)[:80]
    return None, f"設定槓桿失敗（{msg}），沿用帳戶現有值"




def wait_position(symbol, want_qty, tries=12, gap=0.5, side="LONG"):
    """等部位出現在帳戶上，回傳 (實際數量, 進場均價)。

    市價單成交與 positionRisk 更新之間有延遲，直接掛條件單會被拒。
    這裡輪詢到部位出現為止，最多約 6 秒。
    """
    for i in range(tries):
        st, d = _request("GET", "/fapi/v2/positionRisk", {"symbol": symbol}, signed=True)
        if st == 200 and isinstance(d, list):
            for p in _my_side_rows(d, symbol, side):
                try:
                    amt = abs(float(p.get("positionAmt") or 0))
                    ep = float(p.get("entryPrice") or 0)
                except Exception:
                    continue
                if amt > 0:
                    return amt, (ep or None)
        time.sleep(gap)
    return 0.0, None




# ── 持倉模式（單向／雙向）────────────────────────────────────
#
# 模式是帳戶層級的設定，可能被共用同一個帳戶的其他程式切換。
# 這裡自動偵測並調整下單參數：
#   單向：用 reduceOnly 標示平倉單，不帶 positionSide
#   雙向：每張單都帶 positionSide=LONG/SHORT，且不能帶 reduceOnly（會被拒）
# positionRisk 在雙向模式下每個交易對回兩筆，要挑我們那一側。

_mode = {"hedge": None, "ts": 0}


def hedge_mode(force=False):
    if not force and _mode["hedge"] is not None and time.time() - _mode["ts"] < 300:
        return _mode["hedge"]
    pm = position_mode()
    if pm is not None:
        _mode["hedge"] = (pm == "hedge")
        _mode["ts"] = time.time()
    return bool(_mode["hedge"])


def _mode_keys(kind, side, hedge):
    """依模式產生該張單需要的方向參數。kind: entry / reduce / close。"""
    if kind == "entry":
        return {"positionSide": side} if hedge else {}
    if kind == "reduce":
        return {"positionSide": side} if hedge else {"reduceOnly": "true"}
    d = {"closePosition": "true"}
    if hedge:
        d["positionSide"] = side
    return d


def _ps(side):
    """進場單的方向參數。_pos/_kind 是給 _request 重送用的標記，送出前會剝掉。"""
    return {"_pos": side, "_kind": "entry", **_mode_keys("entry", side, hedge_mode())}


def _reduce(side):
    """帶數量的平倉單（停利、移動停利、市價平倉）。"""
    return {"_pos": side, "_kind": "reduce", **_mode_keys("reduce", side, hedge_mode())}


def _close_all(side):
    """closePosition=true 的停損單。"""
    return {"_pos": side, "_kind": "close", **_mode_keys("close", side, hedge_mode())}


def _my_side_rows(rows, symbol, side):
    """從 positionRisk 回傳裡挑出這個交易對、屬於我們方向的那一筆。"""
    out = []
    for p in rows:
        if p.get("symbol") != symbol:
            continue
        ps = p.get("positionSide", "BOTH")
        if ps == "BOTH" or ps == side:
            out.append(p)
    return out


def open_position(symbol_base, side, entry_hint, stop, info=None, note="", stop_pct=None):
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
    # 停損以百分比套到實際標記價：訊號價與成交價可能不同（模擬網尤其明顯），
    # 絕對價格直接沿用會錯位；百分比不會。
    if stop_pct is not None and stop_pct > 0:
        stop = px * (1 - stop_pct / 100.0) if side == "LONG" else px * (1 + stop_pct / 100.0)

    # 先設槓桿：名目上限與保證金上限都要用實際生效的倍數，
    # 所以這一步必須在計算部位大小之前。
    lev_used, lev_note = set_leverage(sym, CFG["leverage"])

    cap = None
    cs, _ = capital_state()
    if cs:
        cap = min(cs["perPosCap"], max(0.0, cs["free"]))
        if cap <= 0:
            return {"ok": False, "error": (
                f"保證金已用滿：可動用 {cs['usable']:.0f} U（階梯 {cs['tier']:.0f} U × {cs['usablePct']}%），"
                f"已佔用 {cs['used']:.0f} U"), "capital": cs}
    qty, detail = size_position(equity, px, stop, info, lev=lev_used or CFG["leverage"], margin_cap=cap)
    if qty <= 0:
        return {"ok": False, "error": f"部位大小不合格：{detail.get('reason')}", "detail": detail}

    exits = plan_exits(px, stop, side)
    if CFG["dryRun"]:
        return {"ok": True, "dryRun": True, "symbol": sym, "side": side,
                "qty": qty, "entry": px, "exits": exits, "sizing": detail}

    order_side = "BUY" if side == "LONG" else "SELL"
    close_side = "SELL" if side == "LONG" else "BUY"

    # newOrderRespType=RESULT 讓市價單回傳成交結果而非只回 ACK，
    # 這樣才拿得到實際成交均價。
    st, entry_res = _request("POST", "/fapi/v1/order", {
        "symbol": sym, "side": order_side, "type": "MARKET", "quantity": qty,
        "newOrderRespType": "RESULT", **_ps(side),
    }, signed=True)
    if st != 200:
        return {"ok": False, "error": f"進場失敗：{entry_res.get('msg') or entry_res}"}

    # 等部位真的出現在帳戶上再掛條件單。
    # 幣安的成交與部位更新之間有延遲，太早掛 closePosition=true 的單會被拒，
    # 錯誤訊息是「TIF GTE can only be used with open positions」。
    actual_qty, actual_entry = wait_position(sym, qty, side=side)
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
            "quantity": qty, **_reduce(side),
        }, signed=True)
        return {"ok": False, "error": (
            f"下單瞬間價格已越過預定停損（現價 {live:g}，停損 {stop_px:g}），"
            f"進場前提不成立，已立即平倉不留倉位")}

    # 停損：closePosition 確保無論部位多大都全平
    st2, r2, ep2 = place_conditional({
        "symbol": sym, "side": close_side, "type": "STOP_MARKET",
        "stopPrice": stop_px, "workingType": "MARK_PRICE", **_close_all(side),
    })
    if st2 != 200:
        errs.append(f"停損掛單失敗：{r2.get('msg') or r2}")
        # 沒有停損就不留倉
        _request("POST", "/fapi/v1/order", {
            "symbol": sym, "side": close_side, "type": "MARKET",
            "quantity": qty, **_reduce(side),
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
            "workingType": "MARK_PRICE", **_reduce(side),
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
            "quantity": trail_qty, "callbackRate": exits["trailCallback"],
            "activationPrice": round_step(exits["trailActivate"], tick),
            "workingType": "MARK_PRICE", **_reduce(side),
        })
        if st4 == 200:
            sub.append({"type": "TRAILING_STOP_MARKET", "id": r4.get("algoId") or r4.get("orderId"),
                        "activate": round_step(exits["trailActivate"], tick), "via": ep4})
        else:
            errs.append(f"移動停利掛單失敗：{r4.get('msg') or r4}")

    params = {k: CFG.get(k) for k in PARAM_KEYS}
    pos = {
        "version": strategy_label(params),
        "leverage": lev_used or CFG["leverage"],
        "params": params, "minScore": AUTO.get("minScore"),
        "symbol": sym, "side": side, "qty": qty, "entry": px,
        "stop": stop_px, "exits": exits, "sizing": detail,
        "orders": sub, "opened": int(time.time() * 1000),
        "note": note, "warnings": (errs + ([lev_note] if lev_note else [])),
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
    "cooldownMin": 120,        # 虧損平倉後多久才能再進（防報復性交易）
    "cooldownWinMin": 15,      # 獲利平倉後多久才能再進（趨勢可能還在走，只擋掉同一根 K 的來回）
    "minScore": 62,            # 自動下單的分數門檻，比手動的 58 嚴格
    "day": None,               # 當前計算中的日期
    "opened": 0,               # 今日已開倉數
    "closedR": 0.0,            # 今日已實現 R
    "closedUsd": 0.0,          # 今日已實現 U（R 對政策有意義，U 對人有意義）
    "blockedAtR": None, "blockedAtUsd": None,   # 觸發停止那一刻的數字
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
        AUTO["closedUsd"] = 0.0
        AUTO["blocked"] = None
        AUTO["blockedAtR"] = None
        AUTO["blockedAtUsd"] = None
        save_state()


def auto_can_trade(symbol):
    """回傳 (可否下單, 原因)。這裡只管風險額度，不管訊號好壞。

    針對這檔幣的原因（已有部位、冷卻中）排最前面：
    它們比全域上限更具體、更有用。否則持倉滿的時候，
    同一檔幣再觸發訊號會被報成「持倉已達上限」，看不出其實是已經持有它。
    """
    auto_roll_day()
    if not AUTO["on"]:
        return False, "自動下單未啟用"
    if not (CFG["key"] and CFG["secret"]):
        return False, "未設定幣安金鑰"

    # ── 這檔幣本身 ──
    pos = STATE["positions"].get(symbol)
    if pos:
        live = live_positions().get(symbol) or {}
        mark = live.get("mark")
        r_amt = ((pos.get("exits") or {}).get("R") or 0) * (pos.get("qty") or 0)
        rm = (live.get("pnl") / r_amt) if (mark and r_amt) else None
        held = (time.time() * 1000 - (pos.get("opened") or 0)) / 3600000
        detail = f"這檔已有部位（{'做多' if pos['side'] == 'LONG' else '做空'}，進場 {pos['entry']:g}"
        if rm is not None:
            detail += f"，目前 {rm:+.2f}R"
        detail += f"，持有 {held:.1f} 小時），不重複進場"
        return False, detail
    last = AUTO["lastClose"].get(symbol)
    if last:
        won = AUTO.get("lastCloseWin", {}).get(symbol, False)
        cd = AUTO.get("cooldownWinMin", 15) if won else AUTO["cooldownMin"]
        if (time.time() - last) < cd * 60:
            left = int((cd * 60 - (time.time() - last)) / 60)
            return False, f"剛{'獲利' if won else '虧損'}平倉，冷卻中還剩 {left} 分鐘（{'獲利後短冷卻' if won else '虧損後長冷卻'}）"

    # ── 全域額度 ──
    if AUTO["blocked"]:
        return False, AUTO["blocked"]
    if AUTO["closedR"] <= AUTO["dailyLossR"]:
        AUTO["blockedAtR"] = AUTO["closedR"]
        AUTO["blockedAtUsd"] = AUTO.get("closedUsd", 0.0)
        AUTO["blocked"] = (f"當日已虧損 {AUTO['closedR']:.2f}R（{AUTO.get('closedUsd', 0.0):+.0f} U），"
                           f"達停損上限，今日停止新開倉；已有部位仍依停損出場")
        save_state()
        return False, AUTO["blocked"]
    if AUTO["opened"] >= AUTO["maxPerDay"]:
        return False, f"今日已開 {AUTO['opened']} 筆，達上限"
    if len(STATE["positions"]) >= CFG["maxPositions"]:
        return False, f"同時持倉已達 {CFG['maxPositions']} 筆上限"
    return True, None


def auto_open(symbol_base, side, entry, stop, note="", on_event=None, stop_pct=None):
    """自動開倉。通過風險閘門才會真的送單。"""
    sym = symbol_base.upper() + "USDT"
    ok, why = auto_can_trade(sym)
    if not ok:
        return {"ok": False, "skipped": True, "error": why}

    r = open_position(symbol_base, side, entry, stop, note=note, stop_pct=stop_pct)
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
            "quantity": pos["qty"], **_reduce(pos["side"]),
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
        "id": f"{pos['symbol']}-{int(time.time() * 1000)}",
        "excluded": False,
        "version": pos.get("version") or "v1",
        "symbol": pos["symbol"], "side": pos["side"], "qty": pos["qty"],
        "entry": pos["entry"], "exit": exit_px, "pnl": round(pnl, 4),
        "rMultiple": round(pnl / r, 2) if r else None,
        "opened": pos["opened"], "closed": int(time.time() * 1000),
        "reason": reason, "note": pos.get("note", ""),
    })
    STATE["positions"].pop(pos["symbol"], None)
    conflict_on_close(pos["symbol"], STATE["trades"][-1].get("rMultiple"))
    AUTO["lastClose"][pos["symbol"]] = time.time()
    AUTO.setdefault("lastCloseWin", {})[pos["symbol"]] = (STATE["trades"][-1].get("pnl") or 0) > 0
    rm = STATE["trades"][-1].get("rMultiple")
    if rm is not None:
        auto_roll_day()
        AUTO["closedR"] += rm
        AUTO["closedUsd"] = AUTO.get("closedUsd", 0.0) + (STATE["trades"][-1].get("pnl") or 0.0)
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
        if abs(amt) <= 0:
            continue
        mine = STATE["positions"].get(p["symbol"])
        ps = p.get("positionSide", "BOTH")
        if mine and ps not in ("BOTH", mine["side"]):
            continue                     # 雙向模式下另一側不是我們的
        live[p["symbol"]] = p
    closed = []
    for sym in list(STATE["positions"].keys()):
        if sym not in live:
            pos = STATE["positions"][sym]
            px = mark_price(sym) or pos["entry"]
            record_close(pos, px, "交易所出場（停損或停利觸發）")
            closed.append(sym)
    STATE["lastRun"] = int(time.time() * 1000)
    b, _ = account_balance(max_age=0)
    return {"closed": closed, "open": list(live.keys()), "balance": b}




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
            # 雙向模式同一交易對可能有兩側；只認我們帳本記錄的那一側
            mine = STATE["positions"].get(p["symbol"])
            ps = p.get("positionSide", "BOTH")
            if mine and ps not in ("BOTH", mine["side"]):
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




# ── 影子追蹤：被持倉上限擋掉的訊號後來怎麼了 ──────────────────
#
# 「上限擋掉高分訊號很可惜」是感覺；這裡把它變成數字。
# 被擋的訊號記下進場價與停損距離，72 小時後用逐時價格回頭算：
# 先碰到停損（-1R）、先碰到 2R、還是都沒碰到（以 72 小時收盤算 R）。
# 累積一批之後，就知道上限到底擋掉了多少期望值，再決定要不要調。

MISSED = []          # 每筆 {sym, cid, side, price, stopPct, score, ts, result, r}


def missed_record(sym, cid, side, price, stop_pct, score, why):
    MISSED.append({"sym": sym, "cid": cid, "side": side, "price": price,
                   "stopPct": stop_pct, "score": score, "why": why,
                   "ts": int(time.time() * 1000), "result": None, "r": None})
    del MISSED[:-200]
    save_state()


def missed_evaluate(hourly_prices_fn, horizon_h=72):
    """每輪都走一次已有的逐時資料：先碰到停損或 2R 就立刻記結果（先碰到誰算誰），
    72 小時內都沒碰到才以 72 小時收盤算。不必等滿 72 小時才有數字。
    注意：逐時資料只有收盤價，沒有高低，小時內的針刺兩邊都會漏，方向大致對稱。
    hourly_prices_fn(cid) 回傳 [(ts_ms, price), ...]（由 main.py 提供快取的 market_chart）。"""
    now = time.time() * 1000
    done = 0
    for m in MISSED:
        if m["result"] is not None:
            continue
        expired = now - m["ts"] >= horizon_h * 3600000
        try:
            series = hourly_prices_fn(m["cid"]) or []
        except Exception:
            continue
        pts = [(t, p) for t, p in series if t >= m["ts"] and t <= m["ts"] + horizon_h * 3600000]
        if not pts or (not expired and len(pts) < 2):
            continue
        sgn = 1 if m["side"] == "LONG" else -1
        entry = m["price"]
        r_unit = entry * m["stopPct"] / 100.0
        stop = entry - sgn * r_unit
        tp2 = entry + sgn * 2 * r_unit
        res, r, hit_t = "none", None, None
        for t_, p in pts:
            if (p - stop) * sgn <= 0:
                res, r, hit_t = "stop", -1.0, t_
                break
            if (p - tp2) * sgn >= 0:
                res, r, hit_t = "tp2", 2.0, t_
                break
        if res == "none":
            if not expired:
                continue                    # 還沒碰到、也還沒到期：下一輪再看
            r = round((pts[-1][1] - entry) * sgn / r_unit, 2)
        m["result"], m["r"] = res, r
        m["hours"] = round((hit_t - m["ts"]) / 3600000, 1) if hit_t else horizon_h
        done += 1
    if done:
        save_state()
    return done


def missed_summary():
    ev = [m for m in MISSED if m["result"] is not None]
    pend = [m for m in MISSED if m["result"] is None]
    if not ev:
        return {"evaluated": 0, "pending": len(pend)}
    rs = [m["r"] for m in ev]
    return {"evaluated": len(ev), "pending": len(pend),
            "tp2": sum(1 for m in ev if m["result"] == "tp2"),
            "stop": sum(1 for m in ev if m["result"] == "stop"),
            "none": sum(1 for m in ev if m["result"] == "none"),
            "avgR": round(sum(rs) / len(rs), 2), "totalR": round(sum(rs), 2),
            "recent": [{k: m.get(k) for k in ("sym", "side", "score", "result", "r", "ts", "hours")} for m in ev[-10:]]}




# ── 反向訊號追蹤：持有中的部位收到反方向訊號時怎麼辦 ────────
#
# 先記錄、後決定。記下訊號當下部位的 R 與訊號是否通過閘門，
# 部位平倉時補上最終 R。累積後看「反向訊號之後平均再走了多少 R」：
# 明顯為負 → 該收緊；不明顯 → 該無視。
# conflictTighten 開啟時，通過閘門的反向訊號會把停損拉到成本（保護，不反手）。

CONFLICTS = []


def conflict_record(sym, held_side, held_r, sig_side, score, gate_ok):
    CONFLICTS.append({"sym": sym, "held": held_side, "rAtSignal": held_r, "sigSide": sig_side,
                      "score": score, "gateOk": gate_ok, "ts": int(time.time() * 1000),
                      "finalR": None, "closedTs": None})
    del CONFLICTS[:-200]
    save_state()


def conflict_on_close(sym, final_r):
    """部位平倉時回填最終 R（同一檔可能有多筆未回填的紀錄，全部補上）。"""
    for c in CONFLICTS:
        if c["sym"] == sym and c["finalR"] is None:
            c["finalR"] = final_r
            c["closedTs"] = int(time.time() * 1000)


def conflict_summary():
    done = [c for c in CONFLICTS if c["finalR"] is not None]
    pend = [c for c in CONFLICTS if c["finalR"] is None]
    if not done:
        return {"evaluated": 0, "pending": len(pend)}
    drift = [c["finalR"] - c["rAtSignal"] for c in done]
    gate = [c for c in done if c["gateOk"]]
    return {"evaluated": len(done), "pending": len(pend),
            "avgRAtSignal": round(sum(c["rAtSignal"] for c in done) / len(done), 2),
            "avgFinalR": round(sum(c["finalR"] for c in done) / len(done), 2),
            "avgDrift": round(sum(drift) / len(drift), 2),
            "worseAfter": sum(1 for d in drift if d < -0.3),
            "betterAfter": sum(1 for d in drift if d > 0.3),
            "gatePassed": len(gate),
            "gatePassedDrift": round(sum(c["finalR"] - c["rAtSignal"] for c in gate) / len(gate), 2) if gate else None}


# ── 績效統計 ────────────────────────────────────────────────



def open_algo_orders(symbol):
    """查這個交易對還掛著哪些條件單。

    回傳 (orders, ok)。ok=False 代表查詢本身失敗，
    呼叫端絕對不能把「查不到」當成「不存在」——這正是先前誤平倉的根源。
    """
    st, d = _request("GET", "/fapi/v1/openAlgoOrders", {"symbol": symbol}, signed=True)
    if st == 200 and isinstance(d, list):
        return d, True
    if st == 200 and isinstance(d, dict) and isinstance(d.get("orders"), list):
        return d["orders"], True
    return [], False


def _order_type(o):
    """Algo 端點的型別欄位叫 orderType，舊端點叫 type。兩個都看。"""
    return str(o.get("orderType") or o.get("type") or o.get("origType") or "").upper()


def has_stop_order(orders):
    return any(_order_type(o) in ("STOP_MARKET", "STOP", "TRAILING_STOP_MARKET")
               for o in orders)


_missing_streak = {}          # symbol → 連續幾次確認停損不在


def guard_positions():
    """確認每個部位的停損還掛著。

    設計原則（來自一次誤平倉的教訓）：
    1. 查詢失敗 ≠ 停損不在。查不到就跳過，下一輪再查。
    2. 必須連續 3 輪都確認不在，才動作。一次的讀取錯誤不該觸發任何事。
    3. 補掛被拒且原因是「已存在」→ 代表停損其實在，判斷錯了，不動作。
    4. 預設只補掛、不平倉。強制平倉需要明確開啟 CFG["guardClose"]。
    """
    if CFG["dryRun"] or not (CFG["key"] and CFG["secret"]):
        return []
    events = []
    for sym, pos in list(STATE["positions"].items()):
        orders, ok = open_algo_orders(sym)
        if not ok:
            continue                              # 原則 1
        if has_stop_order(orders):
            _missing_streak[sym] = 0
            continue

        n = _missing_streak.get(sym, 0) + 1
        _missing_streak[sym] = n
        if n < 3:                                 # 原則 2
            continue

        close_side = "SELL" if pos["side"] == "LONG" else "BUY"
        f = _filters.get(sym) or {}
        tick = f.get("tick") or 0.0
        stop_px = round_step(pos["stop"], tick) if tick else pos["stop"]

        st, r, ep = place_conditional({
            "symbol": sym, "side": close_side, "type": "STOP_MARKET",
            "stopPrice": stop_px, "workingType": "MARK_PRICE", **_close_all(pos["side"]),
        })
        msg = str((r or {}).get("msg") or r)
        if st == 200:
            _missing_streak[sym] = 0
            events.append({"symbol": sym, "action": "restored", "stop": stop_px})
            continue
        if "existing" in msg.lower() or "already" in msg.lower():
            _missing_streak[sym] = 0              # 原則 3
            events.append({"symbol": sym, "action": "false_alarm", "why": msg[:120]})
            continue

        # 到這裡：連續三輪確認不在、補掛失敗、且失敗原因不是「已存在」
        if CFG.get("guardClose"):                 # 原則 4
            _request("POST", "/fapi/v1/order", {
                "symbol": sym, "side": close_side, "type": "MARKET",
                "quantity": pos["qty"], **_reduce(pos["side"]),
            }, signed=True)
            px = mark_price(sym) or pos["entry"]
            record_close(pos, px, "停損單遺失且無法補掛，強制平倉")
            events.append({"symbol": sym, "action": "closed", "why": msg[:120]})
        else:
            events.append({"symbol": sym, "action": "alert", "why": msg[:120],
                           "stop": stop_px})
    return events


def cancel_stop_orders(symbol, pos):
    """只取消停損單，保留停利與移動停利。"""
    ok = False
    for o in pos.get("orders") or []:
        if _order_type(o) != "STOP_MARKET" or not o.get("id"):
            continue
        if o.get("via") == "algo":
            st, _ = _request("DELETE", "/fapi/v1/algoOrder",
                             {"symbol": symbol, "algoId": o["id"]}, signed=True)
        else:
            st, _ = _request("DELETE", "/fapi/v1/order",
                             {"symbol": symbol, "orderId": o["id"]}, signed=True)
        ok = ok or st == 200
    return ok


def move_to_breakeven(pos, mark, force=False):
    """到達設定的 R 倍數後，把停損移到成本價。

    移動停利要到 2R 才啟動，1R 到 2R 之間的回落無法保護；
    這一步補上那個缺口，讓「衝到 1.5R 又跌回去」的單子不再賠滿 1R。
    順序：先掛新停損，成功後才取消舊的，避免中間出現裸倉。
    """
    if pos.get("beMoved"):
        return None
    sgn = 1 if pos["side"] == "LONG" else -1
    if not force:
        be = (pos.get("exits") or {}).get("breakeven")
        if be is None or (mark - be) * sgn < 0:
            return None                   # 還沒到
    elif (mark - pos["entry"]) * sgn <= 0:
        return None                       # 強制移損也要在成本之上才有意義

    sym = pos["symbol"]
    f = _filters.get(sym) or {}
    tick = f.get("tick") or 0.0
    # 成本價加一點點手續費緩衝，避免剛好打平還倒貼手續費
    new_stop = pos["entry"] * (1 + sgn * 0.0015)
    new_stop = round_step(new_stop, tick) if tick else new_stop
    close_side = "SELL" if pos["side"] == "LONG" else "BUY"

    # 幣安不允許同方向同時有兩張 closePosition 停損，所以不能「先掛新再取消舊」。
    # 順序：取消舊的 → 掛新的 → 掛不上就立刻把舊的掛回去。裸倉窗口約一秒。
    old = pos["stop"]
    cancel_stop_orders(sym, pos)
    st, r, ep = place_conditional({
        "symbol": sym, "side": close_side, "type": "STOP_MARKET",
        "stopPrice": new_stop, "workingType": "MARK_PRICE", **_close_all(pos["side"]),
    })
    if st != 200:
        why = str(r.get("msg") or r)[:100]
        # 把舊停損掛回去，絕不留裸倉
        st2, r2, ep2 = place_conditional({
            "symbol": sym, "side": close_side, "type": "STOP_MARKET",
            "stopPrice": old, "workingType": "MARK_PRICE", **_close_all(pos["side"]),
        })
        if st2 == 200:
            pos["orders"] = [o for o in (pos.get("orders") or []) if _order_type(o) != "STOP_MARKET"]
            pos["orders"].append({"type": "STOP_MARKET", "id": r2.get("algoId") or r2.get("orderId"), "px": old, "via": ep2})
            save_state()
            return {"symbol": sym, "ok": False, "why": f"新停損掛不上（{why}），已恢復原停損"}
        # 連舊的都掛不回：守衛執行緒下一輪會補掛，這裡先回報
        return {"symbol": sym, "ok": False, "why": f"新停損掛不上且原停損也掛不回（{why}），守衛將補掛", "naked": True}

    pos["stop"] = new_stop
    pos["beMoved"] = True
    pos["orders"] = [o for o in (pos.get("orders") or []) if _order_type(o) != "STOP_MARKET"]
    pos["orders"].append({"type": "STOP_MARKET", "id": r.get("algoId") or r.get("orderId"),
                          "px": new_stop, "via": ep})
    save_state()
    return {"symbol": sym, "ok": True, "old": old, "new": new_stop, "mark": mark}


def manage_positions():
    """主動管理：目前只有移損到成本。由部位監看執行緒每輪呼叫。"""
    if CFG["dryRun"] or not (CFG["key"] and CFG["secret"]):
        return []
    if CFG.get("breakevenR", 0) <= 0:
        return []
    live = live_positions(max_age=5)
    out = []
    for sym, pos in list(STATE["positions"].items()):
        L = live.get(sym)
        if not L:
            continue
        try:
            ev = move_to_breakeven(pos, L["mark"])
            if ev:
                out.append(ev)
        except Exception as e:
            out.append({"symbol": sym, "ok": False, "why": str(e)[:100]})
    return out


def _stats(t):
    """一組交易的核心統計。performance() 與分版本統計共用。"""
    if not t:
        return {"count": 0}
    wins = [x for x in t if (x["pnl"] or 0) > 0]
    losses = [x for x in t if (x["pnl"] or 0) <= 0]
    win_r = [x["rMultiple"] for x in wins if x.get("rMultiple") is not None]
    loss_r = [x["rMultiple"] for x in losses if x.get("rMultiple") is not None]
    rs = [x["rMultiple"] for x in t if x.get("rMultiple") is not None]
    avg_w = sum(win_r) / len(win_r) if win_r else 0.0
    avg_l = sum(loss_r) / len(loss_r) if loss_r else 0.0
    wr = len(wins) / len(t)
    return {"count": len(t), "wins": len(wins), "winRate": round(wr * 100, 1),
            "avgWinR": round(avg_w, 2), "avgLossR": round(avg_l, 2),
            "payoff": round(abs(avg_w / avg_l), 2) if avg_l else None,
            "expectancyR": round(wr * avg_w + (1 - wr) * avg_l, 3),
            "totalR": round(sum(rs), 2) if rs else None}


def performance_by_version():
    """分策略版本的績效，讓改版前後可以直接比較。"""
    groups = {}
    for x in STATE["trades"]:
        if x.get("excluded"):
            continue
        groups.setdefault(x.get("version") or "v1", []).append(x)
    return {v: _stats(ts) for v, ts in sorted(groups.items())}


def performance():
    """以 R 倍數為核心。大賺小賠的關鍵不是勝率，是平均獲利 R 要明顯大於
    平均虧損 R，所以這裡把兩者分開列出。

    被標記排除的交易不列入統計——例如系統故障造成的非策略出場，
    算進去會汙染對策略本身的評估。"""
    t = [x for x in STATE["trades"] if not x.get("excluded")]
    excluded_n = len(STATE["trades"]) - len(t)
    if not t:
        return {"count": 0, "excluded": excluded_n}
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
        "count": len(t), "excluded": excluded_n,
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

def set_excluded(trade_id, excluded):
    for t in STATE["trades"]:
        if t.get("id") == trade_id:
            t["excluded"] = bool(excluded)
            save_state()
            return True
    return False


def save_state():
    if not STATE_FILE:
        return
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"state": {k: STATE.get(k) for k in ("enabled", "positions", "trades", "lastNet")},
                       "missed": MISSED, "conflicts": CONFLICTS,
                       "auto": AUTO,
                       # 金鑰與網路別刻意不存：金鑰只該在環境變數，
                       # 網路別只該由啟動參數決定，避免存檔把正式網狀態帶回來
                       "cfg": {k: CFG[k] for k in PERSIST_CFG}}, f)
    except Exception:
        pass


def state_path(cache_dir, live):
    """正式網與模擬網各自一份帳本。切換網路不會讓另一邊的部位被誤判平倉，
    績效也永遠不會混在一起。"""
    name = "trader.live.json" if live else "trader.testnet.json"
    p = os.path.join(cache_dir, name)
    # 相容：舊的單一帳本 trader.json 是模擬網時期的，第一次搬過去
    legacy = os.path.join(cache_dir, "trader.json")
    if not live and not os.path.exists(p) and os.path.exists(legacy):
        try:
            os.replace(legacy, p)
        except OSError:
            pass
    return p


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
        STATE["lastNet"] = st.get("lastNet")
        for i, t in enumerate(STATE["trades"]):
            if not t.get("id"):
                t["id"] = f"{t.get('symbol', 'X')}-{t.get('closed') or i}"
            t.setdefault("excluded", False)
            if not t.get("version") or t["version"] in ("v1", "v2-atr"):
                note = t.get("note") or ""
                t["version"] = ("v2·早期（參數未記錄）" if ("（均線）" in note or "（ATR）" in note)
                                else "v1·均線停損")
            if t.get("rMultiple") is not None:
                t["rMultiple"] = round(float(t["rMultiple"]), 2)
            if t.get("pnl") is not None:
                t["pnl"] = round(float(t["pnl"]), 4)
        for k, v in (d.get("auto") or {}).items():
            if k in AUTO:
                AUTO[k] = v
        for k, v in (d.get("cfg") or {}).items():
            if k in PERSIST_CFG and v is not None:
                CFG[k] = v
        MISSED[:] = d.get("missed") or []
        CONFLICTS[:] = d.get("conflicts") or []
    except Exception:
        pass


def configure(**kw):
    for k, v in kw.items():
        if k in CFG and v is not None:
            CFG[k] = v
    save_state()            # 介面上的調整要留得住，不能只改記憶體
    return {k: (bool(v) if k in ("live", "dryRun") else v)
            for k, v in CFG.items() if k not in ("key", "secret")}


_eq_memo = {"ts": 0, "eq": None}


def _one_r_usd():
    """1R 大約等於多少 U：最近權益 × 單筆風險 %。權益每 5 分鐘查一次。"""
    if time.time() - _eq_memo["ts"] > 300 and CFG["key"] and CFG["secret"] and not CFG["dryRun"]:
        eq, _ = account_equity()
        if eq is not None:
            _eq_memo["eq"] = eq
        _eq_memo["ts"] = time.time()
    eq = _eq_memo["eq"]
    return round(eq * CFG["riskPct"] / 100.0, 2) if eq else None


def status():
    pos = enrich_positions()
    return {
        "enabled": STATE["enabled"],
        "net": "LIVE 正式網" if CFG["live"] else "TESTNET 模擬網",
        "positionMode": ("hedge" if _mode["hedge"] else "oneway") if _mode["hedge"] is not None else None,
        "autoOn": AUTO["on"],
        "hasCreds": bool(CFG["key"] and CFG["secret"]),
        "positions": pos,
        "openPnl": round(sum((r.get("pnl") or 0) for r in pos), 2),
        "balance": (account_balance()[0] if (CFG["key"] and CFG["secret"] and not CFG["dryRun"]) else None),
        "capital": (capital_state()[0] if (CFG["key"] and CFG["secret"] and not CFG["dryRun"]) else None),
        "openR": round(sum((r.get("rMultiple") or 0) for r in pos), 2),
        "perf": performance(),
        "perfByVersion": performance_by_version(),
        "missed": missed_summary(),
        "conflicts": conflict_summary(),
        "strategyVersion": strategy_label(),
        "trades": list(reversed(STATE["trades"][-100:])),
        "cfg": {k: v for k, v in CFG.items() if k not in ("key", "secret")},
        "lastRun": STATE["lastRun"],
        "symbols": len(_filters),
        "auto": {**{k: AUTO.get(k) for k in
                    ("on", "maxPerDay", "dailyLossR", "cooldownMin", "minScore",
                     "opened", "closedR", "closedUsd", "blocked", "blockedAtR", "blockedAtUsd", "day",
                 "cooldownWinMin")},
                 "oneR": _one_r_usd()},
    }
