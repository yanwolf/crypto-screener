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
import sys
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
    "positionPoll": 20,            # 重試迴圈間隔秒數，由 main 依 POSITION_POLL 設定（告警文字用）
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


# ── 引擎鎖（BINANCE_LESSONS 第 8 條 r48、r49）──────────────────────
# 背景的對帳／出場管理／守衛、自動下單、網頁的交易操作是不同執行緒。以前沒有任何鎖：
# 背景那條查交易所的幾秒之間，網頁手動平倉照樣進得來——背景手上是舊的判斷，接著照舊判斷補掛停損、送單；
# 自動下單與手動下單同一檔可能兩張都送出；存檔時另一條執行緒增刪部位，json.dump 直接丟例外。
# 鎖加在**函式本身**（裝飾器），不是加在某一個呼叫端（r48：只加在背景迴圈那層，直接呼叫時照樣交錯）。
# 可重入：平倉裡面再結帳、存檔不會卡住。網頁那條用 engine_wait(秒) 設等待上限，拿不到丟 EngineBusy。
_engine_lock = threading.RLock()
_engine_tl = threading.local()
ENGINE_OPS = []


class EngineBusy(Exception):
    """網頁那條等引擎鎖超過上限：背景正在處理。"""


def engine_op(fn):
    import functools

    @functools.wraps(fn)
    def wrap(*a, **k):
        t = getattr(_engine_tl, "timeout", None)
        if not _engine_lock.acquire(timeout=-1 if t is None else t):
            raise EngineBusy(fn.__name__)
        try:
            return fn(*a, **k)
        finally:
            _engine_lock.release()
    wrap.engine_op = True
    ENGINE_OPS.append(fn.__name__)
    return wrap


class engine_wait:
    """with engine_wait(10): —— 這條執行緒接下來拿引擎鎖最多等幾秒（網頁請求用；背景不設＝一直等）。"""

    def __init__(self, seconds):
        self.s = seconds

    def __enter__(self):
        self.old = getattr(_engine_tl, "timeout", None)
        _engine_tl.timeout = self.s
        return self

    def __exit__(self, *exc):
        _engine_tl.timeout = self.old
        return False


class engine_section:
    """整段拿引擎鎖（main 直接改 AUTO 之類不經過函式的地方）；等待上限照 engine_wait。"""

    def __enter__(self):
        t = getattr(_engine_tl, "timeout", None)
        if not _engine_lock.acquire(timeout=-1 if t is None else t):
            raise EngineBusy("engine_section")
        return self

    def __exit__(self, *exc):
        _engine_lock.release()
        return False
_time_offset = [0]                 # 伺服器與幣安的時鐘差

STATE = {
    "enabled": False,
    "leftovers": [],               # 平倉後撤不掉的條件單，每輪重試（第 13 條）
    "pending": {},                 # symbol → 已送單、尚未記帳的部位（第 3 條）
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


def alert_due(n):
    """失敗第 n 次是否該告警（BINANCE_LESSONS 第 8 條，r6 三個專案統一的節奏）。

    第 1、5、30 次各一次，之後每 120 次一次（150、270、390…）；恢復時由呼叫端另發一次。
    次數不等於時間：本專案的重試迴圈持倉時每 POSITION_POLL（預設 20）秒一輪，
    所以第 5 次約 1 分 20 秒、第 30 次約 10 分鐘、之後每 120 次約 40 分鐘。
    """
    return n in (1, 5, 30) or (n > 30 and (n - 30) % 120 == 0)


_outbox = []                   # [{"title", "text"}]，由 main 的背景迴圈送 Telegram


def _notify(title, text):
    """在失敗（或恢復）發生的函式裡直接產生通知（BINANCE_LESSONS 第 8 條 r10）。
    不再交給呼叫端決定要不要發：呼叫端有好幾個，漏掉一個就會少一則告警。"""
    _outbox.append({"title": title, "text": text, "ts": int(time.time() * 1000)})
    del _outbox[:-200]


def drain_alerts():
    out = list(_outbox)
    _outbox.clear()
    return out


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
        # 被拒就證明這張單的假設錯了（第 7 條）：先作廢快取、重新偵測；
        # 偵測也失敗時，反轉的是「這張單送出時的假設」——帶了 positionSide 就是當雙向送的。
        # 不能反轉快取：快取可能從沒偵測成功過（None），也可能已被別的執行緒改過，
        # 用它來反轉會用同一個錯誤假設再送一次。
        assumed_hedge = "positionSide" in params
        _mode["ts"] = 0                                    # 作廢；下一張單會重新偵測
        pm = position_mode()
        hedge = (pm == "hedge") if pm is not None else (not assumed_hedge)
        for k in ("positionSide", "reduceOnly", "closePosition"):
            params.pop(k, None)
        params.update(_mode_keys(meta["_kind"], meta["_pos"], hedge))
        # r10：快取在重送「之後」才寫。重送成功 → 寫成成功的假設；
        # 重送也因模式被拒 → 清掉，不留一個沒驗證過的值給下一張單（也給另一條執行緒）用。
        st2, d2 = _request(method, path, params, signed, timeout, _retried=True)
        code2 = d2.get("code") if isinstance(d2, dict) else None
        if st2 == 200:
            _mode["hedge"], _mode["ts"] = hedge, time.time()
        elif code2 in MODE_MISMATCH:
            _mode["hedge"], _mode["ts"] = None, 0
        return st2, d2
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
_algo_retry_at = [0.0]          # 退回舊端點的期限：過了就重新試 Algo（第 1 條 r13）
ALGO_FALLBACK_SEC = 600


def algo_fallback_active():
    """目前是否正在走舊端點（404 之後、期限未到）。"""
    return _algo_supported[0] is False and time.time() < _algo_retry_at[0]


def place_conditional(params: dict):
    """送出條件單。回傳 (status, data, 用了哪個端點)。

    第 1 條：只有 404 才退回舊端點，而且退回有期限（ALGO_FALLBACK_SEC），過了就重新試 Algo；
    退回期間舊端點若回 -4120，代表交易所明確要求用 Algo，立刻改回並重送這一張。
    """
    algo = dict(params)
    algo["algoType"] = "CONDITIONAL"
    if "stopPrice" in algo:
        algo["triggerPrice"] = algo.pop("stopPrice")
    if "activationPrice" in algo:
        algo["activatePrice"] = algo.pop("activationPrice")

    def _via_algo():
        st, d = _request("POST", "/fapi/v1/algoOrder", algo, signed=True)
        if st == 200:
            _algo_supported[0] = True
            _algo_retry_at[0] = 0.0
        return st, d

    if not algo_fallback_active():
        st, d = _via_algo()
        if st != 404:
            return st, d, "algo"
        _algo_supported[0] = False
        _algo_retry_at[0] = time.time() + ALGO_FALLBACK_SEC

    st, d = _request("POST", "/fapi/v1/order", dict(params), signed=True)
    code = d.get("code") if isinstance(d, dict) else None
    if code == -4120:
        _algo_supported[0] = None
        _algo_retry_at[0] = 0.0
        st, d = _via_algo()
        return st, d, "algo"
    return st, d, "legacy"


def cancel_position_orders(symbol, pos, only_type=None):
    """用記錄下來的 algoId / orderId 精準撤掉這個部位的條件單（BINANCE_LESSONS 第 7 條）。

    不用 symbol 全撤：共用帳號時會把別的專案同一個幣的掛單一起撤掉。
    已經不存在（觸發過或被撤過）視為成功，回傳 (撤掉張數, 已不存在張數, 失敗清單)。
    """
    done = gone = 0
    failed = []
    for o in pos.get("orders") or []:
        if not o.get("id"):
            continue
        if only_type and _order_type(o) != only_type:
            continue
        if o.get("via") == "algo":
            st, d = _request("DELETE", "/fapi/v1/algoOrder", {"symbol": symbol, "algoId": o["id"]}, signed=True)
        else:
            st, d = _request("DELETE", "/fapi/v1/order", {"symbol": symbol, "orderId": o["id"]}, signed=True)
        if st == 200:
            done += 1
            continue
        code = d.get("code") if isinstance(d, dict) else None
        msg = str((d or {}).get("msg") if isinstance(d, dict) else d)
        # -2011 Unknown order / 不存在：已經觸發或被撤過，不是錯誤
        if code in (-2011, -2013) or "Unknown" in msg or "not exist" in msg.lower():
            gone += 1
        else:
            failed.append({"order": o, "why": msg[:80],
                           "text": f"{_order_type(o)}#{o['id']}：{msg[:60]}"})
    return done, gone, failed


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




def wait_position(symbol, want_qty, tries=12, gap=0.5, side="LONG", base=0.0, min_qty=None):
    """等部位出現在帳戶上，回傳 (實際數量, 進場均價)。

    市價單成交與 positionRisk 更新之間有延遲，直接掛條件單會被拒。
    這裡輪詢到部位出現為止，最多約 6 秒。min_qty：已知這張單成交了多少時，等到部位反映到這個數量
    （到時間仍不足就回最後看到的數量）。
    """
    seen = (0.0, None)
    for i in range(tries):
        st, d = _request("GET", "/fapi/v2/positionRisk", {"symbol": symbol}, signed=True)
        if st == 200 and isinstance(d, list):
            for p in d:
                # 幣＋方向（第 7 條）：單向模式要看正負號，別的專案同幣反向的部位不算
                if p.get("symbol") != symbol or _row_side(p) != side:
                    continue
                try:
                    amt = abs(float(p.get("positionAmt") or 0))
                    ep = float(p.get("entryPrice") or 0)
                except Exception:
                    continue
                if amt - base > 1e-12:              # r14：扣掉送單前就有的
                    seen = (amt - base, (ep or None))
                    # 第 15 條：知道這張單成交了多少時，要等部位反映到那個數量——
                    # 回應是 NEW 時部位可能只更新了一部分，第一次看到 >0 就回傳會把部分當全部
                    if not min_qty or amt - base >= float(min_qty) * (1 - 1e-9) - 1e-12:
                        return seen
        time.sleep(gap)
    return seen


# ── 市價單的成交確認（BINANCE_LESSONS 第 15 條）──────────────
#
# 幣安市價單的回應：沒帶 newOrderRespType=RESULT 時是 ACK——status NEW、executedQty 0、avgPrice 0；
# 帶了 RESULT 通常是 FILLED，但也可能回 NEW（撮合還沒回報）、EXPIRED（流動性不夠，只成交一部分或 0）。
# 「API 沒回錯誤」只代表交易所收下了這張單，不代表成交。平倉單把「收下」當成「成交」，
# 就會接著撤停損、結帳——交易所上的部位還在，停損卻沒了。
FINAL_STATUS = ("FILLED", "EXPIRED", "CANCELED", "REJECTED", "EXPIRED_IN_MATCH")


def _fnum(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def order_outcome(resp):
    """純函式：從下單／查單回應讀出成交結果（自檢也用它）。
    回傳 {status, executed, avg, final, orderId}；executed 讀不到是 None（不是 0，也不是送出的數量）。"""
    d = resp if isinstance(resp, dict) else {}
    status = str(d.get("status") or "").upper()
    avg = _fnum(d.get("avgPrice")) or 0.0
    return {"status": status, "executed": _fnum(d.get("executedQty")), "avg": avg if avg > 0 else 0.0,
            "final": status in FINAL_STATUS, "orderId": d.get("orderId")}


def _order_fill_avg(sym, order_id):
    """第 15 條 r42：回應沒有均價時，用那張單號在成交明細裡的成交算加權均價（實際成交價，不是估算）。
    回傳 (成交量, 均價)；查不到回 (None, None)——記未知，不猜。"""
    if order_id is None:
        return None, None
    st, d = _request("GET", "/fapi/v1/userTrades", {"symbol": sym, "orderId": order_id}, signed=True)
    if st != 200 or not isinstance(d, list):
        return None, None
    rows = [x for x in d if str(x.get("orderId")) == str(order_id)]
    q = sum(_fnum(x.get("qty")) or 0 for x in rows)
    if q <= 0:
        return None, None
    return q, sum((_fnum(x.get("qty")) or 0) * (_fnum(x.get("price")) or 0) for x in rows) / q


def _settle_market(sym, resp, tries=8, gap=0.5):
    """市價單送出後確認最終成交（第 15 條）。回應不是最終狀態就用單號查，查到最終狀態為止；
    查了 tries 次還不是最終狀態 → 撤掉這張單（不讓它之後才成交、變成沒人知道的數量），再查一次。
    回傳 order_outcome 的格式，另加 note（過程說明）。final=False＝最後仍確定不了。"""
    o = order_outcome(resp)
    oid = o["orderId"]
    notes = []
    i = 0
    while not o["final"] and oid is not None and i < tries:
        time.sleep(gap)
        i += 1
        st, d = _request("GET", "/fapi/v1/order", {"symbol": sym, "orderId": oid}, signed=True)
        if st == 200 and isinstance(d, dict) and d.get("status"):
            o = dict(order_outcome(d), orderId=oid)
    if not o["final"] and oid is not None:
        st, d = _request("DELETE", "/fapi/v1/order", {"symbol": sym, "orderId": oid}, signed=True)
        notes.append(f"{tries} 次查詢仍是 {o['status'] or '沒有狀態'}，已撤單（HTTP {st}）")
        st, d = _request("GET", "/fapi/v1/order", {"symbol": sym, "orderId": oid}, signed=True)
        if st == 200 and isinstance(d, dict) and d.get("status"):
            o = dict(order_outcome(d), orderId=oid)
    o["note"] = "；".join(notes)
    return o




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


@engine_op
def open_position(symbol_base, side, entry_hint, stop, info=None, note="", stop_pct=None):
    """進場：市價單 + 停損單 + 部分停利 + 移動停利。

    停損一定在進場後立刻掛出。如果掛停損失敗，會立刻market平掉剛進的倉，
    因為沒有停損的部位違反這套系統的前提。
    """
    if STATE.get("loadError"):
        # 手動開倉也不經過 auto_can_trade，這裡再擋一次（第 8 條 r38：讀不到持倉紀錄時不開新倉）
        return {"ok": False, "error": "持倉紀錄還沒載入（狀態檔讀不到），暫停開新倉"}
    ok, sym, msg, finfo = check_tradable(symbol_base)
    if not ok:
        return {"ok": False, "error": msg}
    info = info or finfo
    # r48 對照時發現：手動開倉不經過 auto_can_trade，同一檔已有部位或正在等確認時照樣送單，
    # 記帳時直接蓋掉原本那筆（原本的停損、停利從此沒人管）。引擎鎖讓兩張單排隊，這裡擋第二張。
    if sym in STATE["positions"]:
        return {"ok": False, "error": f"{sym} 已有部位，不重複進場（要加碼請先平倉）"}
    if sym in (STATE.get("pending") or {}):
        return {"ok": False, "error": f"{sym} 剛送出進場單、還在等交易所確認，不重複下單"}

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
    # 送單前先寫 pending（BINANCE_LESSONS 第 3 條）：市價單送出後到記帳之間
    # 任何一處拋例外，下一輪對帳都能把交易所上的部位認領回來，不會變成沒人管的孤兒倉。
    params = {k: CFG.get(k) for k in PARAM_KEYS}
    # r14：送單前先記下這一側原有的數量。共用帳號、先前的孤兒倉、同側加碼都會讓交易所那一列
    # 包含不屬於這張單的部位；成交確認與認領都要扣掉它，否則數量與均價都會錯。
    base, base_entry = _live_row(sym, side)
    base_warn = None
    if base is None:
        # 第 3 條 r16：基準查不到就不送單——當成 0 送出，之後認領、平倉都會把別人的部位算成自己的
        return {"ok": False, "error": "送單前查不到這一側原有數量（基準），這次不下單"}
    STATE["pending"][sym] = {"side": side, "qty": qty, "stop": stop, "stopPct": stop_pct,
                             "note": note, "params": params, "minScore": AUTO.get("minScore"),
                             "leverage": lev_used or CFG["leverage"], "base": base,
                             "baseEntry": base_entry if base else None,
                             "ts": int(time.time() * 1000)}
    save_state()

    st, entry_res = _request("POST", "/fapi/v1/order", {
        "symbol": sym, "side": order_side, "type": "MARKET", "quantity": qty,
        "newOrderRespType": "RESULT", **_ps(side),
    }, signed=True)
    if st != 200:
        if st == 0 or st >= 500:
            # 逾時或伺服器錯誤：單可能已經成交（第 8 條 r11）。保留 pending，
            # 下一輪對帳若在交易所找到部位就認領並補掛停損；2 分鐘沒找到才丟掉。
            return {"ok": False, "uncertain": True,
                    "error": f"進場回應異常（{entry_res.get('error') or entry_res.get('msg') or st}），"
                             f"可能已成交，對帳會確認並接手"}
        STATE["pending"].pop(sym, None)
        save_state()
        return {"ok": False, "error": f"進場失敗：{entry_res.get('msg') or entry_res}"}

    # 第 15 條：HTTP 200 只代表交易所收下了，不代表成交。看 status 與 executedQty；不是最終狀態就用單號查到最終狀態。
    oc = _settle_market(sym, entry_res)
    if oc["final"] and not (oc["executed"] or 0) > 0:
        # 交易所明確說這張單最後沒有成交（EXPIRED／CANCELED 且成交 0）：不會有部位，pending 丟掉
        STATE["pending"].pop(sym, None)
        save_state()
        return {"ok": False, "error": f"進場市價單沒有成交（{oc['status']}，成交 0）" + (f"；{oc['note']}" if oc["note"] else "")}

    # ── 市價單已成交，之後的步驟都包起來（BINANCE_LESSONS 第 8 條 r11）──
    # 送單成功就是「已成交」。後續任何一步（查部位、算位階、記帳、掛條件單）丟例外，
    # 都不能把整筆改寫成「下單失敗」：帳上若已記了部位就回報成功並附警告；
    # 還沒記帳就保留 pending，下一輪對帳認領並補掛停損。今日開倉數也要算進去（auto_open 看 filled）。
    try:

        # 等部位真的出現在帳戶上再掛條件單。
        # 幣安的成交與部位更新之間有延遲，太早掛 closePosition=true 的單會被拒，
        # 錯誤訊息是「TIF GTE can only be used with open positions」。
        exec_qty = oc["executed"] if (oc["final"] and (oc["executed"] or 0) > 0) else None
        actual_qty, actual_entry = wait_position(sym, qty, side=side, base=base, min_qty=exec_qty)
        entry_note = None
        if oc["avg"] <= 0 and (oc["executed"] or 0) > 0:
            _fq, favg = _order_fill_avg(sym, oc["orderId"])       # r42：均價缺漏時從成交明細算
            if favg:
                oc["avg"] = favg
        if oc["avg"] > 0:
            # 這張單自己的成交均價（第 15 條：RESULT、查單、或成交明細）；有基準時交易所的 entryPrice 是合併過的
            actual_entry = oc["avg"]
        elif base > 0:
            actual_entry = None
            entry_note = "進場成交價查不到（回應、查單、成交明細都沒有），出場位階暫用標記價估計；這筆損益記未知"
        if actual_qty <= 0:
            # pending 留著：之後部位若出現，對帳會認領並補掛停損
            return {"ok": False, "error": "進場單已送出，但 6 秒內查不到部位；已記為待認領，下一輪對帳會自動接手"}
        # 第 15 條：真實數量用成交的，不用送出的。交易所給了最終成交量就用它；
        # 給不了（查不到最終狀態）才用部位增加的量，並記警告。
        step_ = (info or {}).get("step") or 0
        if exec_qty is not None:
            if abs(actual_qty - exec_qty) > max(step_ * 0.5, 1e-9):
                errs_pre = f"成交量 {exec_qty:g} 與部位增加量 {actual_qty:g} 不一致，以成交量為準"
                base_warn = errs_pre
            qty = exec_qty
        else:
            base_warn = f"進場單最終成交狀態確認不了（{oc['status'] or '沒有狀態'}），數量以部位增加量 {actual_qty:g} 為準" \
                        + (f"；{oc['note']}" if oc["note"] else "")
            qty = actual_qty
        if actual_entry:
            px = actual_entry            # 用實際成交均價重算出場位階
            exits = plan_exits(px, stop, side)

        tick = (info or {}).get("tick") or 0.01
        stop_px = round_step(stop, tick)
        # 出場位階必須用「實際掛出去的停損價」算：用未取整的停損算，R 與各級目標
        # 會跟真正的停損差一個跳動點，績效的 R 倍數也跟著偏（tests/test_parity.py 抓到的）
        exits = plan_exits(px, stop_px, side)
        sub, errs = [], []

        # 掛停損前用「當下」的標記價再檢查一次。
        # 訊號產生到實際下單之間可能隔了幾分鐘，波動大的幣可能已經跌破預定停損；
        # 這時掛單會被拒（Order would immediately trigger），
        # 而且更重要的是——這筆的前提已經不成立，不該留倉。
        live = mark_price(sym) or px
        buf = 0.002                      # 0.2% 緩衝，避免掛在剛好觸發的邊緣
        breached = (stop_px >= live * (1 - buf)) if side == "LONG" else (stop_px <= live * (1 + buf))
        if breached:
            closed, cwhy = _market_close(sym, side, qty, base)
            if closed is not True:
                # 平不掉：記帳＋待平倉，守衛補掛停損、每輪重試平倉（第 8 條 r13）
                STATE["positions"][sym] = {
                    "version": strategy_label(params), "leverage": lev_used or CFG["leverage"],
                    "params": params, "minScore": AUTO.get("minScore"), "symbol": sym, "side": side,
                    "qty": qty, "qty0": qty, "entry": px, "stop": stop_px, "exits": exits, "sizing": detail,
                    "orders": [], "opened": int(time.time() * 1000), "note": note,
                    "warnings": [f"進場瞬間已穿過停損，市價平倉被拒：{cwhy}"], "base": base}
                STATE["pending"].pop(sym, None)
                if _close_partial.get(sym):
                    STATE["positions"][sym]["closePartial"] = True    # 第 15 條：跨重啟也要知道出場價不能只看最後一張
                _set_pending_close(STATE["positions"][sym], "進場瞬間已穿過停損", cwhy)
                save_state()
                _notify("⚠ 進場後平倉失敗，部位仍在", f"{sym} 下單瞬間已穿過預定停損 {stop_px:g}，市價平倉被拒：{cwhy}\n"
                                                  f"已記入帳上，守衛會嘗試補掛停損。")
                return {"ok": False, "filled": True, "error": f"進場瞬間穿過停損，平倉被拒：{cwhy}"}
            STATE["pending"].pop(sym, None)
            _last_close_fill.pop(sym, None)       # 沒有結帳，不留給下一筆同幣的部位用（第 15 條對照時發現）
            _close_partial.pop(sym, None)
            save_state()
            return {"ok": False, "error": (
                f"下單瞬間價格已越過預定停損（現價 {live:g}，停損 {stop_px:g}），"
                f"進場前提不成立，已立即平倉不留倉位")}

        # 成交確認後立刻記帳，再掛條件單（第 3 條的順序：pending → 記帳 → 掛停損）。
        # sub 與 pos["orders"] 是同一個串列，後面掛上的單會直接出現在帳上。
        pos = {
            "version": strategy_label(params),
            "leverage": lev_used or CFG["leverage"],
            "params": params, "minScore": AUTO.get("minScore"), "base": base,
            "symbol": sym, "side": side, "qty": qty, "qty0": qty, "entry": px,
            "stop": stop_px, "exits": exits, "sizing": detail,
            "orders": sub, "opened": int(time.time() * 1000),
            "note": note, "warnings": errs + ([base_warn] if base_warn else []) + ([entry_note] if entry_note else []),
        }
        if entry_note:
            pos["entryUnverified"] = True             # r42：進場價是估的，結帳時損益記未知
        STATE["positions"][sym] = pos
        STATE["pending"].pop(sym, None)
        try:
            _mark_boundary(sym, pos, (entry_res or {}).get("orderId"))   # 起始界線在開倉當下記（r35）
        except Exception as e:
            _pos_step_error("記成交界線", sym, e)
        save_state()

        # 停損：統一走 place_stop（第 2 條 r20）。部位剛由成交確認過，直接帶自己的數量
        _s, st2, r2, ep2 = place_stop(pos, stop_px, own=pos["qty"])
        if st2 != 200:
            errs.append(f"停損掛單失敗：{r2.get('msg') or r2}")
            # 沒有停損就不留倉
            closed, cwhy = _market_close(sym, side, qty, base, pos=pos)
            if closed is not True:
                pos["warnings"].append(f"停損掛不上，市價平倉也被拒：{cwhy}")
                _set_pending_close(pos, "停損掛不上", cwhy)
                save_state()
                _notify("⚠ 停損掛不上且平倉失敗", f"{sym} 停損掛不上：{'；'.join(errs)}\n市價平倉也被拒：{cwhy}\n"
                                                f"部位保留在帳上，守衛會繼續補掛停損。")
                return {"ok": False, "filled": True, "error": "；".join(errs) + f"　平倉也被拒：{cwhy}"}
            STATE["positions"].pop(sym, None)     # 沒掛上停損就平掉，不列入績效（跟以前一致）
            _last_close_fill.pop(sym, None)       # 沒有結帳，不留給下一筆同幣的部位用
            _close_partial.pop(sym, None)
            save_state()
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

        if lev_note:
            errs.append(lev_note)
        save_state()
        return {"ok": True, **pos}
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:120]}"
        sys.stderr.write(f"  ! {sym} 已成交，後續步驟例外：{err}\n")
        pos_ = STATE["positions"].get(sym)
        if pos_ is not None:
            pos_.setdefault("warnings", []).append(f"成交後的步驟出錯：{err}")
            save_state()
            placed = [o.get("type") for o in pos_.get("orders") or []] or "無"
            _notify("⚠ 已開倉，但後續步驟出錯", f"{sym} 市價單已成交並記帳，之後出錯：{err}\n"
                    f"掛上的單：{placed}。守衛會檢查停損並補掛。")
            return {"ok": True, **pos_, "warnings": pos_["warnings"]}
        _notify("⚠ 已送出進場單，但記帳前出錯", f"{sym} 市價單已送出，記帳前出錯：{err}\n"
                "已保留待認領紀錄，下一輪對帳會在交易所找到部位並補掛停損。")
        return {"ok": False, "filled": True, "error": f"已成交，記帳前出錯：{err}（對帳會接手）"}




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
        AUTO["unknownToday"] = 0
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
        op_ = pos.get("opened")
        held = (time.time() * 1000 - op_) / 3600000 if isinstance(op_, (int, float)) else None
        detail = f"這檔已有部位（{'做多' if pos['side'] == 'LONG' else '做空'}，進場 {pos['entry']:g}"
        if rm is not None:
            detail += f"，目前 {rm:+.2f}R"
        detail += (f"，持有 {held:.1f} 小時）" if held is not None else "，持有時間未知）") + "，不重複進場"
        return False, detail
    last = AUTO["lastClose"].get(symbol)
    if last:
        won = AUTO.get("lastCloseWin", {}).get(symbol, False)
        # 損益未知（None）：套較長的冷卻（只是等久一點，不影響統計），但不寫成「虧損」
        cd = AUTO.get("cooldownWinMin", 15) if won is True else AUTO["cooldownMin"]
        if (time.time() - last) < cd * 60:
            left = int((cd * 60 - (time.time() - last)) / 60)
            what = "獲利" if won is True else ("虧損" if won is False else "平倉（損益未知）")
            kind = "獲利後短冷卻" if won is True else ("虧損後長冷卻" if won is False else "損益未知，採較長冷卻")
            return False, f"剛{what}{'平倉' if won is not None else ''}，冷卻中還剩 {left} 分鐘（{kind}）"

    # ── 全域額度 ──
    if AUTO["blocked"]:
        return False, AUTO["blocked"]
    # 第 8 條 r49：讀不到出場成交價（損益未知）不能當成「沒虧」——成交明細查詢壞掉時每筆都是未知，
    # 每日虧損上限就永遠不會觸發，而且正好是系統狀況不好的時候。統計照 r28～r30 記未知（r46 使用者決定），
    # 但**斷路器**把每筆未知當成一次完整停損（-1R）來判斷：只會更早停，不會更晚。
    unk = AUTO.get("unknownToday", 0) or 0
    worst_r = AUTO["closedR"] - unk * 1.0
    if worst_r <= AUTO["dailyLossR"]:
        AUTO["blockedAtR"] = AUTO["closedR"]
        AUTO["blockedAtUsd"] = AUTO.get("closedUsd", 0.0)
        AUTO["blocked"] = (f"當日已虧損 {AUTO['closedR']:.2f}R（{AUTO.get('closedUsd', 0.0):+.0f} U）"
                           + (f"，另有 {unk} 筆損益未知、風控以每筆 -1R 計（合計 {worst_r:.2f}R）" if unk else "")
                           + "，達停損上限，今日停止新開倉；已有部位仍依停損出場")
        save_state()
        return False, AUTO["blocked"]
    if AUTO["opened"] >= AUTO["maxPerDay"]:
        return False, f"今日已開 {AUTO['opened']} 筆，達上限"
    if STATE.get("loadError"):
        return False, "持倉紀錄還沒載入（狀態檔讀不到），暫停開新倉"
    # r13：pending 期間同幣不能再下單，持倉數也要算 pending
    if symbol in (STATE.get("pending") or {}):
        return False, "這檔剛送出進場單、還在等交易所確認，不重複下單"
    held = len(STATE["positions"]) + len([s for s in (STATE.get("pending") or {}) if s not in STATE["positions"]])
    if held >= CFG["maxPositions"]:
        return False, f"同時持倉已達 {CFG['maxPositions']} 筆上限"
    return True, None


@engine_op
def auto_open(symbol_base, side, entry, stop, note="", on_event=None, stop_pct=None):
    """自動開倉。通過風險閘門才會真的送單。"""
    sym = symbol_base.upper() + "USDT"
    ok, why = auto_can_trade(sym)
    if not ok:
        return {"ok": False, "skipped": True, "error": why}

    r = open_position(symbol_base, side, entry, stop, note=note, stop_pct=stop_pct)
    if r.get("ok") or r.get("filled"):
        AUTO["opened"] += 1
        save_state()
        if on_event:
            on_event("open", r)
    return r


def _live_row(symbol, side):
    """帶 symbol 查這一側：回傳 (數量, 均價)；查不到回 (None, None)。

    第 2 條 r17：帶 symbol 查時交易所一定會回這個幣的列（單向 1 列 BOTH、雙向 2 列，數量 0 也會回）。
    回 200 加空清單、或裡面沒有這個幣的列＝查詢異常，要當成「查不到」，不能當成「數量 0」。
    有這個幣的列、但不是這一側（雙向只回另一側之類）也當成查不到。
    """
    st, d = _request("GET", "/fapi/v2/positionRisk", {"symbol": symbol}, signed=True)
    if st != 200 or not isinstance(d, list):
        return None, None
    rows = [p for p in d if p.get("symbol") == symbol]
    if not rows:
        return None, None
    for p in rows:
        if (p.get("positionSide") or "BOTH") == "BOTH" or p.get("positionSide") == side:
            s = _row_side(p)
            amt = abs(float(p.get("positionAmt") or 0))
            if s is None or s == side:
                return (amt if s == side else 0.0), float(p.get("entryPrice") or 0)
            return 0.0, 0.0                   # 單向列，但方向相反：這一側是 0
    return None, None


def _live_qty(symbol, side):
    """交易所上這個幣、這個方向的數量；查不到回 None（查不到不等於沒有，第 2 條）。"""
    return _live_row(symbol, side)[0]


_last_close_fill = {}
FILLS_PAGE = 1000              # userTrades 一頁上限
FILLS_MAX_PAGES = 10          # symbol → 我們自己平倉單的實際成交均價（_market_close 成功時寫入）


def _close_fills(sym, pos, qty_needed=None):
    """成交明細裡，這個部位開倉之後、平倉方向的成交：回傳 (數量, 均價, 最後一筆的成交 id)；查不到回 (None, None, None)。
    平倉成交用方向判斷（多單的平倉是 SELL），不看 realizedPnl——打平出場那筆的 realizedPnl 剛好是 0（r31）。

    第 8 條 r28：出場價與部分出場價只用實際成交價。有基準部位（同側有別人的倉）時，
    成交明細分不出哪幾筆是自己的，回 (None, None)——記未知，不猜。"""
    if float(pos.get("base") or 0) > 0:
        return None, None, None
    # 界線（r31）：採用過成交之後用「最後採用那筆的成交 id＋1」（fromId）；同一毫秒內可能有好幾筆成交，用時間會漏掉或重算。
    # 還沒採用過任何成交時，從開倉時間起算（開倉那筆是開倉方向，下面會被方向篩掉）。
    from_id = pos.get("fillsFromId")
    if isinstance(from_id, int):
        q_params = {"symbol": sym, "fromId": from_id, "limit": 100}
    else:
        since = pos.get("fillsSince") if isinstance(pos.get("fillsSince"), (int, float)) else pos.get("opened")
        if not isinstance(since, (int, float)) or since <= 0:
            # 0 或缺值不能當界線：從頭查起，這個幣歷史上所有的平倉成交都會被算進來——那是算錯，不是未知（r34）
            return None, None, None
        q_params = {"symbol": sym, "startTime": int(since), "limit": 100}
    # 分頁拿完（r37）：只拿一頁時，界線之後的平倉成交掉了幾筆，程式照樣用剩下的算出「確定的」錯數字。
    # 第一頁之後一律改用 fromId（上一頁最後一筆 id＋1）往後查；超過頁數上限記未知。
    q_params = dict(q_params, limit=FILLS_PAGE)
    d = []
    for _page in range(FILLS_MAX_PAGES):
        st, page = _request("GET", "/fapi/v1/userTrades", q_params, signed=True)
        if st != 200 or not isinstance(page, list):
            return None, None, None
        d += page
        if len(page) < FILLS_PAGE:
            break
        ids_ = [x.get("id") for x in page if isinstance(x.get("id"), int)]
        if not ids_:
            return None, None, None
        q_params = {"symbol": sym, "fromId": max(ids_) + 1, "limit": FILLS_PAGE}
    else:
        return None, None, None                      # 頁數上限內拿不完：記未知，不拿部分當全部
    close_side = "SELL" if pos.get("side") == "LONG" else "BUY"
    rows = [x for x in d if x.get("side") == close_side
            and (x.get("positionSide") or "BOTH") in ("BOTH", pos.get("side"))]
    q = sum(float(x.get("qty") or 0) for x in rows)
    if q <= 0:
        return None, None, None
    if qty_needed is not None and q < float(qty_needed) * 0.99:
        return None, None, None                  # 湊不滿：有成交沒查到，不拿部分當全部
    px = sum(float(x.get("qty") or 0) * float(x.get("price") or 0) for x in rows) / q
    ids = [x.get("id") for x in rows if isinstance(x.get("id"), int)]
    last_id = max(ids) if ids else None
    return q, px, last_id


def _mark_boundary(sym, pos, order_id=None):
    """記下成交明細的起始界線（第 8 條 r34、r35）：開倉成交後、認領當下就記，跟著部位存進狀態檔。

    - 開倉：用開倉那張單的單號查它的成交，最後一筆 id＋1。
    - 認領（沒有單號）：查最近的成交，最後一筆 id＋1；最近沒有成交就記下認領當下的時間。
    以前是平倉時才用「開倉時間」查：交易所時鐘比本機慢時平倉成交被篩掉；時間戳是 0 時從頭查起，
    這個幣歷史上所有的平倉成交都被算成這筆的出場。查不到就不記——之後會記未知，不會算錯。
    回傳記下的方式：id／time／none。"""
    if order_id is not None:
        st, d = _request("GET", "/fapi/v1/userTrades", {"symbol": sym, "orderId": order_id}, signed=True)
    else:
        st, d = _request("GET", "/fapi/v1/userTrades",
                         {"symbol": sym, "startTime": int(time.time() * 1000) - 600000, "limit": 1000}, signed=True)
    if st != 200 or not isinstance(d, list):
        return "none"
    ids = [x.get("id") for x in d if isinstance(x.get("id"), int)]
    if ids:
        pos["fillsFromId"] = max(ids) + 1
        return "id"
    if order_id is None:
        pos["fillsSince"] = int(time.time() * 1000)
        return "time"
    return "none"


def _exit_price(sym, pos):
    """出場價：先用我們自己平倉單的成交均價，否則查成交明細；都沒有就回 None（損益記未知）。
    以前是「標記價，查不到再用進場價」——用進場價時損益剛好是 0，是把未知包裝成已知（第 8 條 r28）。"""
    avg = _last_close_fill.pop(sym, None)
    partial = bool(pos.get("closePartial")) or _close_partial.pop(sym, False)
    if isinstance(avg, (int, float)) and avg > 0 and not partial:
        return avg
    _q, px, _t = _close_fills(sym, pos, qty_needed=pos.get("qty"))
    return px


_close_partial = {}           # symbol → 平倉單只成交一部分（出場價不能只用最後一張單的均價，第 15 條）


def _market_close(symbol, side, qty, base=0.0, pos=None):
    """市價平倉，回傳 (結果, 說明)。結果：True 已確認平掉／False 沒平掉／"gone" 送單前那一側就已經不在。

    - 送單前先確認自己那一側還在（第 7 條 r13）：已經不在（例如剛被停損）就不送單、交給對帳；
      單向共用帳號裡同側還有別人的部位時，送出去會把別人的平掉。
    - 只平自己的數量：交易所這一側 − 送單前的基準（第 3 條 r14）。
    - 送單結果一定要看（第 8 條）：回應非成功時再查一次；查不到當成沒平掉，確認沒了才算平掉。
    - **HTTP 200 不等於平掉**（第 15 條）：True 只在「交易所說 FILLED 且成交量＝送出量」或「再查部位確認沒了」時回傳。
      呼叫端拿到 True 就會撤停損、結帳——把「交易所收下了」當成 True，部位還在、停損卻撤了。
    """
    live = _live_qty(symbol, side)
    if live is None:
        return False, "查不到部位，為安全起見不送平倉單"
    own = live - (base or 0)
    if own <= 1e-12:
        return "gone", "這一側已經沒有自己的部位"
    q = min(qty, own)
    close_side = "SELL" if side == "LONG" else "BUY"
    st, d = _request("POST", "/fapi/v1/order", {
        "symbol": symbol, "side": close_side, "type": "MARKET",
        "quantity": q, "newOrderRespType": "RESULT", **_reduce(side),
    }, signed=True)
    if st == 200:
        oc = _settle_market(symbol, d)
        ex_q = oc["executed"] or 0.0
        step = (_filters.get(symbol) or {}).get("step") or 0
        tol = max(step * 0.5, 1e-9)
        partial_before = bool((pos or {}).get("closePartial")) or _close_partial.get(symbol)
        if oc["status"] == "FILLED" and ex_q >= q - tol:
            if oc["avg"] <= 0:
                _fq, favg = _order_fill_avg(symbol, oc["orderId"])   # r42：均價缺漏時從成交明細算
                oc["avg"] = favg or 0.0
            if oc["avg"] > 0 and not partial_before:
                _last_close_fill[symbol] = oc["avg"]      # 這張單的實際成交均價（r28：出場價只用實際成交價）
            return True, (None if q == qty else f"帳上 {qty:g}、交易所自己的 {own:g}，以實際數量平倉")
        # 交易所沒說「全部成交」：不能當成平掉。再查部位——確實沒了才算（例如同時被停損觸發）
        desc = f"平倉單回 {oc['status'] or '沒有狀態'}、成交 {ex_q:g}／{q:g}" + (f"（{oc['note']}）" if oc["note"] else "")
        if ex_q > 0:
            _close_partial[symbol] = True
            if pos is not None:
                pos["closePartial"] = True
        after = _live_qty(symbol, side)
        if after is None:
            return False, f"{desc}；再查部位也失敗，當成沒平掉"
        if after - (base or 0) <= tol:
            return True, f"{desc}，但交易所上已無自己的部位"
        return False, f"{desc}，交易所上仍有自己的 {after - (base or 0):g}，當成沒平掉"
    why = str((d or {}).get("msg") or (d or {}).get("error") if isinstance(d, dict) else d)[:100]
    after = _live_qty(symbol, side)
    if after is None:
        return False, f"{why}；再查部位也失敗，當成沒平掉"
    if after - (base or 0) <= 1e-12:
        return True, f"平倉單回應 {why}，但交易所上已無自己的部位"
    return False, why


def _own_live(pos):
    """逐幣確認自己在交易所上的數量（扣基準）。回傳 (數量, 狀態)：ok / unknown（查不到）/ gone（沒了）。"""
    live = _live_qty(pos["symbol"], pos["side"])
    if live is None:
        return None, "unknown"
    own = live - float(pos.get("base") or 0)
    return (own, "ok") if own > 1e-12 else (0.0, "gone")


def place_stop(pos, price, own=None):
    """送出停損單的唯一入口（第 2 條 r19、r20）。第一次掛、認領、守衛補掛、移損掛新、移損失敗掛回，全部走這裡。

    - 先逐幣確認自己的部位（扣基準）：查不到 → "unknown"（這輪不動、不算掛單失敗）；
      沒了 → "gone"（不掛，交給對帳）。部位其實已被平掉、只是還沒偵測到時，掛出去的就是孤兒 reduce-only 單。
      剛確認過（例如成交確認、認領時手上就有數量）可以直接傳 own。
    - 有基準（同側有別人的部位）時用自己的數量＋reduce-only，數量取「交易所−基準」與帳上的小者；
      closePosition 是「平掉這一側全部」，會把別人的部位一起平掉。沒有基準時維持 closePosition（出一半後自動涵蓋剩餘）。
    回傳 (狀態, status, data, 端點)，狀態：placed / failed / unknown / gone。
    """
    if own is None:
        own, s = _own_live(pos)
        if s != "ok":
            return s, None, None, None
    sym, side = pos["symbol"], pos["side"]
    params = {"symbol": sym, "side": "SELL" if side == "LONG" else "BUY", "type": "STOP_MARKET",
              "stopPrice": price, "workingType": "MARK_PRICE"}
    if float(pos.get("base") or 0) > 0:
        step = (_filters.get(sym) or {}).get("step") or 0
        q = min(float(pos["qty"]), float(own))
        params.update(quantity=round_step(q, step) if step else q, **_reduce(side))
    else:
        params.update(_close_all(side))
    st, r, ep = place_conditional(params)
    return ("placed" if st == 200 else "failed"), st, r, ep


def _set_pending_close(pos, reason, why):
    """平倉沒平掉：記待平倉旗標、告警第 1 次（第 8 條 r13）。停損與停利不動，守衛照常運作。"""
    pc = pos.get("pendingClose")
    if not pc:
        pc = pos["pendingClose"] = {"reason": reason, "attempts": 0, "since": int(time.time() * 1000)}
    pc["attempts"] += 1
    pc["lastErr"] = why
    save_state()
    n = pc["attempts"]
    if alert_due(n):
        _notify(f"⚠ 平倉失敗，部位仍在（第 {n} 次）",
                f"{pos['symbol']} {pc['reason']}\n目前停損 {pos.get('stop', 0):g}（未撤，繼續保護）\n"
                f"第 {n} 次平倉失敗：{why}\n每 {CFG.get('positionPoll', 20):g} 秒重試一次，平掉時會再通知。")


@engine_op
def retry_pending_closes():
    """每輪重試待平倉的部位。只有這條路會為待平倉的部位送平倉單（第 8 條 r14）。"""
    events = []
    for sym, pos in list(STATE["positions"].items()):
        try:
            pc = pos.get("pendingClose")
            if not pc:
                continue
            closed, why = _market_close(sym, pos["side"], pos["qty"], pos.get("base") or 0, pos=pos)
            if closed == "gone":
                events.append({"symbol": sym, "action": "gone"})     # 交給對帳記帳
                continue
            if closed:
                px = _exit_price(sym, pos)
                n = pc["attempts"]
                record_close(pos, px, pc["reason"])
                _notify("已平倉", f"{sym} {pc['reason']}（已補上：先前平倉失敗 {n} 次）。")
                events.append({"symbol": sym, "action": "closed", "fails": n})
                continue
            _set_pending_close(pos, pc["reason"], why)
            events.append({"symbol": sym, "action": "retry", "attempt": pc["attempts"]})
        except Exception as e:
            _pos_step_error("待平倉重試", sym, e)
        else:
            _pos_step_ok("待平倉重試", sym)
    return events


@engine_op
def close_position(symbol, reason="手動平倉"):
    """手動平倉的外層保護（第 8 條 r23）：以「結帳」為界。

    結帳之前出錯（送單、確認查詢丟例外）→ 部位留在帳上、記待平倉、每輪重試、推播；
    結帳之後出錯 → 已經不可逆，只推播，回報已平倉。以前例外直接往外傳，網頁只看到連線中斷，
    使用者要平倉的意圖也沒留下。
    """
    n0 = len(STATE["trades"])
    try:
        return _close_position_impl(symbol, reason)
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:120]}"
        booked = len(STATE["trades"]) > n0 and (STATE["trades"][-1] or {}).get("symbol") == symbol
        if booked:
            _pos_step_error("手動平倉收尾", symbol, e)
            return {"ok": True, "symbol": symbol, "warning": f"已平倉，收尾時出錯：{err}"}
        pos = STATE["positions"].get(symbol)
        if pos is not None:
            _set_pending_close(pos, reason, err)
        _pos_step_error("手動平倉", symbol, e)
        return {"ok": False, "symbol": symbol,
                "error": f"平倉途中出錯：{err}" + ("（已記為待平倉，每輪重試）" if pos is not None else "")}


def _close_position_impl(symbol, reason="手動平倉"):
    pos = STATE["positions"].get(symbol)
    if not pos:
        return {"ok": False, "error": "沒有這個部位"}
    if not CFG["dryRun"]:
        if pos.get("pendingClose"):
            return {"ok": False, "symbol": symbol, "error": "這筆已在待平倉、每輪重試中"}
        closed, why = _market_close(symbol, pos["side"], pos["qty"], pos.get("base") or 0, pos=pos)
        if closed == "gone":
            # 第 7 條 r13：那一側已經不在（多半剛被停損）→ 不送單、不動帳，交給對帳
            return {"ok": False, "symbol": symbol, "error": f"{why}，不送平倉單，交給對帳處理"}
        if not closed:
            # 沒平掉就不記帳、不撤停損；記待平倉，每輪重試（第 8 條 r13）
            _set_pending_close(pos, reason, why)
            return {"ok": False, "symbol": symbol, "error": f"平倉失敗：{why}（已記為待平倉，每輪重試）"}
    px = _exit_price(symbol, pos)
    record_close(pos, px, reason)
    return {"ok": True, "symbol": symbol, "exit": px}


@engine_op
def cancel_orphan(symbol, algo_id):
    """撤掉一張孤兒條件單（自檢列出的）。

    幣安子帳戶的網頁訂單管理看不到 Algo 條件單，手動撤不了，所以從這裡撤。
    安全檢查：這個幣現在有部位、或這張單屬於帳上某個部位，就拒絕——那不是孤兒。
    """
    if not symbol or not algo_id:
        return {"ok": False, "error": "缺少 symbol 或 algoId"}
    if STATE.get("loadError"):
        # r40：帳沒載入時分不出哪些單是自己部位的，不能判定孤兒
        return {"ok": False, "error": "持倉紀錄還沒載入，無法判斷是不是孤兒單，不撤"}
    for p in STATE["positions"].values():
        for o in p.get("orders") or []:
            if str(o.get("id")) == str(algo_id):
                return {"ok": False, "error": f"這張單屬於帳上的 {p['symbol']} 部位，不是孤兒單"}
    st, d = _request("GET", "/fapi/v2/positionRisk", {"symbol": symbol}, signed=True)
    rows = [r for r in d if r.get("symbol") == symbol] if st == 200 and isinstance(d, list) else []
    if not rows:
        # 第 2 條 r17：沒有這個幣的列（含空清單）＝查不到，不是「沒有部位」
        return {"ok": False, "error": "查不到這個幣的部位，為安全起見不撤（查不到不等於不存在）"}
    if any(abs(float(r.get("positionAmt") or 0)) > 0 for r in rows):
        return {"ok": False, "error": f"{symbol} 目前有部位，這張單可能正在保護它，不撤"}
    st, d = _request("DELETE", "/fapi/v1/algoOrder", {"symbol": symbol, "algoId": algo_id}, signed=True)
    if st == 200:
        return {"ok": True, "symbol": symbol, "algoId": algo_id}
    return {"ok": False, "error": str((d or {}).get("msg") if isinstance(d, dict) else d)[:120]}


def _leftover_failed(lf):
    """殘留單撤不掉：計數＋依節奏告警（第 13 條、第 8 條 r10）。
    平倉當下的第 1 次失敗也走這裡，不再依賴平倉通知順便帶一句——
    手動平倉、守衛強平、移損穿價出場都不經過那則平倉通知。"""
    n = lf["attempts"]
    if alert_due(n):
        _notify(f"⚠ 殘留單撤不掉（第 {n} 次）",
                f"{lf['symbol']} {lf['type']} #{lf['id']}" + (f" @ {lf['px']:g}" if lf.get("px") else "")
                + f"\n部位已平倉，這張單沒有對應部位。\n第 {n} 次撤單失敗：{lf.get('lastErr')}\n"
                  f"下次同幣進場時它可能讓新停損被拒。每 {CFG.get('positionPoll', 20):g} 秒重試一次，撤掉時會再通知。")


@engine_op
def retry_leftovers():
    """每輪重試待撤清單（第 13 條）。回傳事件，節奏同第 8 條。"""
    events = []
    if CFG["dryRun"] or not (CFG["key"] and CFG["secret"]):
        return events
    keep = []
    for lf in STATE.get("leftovers") or []:
        try:
            if lf.get("via") == "algo":
                st, d = _request("DELETE", "/fapi/v1/algoOrder", {"symbol": lf["symbol"], "algoId": lf["id"]}, signed=True)
            else:
                st, d = _request("DELETE", "/fapi/v1/order", {"symbol": lf["symbol"], "orderId": lf["id"]}, signed=True)
            code = d.get("code") if isinstance(d, dict) else None
            msg = str((d or {}).get("msg") if isinstance(d, dict) else d)
            if st == 200 or code in (-2011, -2013) or "Unknown" in msg or "not exist" in msg.lower():
                events.append({"action": "recovered", **lf})
                # 第 1 次失敗一定告警過（平倉當下），所以撤掉時一律發恢復
                _notify("殘留單已撤掉", f"{lf['symbol']} {lf['type']} #{lf['id']}\n（已補上：先前撤單失敗 {lf['attempts']} 次）")
                continue
            lf["attempts"] += 1
            lf["lastErr"] = msg[:80]
            keep.append(lf)
            events.append({"action": "alert", "alert": alert_due(lf["attempts"]), **lf})
            _leftover_failed(lf)
        except Exception as e:
            _pos_step_error("殘留單重試", f"{lf.get('symbol')}#{lf.get('id')}", e)
            keep.append(lf)                  # 出錯的這張留在清單，下輪再試
        else:
            _pos_step_ok("殘留單重試", f"{lf.get('symbol')}#{lf.get('id')}")
    if (STATE.get("leftovers") or []) != keep:
        STATE["leftovers"] = keep
        save_state()
    return events


PENDING_EXPIRE_SEC = 180


@engine_op
def adopt_pending(live):
    # live：{(symbol, side): positionRisk 列}，只認領方向相符的部位
    """把「送了單但沒記到帳」的部位認領回來（BINANCE_LESSONS 第 3 條）。

    30 秒後交易所上有這個幣、帳上卻沒有 → 用 pending 裡的參數建立紀錄，並立刻補掛停損。
    2 分鐘後交易所上也沒有 → 那張單根本沒成交，丟掉 pending。
    """
    now = int(time.time() * 1000)
    for sym, pd in list((STATE.get("pending") or {}).items()):
        ts = pd.get("ts")
        # 時間戳缺值時當成已經很久（逾時），走逐幣確認後認領或判定未成交；
        # 以前當成 0 歲，永遠不會到期——這個幣永遠不能再下單、還佔一個持倉名額（r31 pump-dump-hunter 的同類問題）
        age = now - ts if isinstance(ts, (int, float)) else (PENDING_EXPIRE_SEC + 1) * 1000
        if age < 30000 or sym in STATE["positions"]:
            if sym in STATE["positions"]:
                STATE["pending"].pop(sym, None)
            continue
        lp = live.get((sym, pd["side"]))
        base = float(pd.get("base") or 0)
        own = (abs(float(lp.get("positionAmt") or 0)) - base) if lp else 0.0
        if own <= 1e-12:
            # r13：這一輪沒看到不能立刻丟——交易所可能還沒反映。180 秒過了才判定未成交並通知。
            if age <= PENDING_EXPIRE_SEC * 1000:
                continue
            # 第 2 條 r16：判定「交易所上沒有」要帶 symbol 逐幣查，不能從全量表下結論
            q, e = _live_row(sym, pd["side"])
            if q is None:
                continue                          # 查不到：這輪不判定
            if q - base <= 1e-12:
                STATE["pending"].pop(sym, None)
                save_state()
                _notify("送出的進場單判定未成交",
                        f"{sym} 送單後 {PENDING_EXPIRE_SEC} 秒，逐幣確認交易所上仍沒有這一側的新部位，判定未成交，不再等候。")
                continue
            lp = {"positionAmt": str(q), "entryPrice": str(e)}
        try:
            qty = abs(float(lp.get("positionAmt") or 0))
            entry = float(lp.get("entryPrice") or 0)
        except Exception:
            continue
        qty = qty - base                         # r14：扣掉送單前就有的同側部位
        if qty <= 0 or entry <= 0:
            continue
        merged_entry, estimated = entry, False
        if base > 0 and pd.get("baseEntry"):
            # 第 3 條 r16：交易所均價是合併過的；用基準均價反推這張單的成交價（估計值）
            est = (merged_entry * (qty + base) - float(pd["baseEntry"]) * base) / qty
            if est > 0:
                entry, estimated = est, True
        side = pd["side"]
        sp = pd.get("stopPct")
        stop = (entry * (1 - sp / 100) if side == "LONG" else entry * (1 + sp / 100)) if sp else pd.get("stop")
        info = _filters.get(sym) or {}
        stop_px = round_step(stop, info.get("tick") or 0.01)
        close_side = "SELL" if side == "LONG" else "BUY"
        pos = {"version": strategy_label(pd.get("params")), "leverage": pd.get("leverage"),
               "params": pd.get("params"), "minScore": pd.get("minScore"),
               "symbol": sym, "side": side, "qty": qty, "entry": entry, "stop": stop_px,
               "exits": plan_exits(entry, stop_px, side), "orders": [],
               "opened": pd.get("ts"), "note": (pd.get("note") or "") + "（對帳認領）",
               "base": base, "qty0": qty,
               "warnings": ["送單後未記帳，由對帳認領；只補掛停損，停利與移動停利未掛"]
                           + ([f"送單前這一側已有 {base:g}，進場價 {entry:g} 是用基準均價反推的估計值"
                               f"（交易所合併均價 {merged_entry:g}）"] if estimated else
                              [f"送單前這一側已有 {base:g}，交易所均價 {entry:g} 是合併過的，不是這張單的成交價"]
                              if base > 0 else [])}
        _s, st, r, ep = place_stop(pos, stop_px, own=qty)      # 認領時剛從交易所看到（已扣基準）
        if st == 200:
            pos["orders"].append({"type": "STOP_MARKET", "id": r.get("algoId") or r.get("orderId"),
                                  "px": stop_px, "via": ep})
        else:
            pos["warnings"].append(f"補掛停損失敗：{(r or {}).get('msg') or r}（守衛會再試）")
        STATE["positions"][sym] = pos
        STATE["pending"].pop(sym, None)
        try:
            _mark_boundary(sym, pos)                                    # 認領當下記界線（r34）
        except Exception as e:
            _pos_step_error("記成交界線", sym, e)
        save_state()
        ADOPTED.append({"symbol": sym, "qty": qty, "entry": entry, "stop": stop_px, "stopOk": st == 200})


ADOPTED = []          # 本輪認領的部位，給 main.py 推播用


@engine_op
def record_close(pos, exit_px, reason):
    """記帳並撤掉這個部位剩下的條件單（BINANCE_LESSONS 第 13 條）。

    交易所端任一張條件單觸發平倉後，其餘的會留下來變孤兒：
    停損觸發 → 停利與移動停利還在；移動停利出場 → closePosition 停損還在。
    下次同一個幣再進場時，舊的 closePosition 停損會讓新停損被拒（第 8 條），
    進而觸發「沒有停損就不留倉」而立刻平倉；共用帳號時還可能動到別的專案的倉。

    以「結帳」為界（第 8 條 r22、r23）：
      1. 先算好平倉紀錄——只用 .get() 取值，缺欄位時損益記為未知，不能在這裡丟例外；
      2. 結帳：寫紀錄、從帳上移除、存檔。這一步之後部位就不會再被結一次；
      3. 結帳之後的每一步（撤剩下的條件單、收尾通知、每日統計）都不可逆或只是通知，各自 try，出錯只推播。
    以前是「撤條件單 → 發收尾通知 → 算損益 → 結帳」：通知或損益一出錯，條件單已撤、部位卻還在帳上，
    下一輪對帳又結一次、又出錯，這筆永遠結不了帳。
    """
    sym_ = pos.get("symbol")
    now_ms = int(time.time() * 1000)
    # ── 1. 平倉紀錄（純計算，不可能丟例外）──
    try:
        sgn = 1 if pos.get("side") == "LONG" else -1
        parts = pos.get("partials") or []
        qty0 = pos.get("qty0") or pos.get("qty") or 0
        if exit_px is None or pos.get("entryUnverified") or any(p.get("pnl") is None for p in parts):
            # 出場價或某一段部分出場的成交價查不到：整筆損益未知（第 8 條 r28）。
            # 這是正常的未知，不是程式出錯，不告警；以前未知那段會被 `or 0` 當成 0 加總。
            pnl_r, rmult = None, None
        else:
            pnl = sum(float(p["pnl"]) for p in parts) + \
                (float(exit_px) - float(pos.get("entry"))) * sgn * float(pos.get("qty") or 0)
            r_unit = float((pos.get("exits") or {}).get("R") or 0) * float(qty0)
            pnl_r, rmult = round(pnl, 4), (round(pnl / r_unit, 2) if r_unit else None)
    except Exception as e:
        parts, qty0, pnl_r, rmult = pos.get("partials") or [], pos.get("qty"), None, None
        _pos_step_error("結帳損益計算", sym_, e)
    trade = {
        "id": f"{sym_}-{now_ms}",
        "excluded": False,
        "version": pos.get("version") or "v1",
        "symbol": sym_, "side": pos.get("side"), "qty": qty0,
        "partials": parts,
        "entry": pos.get("entry"), "exit": exit_px, "pnl": pnl_r,
        "rMultiple": rmult,
        "orders": pos.get("orders") or [],           # 保留訂單 id，事後查孤兒單用
        "leftover": None,
        "opened": pos.get("opened"), "closed": now_ms,
        "reason": reason, "note": pos.get("note", ""),
    }

    # ── 2. 結帳（界線）──
    STATE["trades"].append(trade)
    STATE["positions"].pop(sym_, None)
    save_state()

    # ── 3. 界線之後：各自 try，出錯只推播 ──
    def after(step, fn):
        try:
            fn()
        except Exception as e:
            _pos_step_error(step, sym_, e)

    def _cancel_rest():
        if CFG["dryRun"] or not pos.get("orders"):
            return
        try:
            done, gone, failed = cancel_position_orders(sym_, pos)
            trade["leftover"] = {"cancelled": done, "alreadyGone": gone,
                                 "failed": [f_.get("text") for f_ in failed]}
        except Exception as e:
            trade["leftover"] = {"failed": [str(e)[:80]]}
            failed = [{"order": o, "why": str(e)[:80]} for o in pos.get("orders") or [] if o.get("id")]
        # 撤不掉的放進待撤清單：部位已經平了，重試狀態沒地方放在部位上
        for f_ in failed if isinstance(failed, list) else []:
            if isinstance(f_, dict):
                o = f_.get("order") or {}
                lf = {"symbol": sym_, "id": o.get("id"), "via": o.get("via"),
                      "type": _order_type(o), "px": o.get("px"), "attempts": 1,
                      "lastErr": f_.get("why"), "since": now_ms}
                STATE.setdefault("leftovers", []).append(lf)
                after("殘留單告警", lambda lf=lf: _leftover_failed(lf))

    def _close_out_states():
        # r10：失敗狀態隨部位平倉消失的地方——先前告警過就發收尾，計數一併清掉，
        # 否則同一個幣下次進場時會接著舊的次數數下去
        n_rf = _replace_fails.pop(sym_, 0)
        _missing_streak.pop(sym_, None)
        # 第 8 條 r19：以幣名為鍵的出錯次數也要跟著部位結束清掉
        errs = {"停損檢查": _guard_errors.pop(sym_, 0)}
        for k in [k for k in _pos_errors if k[1] == sym_]:
            errs[k[0]] = _pos_errors.pop(k)
        errs = {k: v for k, v in errs.items() if v}
        if pos.get("wantStop") is not None and pos.get("beFails"):
            want = pos.get("wantStop")
            want_s = f"{want:g}" if isinstance(want, (int, float)) else str(want)
            _notify("移損失敗狀態結束：部位已平倉",
                    f"{sym_} 先前移損到成本失敗 {pos.get('beFails')} 次（想要 {want_s}），部位已平倉（{reason}），不再重試。")
        if errs:
            _notify("出錯狀態結束：部位已平倉",
                    f"{sym_} 部位已平倉（{reason}）。先前出錯：" + "、".join(f"{k} {v} 次" for k, v in errs.items()) + "，不再重試。")
        if n_rf:
            _notify("補掛失敗狀態結束：部位已平倉",
                    f"{sym_} 先前補掛停損失敗 {n_rf} 次，部位已平倉（{reason}），不再重試。")

    def _stats():
        conflict_on_close(sym_, rmult)
        AUTO["lastClose"][sym_] = time.time()
        # 損益未知記 None：冷卻不寫成「虧損」；每日虧損不加進去，但筆數放進風控狀態（第 8 條 r29）
        AUTO.setdefault("lastCloseWin", {})[sym_] = (pnl_r > 0) if pnl_r is not None else None
        if pnl_r is None:
            auto_roll_day()
            AUTO["unknownToday"] = AUTO.get("unknownToday", 0) + 1
        if rmult is not None:
            auto_roll_day()
            AUTO["closedR"] += rmult
            AUTO["closedUsd"] = AUTO.get("closedUsd", 0.0) + (pnl_r or 0.0)

    after("撤剩餘條件單", _cancel_rest)
    after("收尾通知", _close_out_states)
    after("每日統計", _stats)
    after("存檔", save_state)



def _row_side(p):
    """positionRisk 的一列屬於哪個方向（BINANCE_LESSONS 第 7 條）。

    雙向模式看 positionSide；單向模式（BOTH）看 positionAmt 的正負。
    不能只看幣名：我的多單已被停損、共用帳號裡別的專案剛好有同幣空單時，
    只看幣名會誤以為自己還在場。回傳 LONG / SHORT，空倉回 None。
    """
    try:
        amt = float(p.get("positionAmt") or 0)
    except (TypeError, ValueError):
        return None
    if amt == 0:
        return None
    ps = p.get("positionSide") or "BOTH"
    if ps in ("LONG", "SHORT"):
        return ps
    return "LONG" if amt > 0 else "SHORT"


def _rows_by_side(rows):
    """{(symbol, side): row}"""
    out = {}
    for p in rows or []:
        s = _row_side(p)
        if s:
            out[(p.get("symbol"), s)] = p
    return out


@engine_op
def sync_positions():
    """跟幣安對帳：本地記著但交易所已無部位的，代表被停損或停利成交了。"""
    if CFG["dryRun"]:
        return {"closed": []}
    if STATE.get("loadError"):
        # r40：帳還沒載入，不動帳，並講明（以前手動對帳回「無異動」，看起來像對過了）
        return {"error": "持倉紀錄還沒載入（狀態檔讀不到），無法對帳"}
    st, d = _request("GET", "/fapi/v2/positionRisk", signed=True)
    if st != 200 or not isinstance(d, list):
        return {"error": "對帳失敗"}
    by_side = _rows_by_side(d)
    # 先認領，再算「還在場」：以前反過來，剛認領的部位不在清單裡，同一輪就被判成平倉、停損也被撤掉
    adopt_pending(by_side)
    live = {}
    for sym, pos in STATE["positions"].items():
        row = by_side.get((sym, pos["side"]))
        base = float(pos.get("base") or 0)
        own = (abs(float(row.get("positionAmt") or 0)) - base) if row else 0.0
        if own <= 1e-12:
            # 全量表裡沒有：逐幣再查一次再下結論（全量表可能偶發回空清單；查不到不等於沒有，第 2 條）
            q = _live_qty(sym, pos["side"])
            if q is None:
                live[sym] = {"positionAmt": str(pos["qty"] + base), "_unverified": True}
                continue
            own = q - base
            if own <= 1e-12:
                continue
            row = {"positionAmt": str(q)}
        live[sym] = row

    # 部分出場：交易所數量比帳上少 → 停利單（2R 出一半）成交了。
    # 已成交的動作要先通知（第 8 條 r10），也要馬上更新帳上數量：
    # 否則平倉時用全部數量 × 最後出場價算損益，而且雙向模式下手動平倉會因超量被拒。
    for sym, row in live.items():
        try:
            pos = STATE["positions"][sym]
            if row.get("_unverified"):
                continue
            try:
                q_live = abs(float(row.get("positionAmt") or 0)) - float(pos.get("base") or 0)
            except (TypeError, ValueError):
                continue
            step = (_filters.get(sym) or {}).get("step") or 0
            if q_live <= 0 or pos["qty"] - q_live <= max(step * 0.5, 1e-12):
                continue
            reduced = pos["qty"] - q_live
            tp = next((o for o in pos.get("orders") or [] if _order_type(o) == "TAKE_PROFIT_MARKET"), None)
            if tp and tp.get("qty") and abs(float(tp["qty"]) - reduced) <= max(step, 1e-12):
                how = "第一目標停利"
                pos["orders"] = [o for o in pos["orders"] if o is not tp]       # 已觸發，不再追蹤
            else:
                how = "部分減碼"
            # 第 8 條 r28：價格只用成交明細的實際成交價；以前用停利觸發價或標記價估，是把未知包裝成已知
            _fq, px, last_id = _close_fills(sym, pos, qty_needed=reduced)
            sgn = 1 if pos["side"] == "LONG" else -1
            part_pnl = (px - pos["entry"]) * sgn * reduced if isinstance(px, (int, float)) else None
            pos.setdefault("qty0", pos["qty"])
            now_ms = int(time.time() * 1000)
            pos.setdefault("partials", []).append({"qty": reduced, "px": px,
                                                   "pnl": round(part_pnl, 4) if part_pnl is not None else None,
                                                   "ts": now_ms, "how": how})
            pos["qty"] = q_live
            # 之後的出場價只看「已採用的最後一筆成交 id」之後的成交（r31：用 id，不用時間——同一毫秒可能有好幾筆）
            if isinstance(last_id, int):
                pos["fillsFromId"] = last_id + 1
            save_state()
            r_unit = (pos.get("exits") or {}).get("R") or 0
            if part_pnl is None:
                detail = "（成交價查不到，這一段損益記為未知）"
            else:
                detail = (f"（{part_pnl:+.2f} U" + (f"，{part_pnl / (r_unit * pos['qty0']):+.2f}R" if r_unit else "") + "）")
            _notify(f"部分出場：{how}",
                    f"{sym} {'做多' if sgn > 0 else '做空'}　出場 {reduced:g}"
                    + (f" @ {px:g}" if isinstance(px, (int, float)) else "") + detail + "\n"
                    f"剩餘 {q_live:g}，移動停利與停損繼續保護。")
        except Exception as e:
            _pos_step_error("部分出場偵測", sym, e)
        else:
            _pos_step_ok("部分出場偵測", sym)

    closed = []
    for sym in list(STATE["positions"].keys()):
        try:
            if sym not in live:
                pos = STATE["positions"][sym]
                px = _exit_price(sym, pos)
                record_close(pos, px, "交易所出場（停損或停利觸發）")
                closed.append(sym)
        except Exception as e:
            _pos_step_error("平倉記帳", sym, e)
        else:
            _pos_step_ok("平倉記帳", sym)
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
            side = _row_side(p)
            if not side:
                continue
            # 只認帳上記錄的那一側：別的專案同幣反向的部位不是我們的（第 7 條）
            mine = STATE["positions"].get(p["symbol"])
            if mine and side != mine["side"]:
                continue
            row = {
                "mark": float(p.get("markPrice") or 0),
                "pnl": float(p.get("unRealizedProfit") or 0),
                "entry": float(p.get("entryPrice") or 0),
                "qty": abs(amt),
                "liq": float(p.get("liquidationPrice") or 0) or None,
            }
            base = float((mine or {}).get("base") or 0)
            if mine and base > 0:
                # 第 3 條 r16：基準跟著部位走完生命週期——同側有別人的部位時，交易所的損益與數量是合併的
                own = max(0.0, abs(amt) - base)
                sgn = 1 if mine["side"] == "LONG" else -1
                row.update(qty=own, entry=mine["entry"], pnl=(row["mark"] - mine["entry"]) * sgn * own)
            out[p["symbol"]] = row
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


@engine_op
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
        # 出場位階與實盤共用 plan_exits（BINANCE_LESSONS 第 11 條），不另外寫一套
        stop = entry - sgn * entry * m["stopPct"] / 100.0
        ex = plan_exits(entry, stop, m["side"])
        r_unit = ex["R"]
        tp2 = ex["tp1"]
        tp_r = CFG["tp1R"]
        res, r, hit_t = "none", None, None
        for t_, p in pts:
            if (p - stop) * sgn <= 0:
                res, r, hit_t = "stop", -1.0, t_
                break
            if (p - tp2) * sgn >= 0:
                res, r, hit_t = "tp2", float(tp_r), t_
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


@engine_op
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



def open_algo_orders(symbol, include_legacy=False):
    """查這個交易對還掛著哪些條件單。

    回傳 (orders, ok)。ok=False 代表查詢本身失敗，
    呼叫端絕對不能把「查不到」當成「不存在」——這正是先前誤平倉的根源。

    第 1 條 r13：退回舊端點期間，兩個端點都可能有自己的單，要兩邊都查。
    Algo 查詢失敗只有在「目前確實走舊端點」時才能忽略。
    """
    out, algo_ok = [], False
    st, d = _request("GET", "/fapi/v1/openAlgoOrders", {"symbol": symbol}, signed=True)
    if st == 404 and not algo_fallback_active():
        # 第 1 條 r16：只有掛單時的 404 才進入退回的話，服務重啟後旗標歸零，
        # 守衛查 Algo 一直 404、每輪「查詢失敗、跳過」。查詢的 404 也要進入退回期。
        _algo_supported[0] = False
        _algo_retry_at[0] = time.time() + ALGO_FALLBACK_SEC
    fb = algo_fallback_active()
    if st == 200 and isinstance(d, list):
        out, algo_ok = list(d), True
    elif st == 200 and isinstance(d, dict) and isinstance(d.get("orders"), list):
        out, algo_ok = list(d["orders"]), True
    if not (fb or include_legacy):
        return out, algo_ok
    st2, d2 = _request("GET", "/fapi/v1/openOrders", {"symbol": symbol}, signed=True)
    legacy_ok = st2 == 200 and isinstance(d2, list)
    if legacy_ok:
        out += [o for o in d2 if _order_type(o) in ("STOP_MARKET", "STOP", "TAKE_PROFIT_MARKET",
                                                     "TRAILING_STOP_MARKET")]
    ok = legacy_ok and (algo_ok or fb)
    return out, ok


def _order_type(o):
    """Algo 端點的型別欄位叫 orderType，舊端點叫 type。兩個都看。"""
    return str(o.get("orderType") or o.get("type") or o.get("origType") or "").upper()


def has_stop_order(orders, pos):
    """這個部位自己的停損還在不在（BINANCE_LESSONS 第 7 條）。

    只認部位上記錄過的 algoId / orderId：共用帳號時別的專案同幣的停損不是我們的，
    不能拿來當「停損還在」的證據。移動停利也不算——它要到 2R 才啟動、只涵蓋一半，
    以前把它算進來，停損不見時會被它蓋過去。
    沒記錄 id 的舊部位才退而用「類型＋方向」判斷。
    """
    ids = {str(o["id"]) for o in (pos.get("orders") or [])
           if _order_type(o) == "STOP_MARKET" and o.get("id")}
    if ids:
        return any(str(o.get("algoId") or o.get("orderId")) in ids for o in orders)
    close_side = "SELL" if pos["side"] == "LONG" else "BUY"
    return any(_order_type(o) in ("STOP_MARKET", "STOP") and o.get("side") == close_side
               and (o.get("positionSide") or "BOTH") in ("BOTH", pos["side"]) for o in orders)


_missing_streak = {}          # symbol → 連續幾次確認停損不在
_replace_fails = {}           # symbol → 補掛連續失敗幾次（告警節奏用）
_guard_errors = {}            # symbol → 停損檢查程式出錯幾次（告警節奏用）
_pos_errors = {}              # (步驟, 鍵) → 程式出錯幾次（第 8 條 r19：同一部位每一步各自 try）


def _pos_step_error(step, key, e):
    """某個部位的某一步出錯：寫錯誤區＋照節奏推播（第 14 條：不能被 try 吞掉）。"""
    k = (step, str(key))
    n = _pos_errors.get(k, 0) + 1
    _pos_errors[k] = n
    sys.stderr.write(f"  ! {key} {step}出錯（第 {n} 次）：{type(e).__name__}: {str(e)[:100]}\n")
    if alert_due(n):
        _notify(f"⚠ {step}出錯（第 {n} 次）",
                f"{key} {step}時程式出錯：{type(e).__name__}: {str(e)[:120]}\n"
                f"其他部位與其他步驟照常執行。每 {CFG.get('positionPoll', 20):g} 秒重試一次。")


def _pos_step_ok(step, key):
    n = _pos_errors.pop((step, str(key)), 0)
    if n:
        _notify(f"{step}恢復", f"{key} {step}恢復正常（已補上：先前出錯 {n} 次）。")


def _guard_one(sym, pos, events):
    """檢查單一部位的停損（guard_positions 的迴圈內容）。

    抽成獨立函式，讓 guard_positions 能對每個部位各自接住例外：
    以前某個部位丟例外，迴圈就中斷，排在後面的部位整輪都不檢查（第 8 條 r17）。
    """
    orders, ok = open_algo_orders(sym, include_legacy=any(o.get("via") == "legacy"
                                                          for o in pos.get("orders") or []))
    if not ok:
        return "query_failed"               # 原則 1：查詢失敗 ≠ 停損不在
    if has_stop_order(orders, pos):
        _missing_streak[sym] = 0
        n = _replace_fails.pop(sym, 0)
        if n:                                 # 之前補掛失敗過，現在停損回來了（例如上次逾時但交易所端其實成功）
            events.append({"symbol": sym, "action": "recovered", "stop": pos["stop"], "fails": n})
            _notify("停損已恢復", f"{sym} 的停損 {pos['stop']:g} 又查得到了（已補上：先前補掛失敗 {n} 次）。")
        return "present"

    n = _missing_streak.get(sym, 0) + 1
    _missing_streak[sym] = n
    if n < 3:                                 # 原則 2
        return "counting"

    close_side = "SELL" if pos["side"] == "LONG" else "BUY"
    f = _filters.get(sym) or {}
    tick = f.get("tick") or 0.0
    stop_px = round_step(pos["stop"], tick) if tick else pos["stop"]

    # 第 2 條 r19：補掛前確認部位（扣基準）。對帳出錯時守衛照樣會跑，部位可能其實已被平掉
    status, st, r, ep = place_stop(pos, stop_px)
    if status in ("unknown", "gone"):
        # unknown＝查不到：這輪不動，不算補掛失敗；gone＝自己的部位已經沒了：不補，交給對帳記平倉
        events.append({"symbol": sym, "action": "skip", "why": status})
        return status
    msg = str((r or {}).get("msg") or r)
    code = r.get("code") if isinstance(r, dict) else None
    if code == -2021 and not pos.get("pendingClose"):
        # 第 8 條 r16：停損不見、補掛時價格已穿過停損——停損本來就該觸發了。只告警的話部位沒有停損、
        # 價格又在錯的那一邊。直接走平倉流程（看結果，失敗就待平倉）；不看 guardClose 設定。
        # 這條路徑送單時交易所上本來就沒有停損（正是不見了才要補），沒辦法先掛回去，只能盡快出場。
        _missing_streak[sym] = 0
        closed, cwhy = _market_close(sym, pos["side"], pos["qty"], pos.get("base") or 0, pos=pos)
        if closed is True:
            px = _exit_price(sym, pos)
            _replace_fails.pop(sym, None)
            record_close(pos, px, "停損不見且價格已穿過停損價，直接出場")
            events.append({"symbol": sym, "action": "closed", "why": msg[:120]})
            _notify("⚠ 停損不見且價格已穿過，已出場",
                    f"{sym} 連續三輪查不到停損，補掛時價格已穿過停損價 {stop_px:g}（{msg[:80]}），已市價出場。")
        elif closed != "gone":
            _set_pending_close(pos, "停損不見且價格已穿過停損價", cwhy)
            events.append({"symbol": sym, "action": "pending_close", "why": cwhy})
        return "exited" if closed is True else ("gone" if closed == "gone" else "pending_close")
    if st == 200:
        _missing_streak[sym] = 0
        prior = _replace_fails.pop(sym, 0)
        pos["orders"] = [o for o in (pos.get("orders") or []) if _order_type(o) != "STOP_MARKET"]
        pos["orders"].append({"type": "STOP_MARKET", "id": r.get("algoId") or r.get("orderId"),
                              "px": stop_px, "via": ep})
        save_state()
        events.append({"symbol": sym, "action": "restored", "stop": stop_px, "fails": prior})
        _notify("停損已補掛", f"{sym} 連續三輪確認停損不在，已重新掛回 {stop_px:g}。"
                + (f"（已補上：先前補掛失敗 {prior} 次）" if prior else ""))
        return "replaced"
    if "existing" in msg.lower() or "already" in msg.lower():
        # 原則 3：補掛回報已存在 → 其實停損還在，是誤報。
        # r10：這也是失敗狀態被清掉的地方，先前若告警過就要發恢復，計數同時歸零。
        _missing_streak[sym] = 0
        prior = _replace_fails.pop(sym, 0)
        events.append({"symbol": sym, "action": "false_alarm", "why": msg[:120], "fails": prior})
        if prior:
            _notify("停損已恢復", f"{sym} 補掛回報「已存在」，停損其實在（已補上：先前補掛失敗 {prior} 次）。")
        return "false_alarm"

    # 到這裡：連續三輪確認不在、補掛失敗、且失敗原因不是「已存在」
    if CFG.get("guardClose") and not pos.get("pendingClose"):   # 原則 4；待平倉期間只讓重試路徑送單
        closed, cwhy = _market_close(sym, pos["side"], pos["qty"], pos.get("base") or 0, pos=pos)
        if closed is True:
            px = _exit_price(sym, pos)
            record_close(pos, px, "停損單遺失且無法補掛，強制平倉")
            events.append({"symbol": sym, "action": "closed", "why": msg[:120]})
            _notify("⚠ 停損遺失，已強制平倉", f"{sym} 連續三輪查不到停損、補掛也失敗，已依設定市價平倉。\n原因：{msg[:120]}")
            return "force_closed"
        msg = f"{msg[:80]}；強制平倉也被拒：{cwhy}"
    n = _replace_fails.get(sym, 0) + 1
    _replace_fails[sym] = n
    events.append({"symbol": sym, "action": "alert", "why": msg[:120],
                   "stop": stop_px, "attempt": n, "alert": alert_due(n)})
    if alert_due(n):
        _notify(f"⚠ 停損不在且補掛失敗（第 {n} 次）",
                f"{sym}\n想要的停損 {stop_px:g}\n目前停損 無（交易所上找不到本部位的停損）\n"
                f"第 {n} 次補掛失敗：{msg[:120]}\n"
                f"每 {CFG.get('positionPoll', 20):g} 秒重試一次，補上時會再通知。"
                + ("" if CFG.get("guardClose") else "若要讓系統自動平倉，在設定開啟 guardClose。"))
    return "replace_failed"                   # 最後一句也要是 return，不能掉出函式（第 2 條 r26）


@engine_op
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
        try:
            _guard_one(sym, pos, events)
        except Exception as e:
            # 一個部位出錯不能讓其他部位跳過檢查；錯誤本身要讓人看到（第 14 條：不能被 try 吞掉）
            n = _guard_errors.get(sym, 0) + 1
            _guard_errors[sym] = n
            sys.stderr.write(f"  ! {sym} 停損檢查出錯（第 {n} 次）：{type(e).__name__}: {str(e)[:100]}\n")
            if alert_due(n):
                _notify(f"⚠ 停損檢查出錯（第 {n} 次）",
                        f"{sym} 檢查停損時程式出錯：{type(e).__name__}: {str(e)[:100]}\n"
                        f"這個部位的停損沒有被確認。其他部位照常檢查。每 {CFG.get('positionPoll', 20):g} 秒重試一次。")
        else:
            if _guard_errors.pop(sym, 0):
                _notify("停損檢查恢復", f"{sym} 停損檢查恢復正常（已補上）。")
    return events


def cancel_stop_orders(symbol, pos):
    """只取消停損單，保留停利與移動停利。
    全部撤掉或本來就不在（-2011）才回 True；任何一張撤不掉就回 False，
    呼叫端不能繼續掛新的——舊 id 留在帳上，平倉時 record_close 會一起撤（第 8、13 條）。"""
    done, gone, failed = cancel_position_orders(symbol, pos, only_type="STOP_MARKET")
    return not failed


@engine_op
def move_to_breakeven(pos, mark, force=False, reason=None):
    """到達設定的 R 倍數後，把停損移到成本價（BINANCE_LESSONS 第 8 條）。

    移動停利要到 2R 才啟動，1R 到 2R 之間的回落無法保護；這一步補上那個缺口。

    幣安不允許同方向同時有兩張 closePosition 停損，所以順序是
    撤舊 → 掛新 → 掛不上就立刻把舊的掛回去（裸倉窗口約一秒）。

    觸發一次後把「想要的停損價」記在 pos["wantStop"]，之後每輪都重試直到成功，
    不再看價格有沒有在 1R 以上——價格回落到 1R 下方時條件不會再成立，錯過就永遠錯過。
    價格若已穿過想要的停損（-2021），直接市價出場，結果就是約略打平。
    回傳事件：ok / retry（first 表示第一次失敗）/ exited / naked。
    """
    if pos.get("beMoved") or pos.get("pendingClose"):
        # 待平倉期間只讓「每輪重試」那條路動它（第 8 條 r14）
        return {"symbol": pos.get("symbol"), "ok": False,
                "skipped": "pending_close" if pos.get("pendingClose") else "done"}
    sgn = 1 if pos["side"] == "LONG" else -1
    sym = pos["symbol"]
    want = pos.get("wantStop")
    if want is None:
        if not force:
            be = (pos.get("exits") or {}).get("breakeven")
            if be is None or (mark - be) * sgn < 0:
                return {"symbol": sym, "ok": False, "skipped": "not_due"}      # 還沒到
        elif (mark - pos["entry"]) * sgn <= 0:
            return {"symbol": sym, "ok": False, "skipped": "not_due"}          # 強制移損也要在成本之上才有意義
        f = _filters.get(sym) or {}
        tick = f.get("tick") or 0.0
        # 成本價加一點點手續費緩衝，避免剛好打平還倒貼手續費
        want = pos["entry"] * (1 + sgn * 0.0015)
        want = round_step(want, tick) if tick else want
        pos["wantStop"] = want
        pos["beFails"] = 0
        save_state()

    close_side = "SELL" if pos["side"] == "LONG" else "BUY"
    old = pos["stop"]
    fails = pos.get("beFails", 0)

    def _fail(why, **extra):
        # r10：計數與告警都在這裡——不管是背景迴圈還是反向訊號路徑呼叫，第 1 次失敗都會告警
        n = fails + 1
        pos["beFails"] = n
        pos["beLastErr"] = why
        save_state()
        cur = None if extra.get("naked") else pos["stop"]
        if alert_due(n):
            _notify(("⚠ 停損暫時遺失" if extra.get("naked") else "移損到成本仍未成功") + f"（第 {n} 次）",
                    f"{sym}\n想要的停損 {want:g}\n目前停損 "
                    + (f"{cur:g}" if cur is not None else "無（新舊都掛不上，守衛補掛中）")
                    + f"\n第 {n} 次失敗：{why}\n每 {CFG.get('positionPoll', 20):g} 秒重試一次，成功時會再通知。")
        return {"symbol": sym, "ok": False, "retry": True, "attempt": n, "alert": alert_due(n),
                "want": want, "current": cur, "why": why, **extra}

    # 第 2 條 r20：移損看起來是「更新」停損，實際上是新掛一張。撤舊之前先確認部位（扣基準）：
    # 查不到 → 這輪不動、不算失敗（下輪重試）；已經沒了 → 不動，交給對帳。
    own, s_ = _own_live(pos)
    if s_ != "ok":
        # 回傳略過的原因（unknown＝查不到、gone＝沒了），讓呼叫端與測試分得出兩者（測錯方式第 16 種）；不算失敗
        return {"symbol": sym, "ok": False, "skipped": s_, "want": want}

    if not cancel_stop_orders(sym, pos):
        return _fail("撤不掉舊停損，這輪不動，下輪重試")

    _s, st, r, ep = place_stop(pos, want, own=own)
    if st == 200:
        pos["stop"] = want
        pos["beMoved"] = True
        pos.pop("wantStop", None)
        pos["beFails"] = 0
        pos["orders"] = [o for o in (pos.get("orders") or []) if _order_type(o) != "STOP_MARKET"]
        pos["orders"].append({"type": "STOP_MARKET", "id": r.get("algoId") or r.get("orderId"),
                              "px": want, "via": ep})
        save_state()
        _notify("停損移至成本" if not reason else f"{reason} → 停損移至成本",
                f"{sym} " + (f"{reason}，" if reason else f"已到 {CFG.get('breakevenR', 1):g}R，")
                + f"停損從 {old:g} 移到 {want:g}（現價 {mark:g}）。\n這筆最差就是打平。"
                + (f"\n（已補上：先前失敗 {fails} 次）" if fails else ""))
        return {"symbol": sym, "ok": True, "old": old, "new": want, "mark": mark,
                "recovered": fails > 0, "fails": fails}

    code = r.get("code") if isinstance(r, dict) else None
    why = str((r or {}).get("msg") if isinstance(r, dict) else r)[:100]
    # 其他失敗、以及 -2021（價格已穿過想要的停損）：都先把舊停損掛回去，絕不留裸倉。
    # 第 8 條 r14：交易所停損要等平倉確認之後才撤。-2021 時舊停損已經為了「移動」撤掉了，
    # 所以先恢復它，再送平倉單；平掉了才由 record_close 撤，沒平掉就記待平倉、停損繼續保護。
    _s, st2, r2, ep2 = place_stop(pos, old, own=own)          # 剛確認過，直接帶數量（時間要緊）
    pos["orders"] = [o for o in (pos.get("orders") or []) if _order_type(o) != "STOP_MARKET"]
    if st2 == 200:
        pos["orders"].append({"type": "STOP_MARKET", "id": r2.get("algoId") or r2.get("orderId"),
                              "px": old, "via": ep2})
    if code == -2021:
        closed, cwhy = _market_close(sym, pos["side"], pos["qty"], pos.get("base") or 0, pos=pos)
        if closed is True:
            px = _exit_price(sym, pos)
            record_close(pos, px, "移損到成本時價格已穿過成本，直接出場")
            _notify("移損時已跌回成本，直接出場",
                    f"{sym} 想把停損移到 {want:g}，但價格已穿過，改以市價出場（約略打平）。")
            return {"symbol": sym, "ok": False, "exited": True, "want": want, "why": why}
        if closed == "gone":
            return {"symbol": sym, "ok": False, "gone": True, "want": want, "why": why}
        pos.pop("wantStop", None)                 # 不再移損；改由待平倉每輪重試出場
        _set_pending_close(pos, "移損到成本時價格已穿過成本", cwhy)
        return {"symbol": sym, "ok": False, "pendingClose": True, "want": want,
                "why": f"{why}；市價出場也被拒：{cwhy}"}
    if st2 == 200:
        return _fail(f"新停損掛不上（{why}），已恢復原停損，下輪重試")
    return _fail(f"新停損掛不上且原停損也掛不回（{why}），守衛將補掛", naked=True)


@engine_op
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
            # 以前只放進事件、main 寫一行錯誤區就沒了——錯誤被吞掉（第 14 條）
            _pos_step_error("移損", sym, e)
            out.append({"symbol": sym, "ok": False, "why": str(e)[:100]})
        else:
            _pos_step_ok("移損", sym)
    return out


def _stats(t):
    """一組交易的核心統計。performance() 與分版本統計共用。"""
    unknown = [x for x in t if x.get("pnl") is None]          # 損益未知不算勝負（r26）
    t = [x for x in t if x.get("pnl") is not None]
    if not t:
        return {"count": 0, "unknown": len(unknown)}
    wins = [x for x in t if x["pnl"] > 0]
    losses = [x for x in t if x["pnl"] <= 0]
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
    # 損益未知（結帳時資料缺欄位，r24）的不算勝也不算負，另外計數；以前被 `or 0` 算成虧損、勝率被拉低
    unknown = [x for x in t if x.get("pnl") is None]
    t = [x for x in t if x.get("pnl") is not None]
    if not t:
        return {"count": 0, "excluded": excluded_n, "unknown": len(unknown)}
    wins = [x for x in t if x["pnl"] > 0]
    losses = [x for x in t if x["pnl"] <= 0]
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
        "count": len(t), "excluded": excluded_n, "unknown": len(unknown),
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

@engine_op
def set_excluded(trade_id, excluded):
    for t in STATE["trades"]:
        if t.get("id") == trade_id:
            t["excluded"] = bool(excluded)
            save_state()
            return True
    return False


@engine_op
def save_state():
    if not STATE_FILE:
        return
    target = STATE_FILE
    if STATE.get("loadError"):
        # 讀取失敗期間不覆寫原檔：否則下一次重試會「成功」讀到一份空白的狀態（r37）。
        # r40：這段期間的狀態（例如網頁改的設定）寫到旁邊的 .unloaded 留作對照，不是靜靜丟掉
        target = f"{STATE_FILE}.unloaded"
    tmp = f"{target}.tmp"
    try:
        # 先寫暫存檔再換名：直接覆蓋寫，寫到一半當機就會留下壞檔（r37）
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"state": {k: STATE.get(k) for k in ("enabled", "pending", "positions", "trades",
                                                           "lastNet", "leftovers")},
                       "missed": MISSED, "conflicts": CONFLICTS,
                       "auto": AUTO,
                       # 金鑰與網路別刻意不存：金鑰只該在環境變數，
                       # 網路別只該由啟動參數決定，避免存檔把正式網狀態帶回來
                       "cfg": {k: CFG[k] for k in PERSIST_CFG}}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except Exception as e:
        # 以前是 except: pass——存檔失敗完全沒有訊息。狀態檔是成交界線、待平倉、pending 能跨重啟的唯一依據
        _pos_step_error("存檔", "狀態檔", e)
    else:
        _pos_step_ok("存檔", "狀態檔")


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


@engine_op
def load_state(path, _retry=False):
    """讀狀態檔。回傳 (ok, 錯誤)。第 8 條 r37、r38：「讀取失敗」與「沒有資料」不能是同一個結果。

    - 檔案不存在：全新開始，(True, None)。
    - 其他讀取或解析失敗：壞檔另存一份、記錯誤、推播；這段期間**暫停開新倉**、**不覆寫原檔**，
      每輪重試讀取（retry_load_state），讀到了發恢復通知。以前是 except: pass——靜靜回到空白，
      持倉、成交界線、待平倉、pending 全部消失，第一次存檔就把壞檔蓋掉、連證據都沒了。
    - 先全部解析到暫存，全部成功才一次換上，不留半套狀態。
    """
    global STATE_FILE
    STATE_FILE = path
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError as e:
        if STATE.get("loadError"):
            # r40：讀取失敗期間原檔不見了（被搬走、改名），不能當成「全新開始」——那是假恢復，
            # 交易所上的部位會被當成別人的、接著照常開新倉。要全新開始就放一份空白的 {} 進去。
            return _load_failed(path, RuntimeError(f"讀取失敗期間狀態檔不見了（{type(e).__name__}）；"
                                                    "確定要全新開始請放一份內容為 {} 的檔案"), True)
        STATE["loadError"] = None
        return True, None
    except Exception as e:
        return _load_failed(path, e, _retry)
    try:
        st = d.get("state", d)          # 相容舊格式
        trades = [dict(t) for t in (st.get("trades") or [])]
        for i, t in enumerate(trades):
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
        new_state = {"enabled": st.get("enabled", False), "positions": st.get("positions") or {},
                     "trades": trades, "lastNet": st.get("lastNet"),
                     "pending": st.get("pending") or {}, "leftovers": st.get("leftovers") or []}
        new_auto = {k: v for k, v in (d.get("auto") or {}).items() if k in AUTO}
        new_cfg = {k: v for k, v in (d.get("cfg") or {}).items() if k in PERSIST_CFG and v is not None}
        missed, conflicts = list(d.get("missed") or []), list(d.get("conflicts") or [])
    except Exception as e:
        return _load_failed(path, e, _retry)
    was = STATE.get("loadError")
    STATE.update(new_state)
    AUTO.update(new_auto)
    CFG.update(new_cfg)
    MISSED[:] = missed
    CONFLICTS[:] = conflicts
    STATE["loadError"] = None
    if was:
        _notify("狀態檔已讀到，恢復開新倉", f"{path}\n（已補上：先前讀取失敗 {was.get('attempts', 1)} 次）")
    return True, None


def _load_failed(path, e, retry):
    err = f"{type(e).__name__}: {str(e)[:150]}"
    le = STATE.get("loadError") or {}
    if not retry or not le:
        backup = None
        try:
            import shutil
            backup = f"{path}.bad-{int(time.time())}"
            shutil.copyfile(path, backup)          # 複製，不是改名：原檔留在原路徑（r40：改名後原路徑空了＝假恢復）
        except Exception:
            backup = None
        le = {"path": path, "error": err, "backup": backup, "since": int(time.time() * 1000), "attempts": 0}
    le["attempts"] = le.get("attempts", 0) + 1
    le["error"] = err
    STATE["loadError"] = le
    sys.stderr.write(f"  ! 狀態檔讀取失敗（第 {le['attempts']} 次）：{err}\n")
    if alert_due(le["attempts"]):
        _notify(f"⚠ 狀態檔讀不到，暫停開新倉（第 {le['attempts']} 次）",
                f"{path}\n{err}\n壞檔已另存：{le.get('backup') or '另存失敗'}\n"
                f"持倉紀錄還沒載入：不開新倉、不覆寫原檔，交易所上的部位請先到幣安確認。每輪重試讀取。")
    return False, err


@engine_op
def retry_load_state():
    """讀取失敗期間每輪重試（例如人工把狀態檔修好或還原）。回傳原因。"""
    le = STATE.get("loadError")
    if not le:
        return "loaded"
    ok, _err = load_state(le["path"], _retry=True)
    return "loaded" if ok else "still_failed"


@engine_op
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
                     "opened", "closedR", "closedUsd", "unknownToday", "blocked", "blockedAtR", "blockedAtUsd", "day",
                 "cooldownWinMin")},
                 "oneR": _one_r_usd()},
    }
