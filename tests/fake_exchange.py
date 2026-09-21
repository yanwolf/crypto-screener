"""模擬幣安合約帳戶，在 _request_raw（HTTP 回應）這一層攔截，讓真的 trader.py 跑過。

要跟真實幣安一致（BINANCE_LESSONS 用法第 5 點）：條件單送舊端點回 -4120、單向空單為負數、
雙向回 LONG/SHORT 兩列、雙向超量平倉被拒。test_r11、test_r14 共用。
"""
import atexit
import json
import os
import time as _real_time

import trader as T

# 突變命中紀錄：每一項測試裡，被突變的查詢實際被呼叫幾次（0 次＝這項與突變無關，r23）
CURRENT = ["?"]
HITS = {}


def _mutation_hit():
    HITS[CURRENT[0]] = HITS.get(CURRENT[0], 0) + 1


def _dump_hits():
    path = os.environ.get("MUTATION_LOG")
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(HITS, fh, ensure_ascii=False)


atexit.register(_dump_hits)


class FakeEx:
    """模擬幣安合約帳戶：持倉模式、部位、條件單、各種拒絕與逾時。"""

    def __init__(self, mode="oneway"):
        self.mode = mode
        self.dual_fail = False
        self.pos = {}                  # (symbol, side) → [qty, entry]
        self.algo = {}                 # algoId → params
        self.next_id = 1000
        self.mark = {}
        self.reject_market = set()     # 這些幣的市價單被拒（例如保證金不足）
        self.entry_timeout = False     # 進場市價單成交了，但回應逾時（狀態 0）
        self.reject_algo = None        # callable(params) → (st, body) 或 None
        self.calls = []
        self.cache_seen_on_resend = []

    def _mode_ok(self, params, reduce_kind):
        has_ps = "positionSide" in params
        if self.mode == "hedge":
            if not has_ps:
                return False, -4061
            if params.get("reduceOnly"):
                return False, -1106
        elif has_ps:
            return False, -4061
        return True, None

    def rows(self, sym=None):
        out = []
        syms = {s for s, _ in self.pos} | ({sym} if sym else set())
        for s in syms:
            if sym and s != sym:
                continue
            if self.mode == "hedge":
                for side in ("LONG", "SHORT"):
                    q, e = self.pos.get((s, side), [0, 0])
                    out.append({"symbol": s, "positionSide": side, "positionAmt": str(q if side == "LONG" else -q),
                                "entryPrice": str(e), "markPrice": str(self.mark.get(s, e)), "unRealizedProfit": "0"})
            else:
                lq, le = self.pos.get((s, "LONG"), [0, 0])
                sq, se = self.pos.get((s, "SHORT"), [0, 0])
                amt, e = (lq, le) if lq else (-sq, se)
                out.append({"symbol": s, "positionSide": "BOTH", "positionAmt": str(amt), "entryPrice": str(e),
                            "markPrice": str(self.mark.get(s, e)), "unRealizedProfit": "0"})
        return out

    def __call__(self, method, path, params, signed, timeout):
        params = dict(params or {})
        self.calls.append((method, path, params))
        if path == "/fapi/v1/positionSide/dual":
            return (0, {"error": "timeout"}) if self.dual_fail else (200, {"dualSidePosition": self.mode == "hedge"})
        if path == "/fapi/v2/account":
            return 200, {"totalWalletBalance": "1000", "totalMarginBalance": "1000", "availableBalance": "1000",
                         "totalPositionInitialMargin": "0", "totalUnrealizedProfit": "0"}
        if path == "/fapi/v1/premiumIndex":
            return 200, {"markPrice": str(self.mark.get(params.get("symbol"), 100.0))}
        if path in ("/fapi/v1/leverage", "/fapi/v1/leverageBracket"):
            return 200, {}
        if path == "/fapi/v2/positionRisk":
            if params.get("symbol") and os.environ.get("MUTATE_SYMBOL_EMPTY"):
                _mutation_hit()
                return 200, []                                # 突變測試：逐幣查詢一律回空清單（基準查不到）
            return 200, self.rows(params.get("symbol"))
        if path == "/fapi/v1/openAlgoOrders":
            return 200, [dict(v, algoId=k) for k, v in self.algo.items()
                         if not params.get("symbol") or v.get("symbol") == params.get("symbol")]
        if path == "/fapi/v1/order" and method == "POST" and params.get("type") == "MARKET":
            sym, side = params["symbol"], params["side"]
            ok, code = self._mode_ok(params, None)
            if not ok:
                self.cache_seen_on_resend.append(T._mode.get("hedge"))
                return 400, {"code": code, "msg": "position side does not match"}
            qty = float(params["quantity"])
            if self.mode == "hedge":
                pside = params["positionSide"]
                reduce = (pside == "LONG" and side == "SELL") or (pside == "SHORT" and side == "BUY")
            else:
                reduce = bool(params.get("reduceOnly"))
                pside = ("LONG" if side == "SELL" else "SHORT") if reduce else ("LONG" if side == "BUY" else "SHORT")
            if reduce and sym in self.reject_market:
                return 400, {"code": -2019, "msg": "Margin is insufficient."}
            q, e = self.pos.get((sym, pside), [0, 0])
            if reduce:
                if self.mode == "hedge" and qty > q + 1e-9:
                    return 400, {"code": -4118, "msg": "ReduceOnly Order Failed."}
                self.pos[(sym, pside)] = [max(0.0, q - qty), e]
            else:
                px = self.mark.get(sym, 100.0)
                self.pos[(sym, pside)] = [q + qty, px]
                if self.entry_timeout:
                    return 0, {"error": "timed out"}
            return 200, {"orderId": 1}
        if path == "/fapi/v1/order" and method == "POST" and params.get("type") != "MARKET":
            # 真實幣安 2025-12-09 起：條件單送到舊端點一律 -4120（清單第 1 條）
            return 400, {"code": -4120, "msg": "Order type not supported for this endpoint. "
                                                "Please use the Algo Order API endpoints instead."}
        if path == "/fapi/v1/algoOrder" and method == "POST":
            ok, code = self._mode_ok(params, None)
            if not ok:
                return 400, {"code": code, "msg": "position side does not match"}
            if self.reject_algo:
                r = self.reject_algo(params)
                if r:
                    return r
            self.next_id += 1
            self.algo[self.next_id] = params
            return 200, {"algoId": self.next_id}
        if path == "/fapi/v1/algoOrder" and method == "DELETE":
            i = int(params["algoId"])
            if i in self.algo:
                self.algo.pop(i)
                return 200, {}
            return 400, {"code": -2011, "msg": "Unknown order sent."}
        return 200, {}


class Clock:
    """可控的時鐘：只改 time()，其他照真的。sleep 不真的睡。"""
    def __init__(self):
        self.now = 1_800_000_000.0

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s

    def __getattr__(self, name):
        return getattr(_real_time, name)


class Ex(FakeEx):
    """在 test_r11 的模擬交易所上補：Algo 端點 404、舊端點可接受條件單、查舊端點掛單、
    reduceOnly 沒有部位時拒絕（-2022）、市價單回傳成交均價、全量部位表偶發空清單。"""

    def __init__(self, mode="oneway"):
        super().__init__(mode)
        self.algo_404 = False
        self.legacy_accept = False
        self.legacy = {}
        self.full_list_empty = 0
        self.risk_fail_after_market = False

    def __call__(self, method, path, params, signed, timeout):
        params = dict(params or {})
        if path.startswith("/fapi/v1/algoOrder") or path == "/fapi/v1/openAlgoOrders":
            if self.algo_404:
                self.calls.append((method, path, params))
                return 404, {"error": "Not Found"}
        if path == "/fapi/v1/openOrders":
            self.calls.append((method, path, params))
            return 200, [dict(v, orderId=k, type=v.get("type")) for k, v in self.legacy.items()
                         if v.get("symbol") == params.get("symbol")]
        if path == "/fapi/v1/order" and method == "POST" and params.get("type") != "MARKET":
            self.calls.append((method, path, params))
            if not self.legacy_accept:
                return 400, {"code": -4120, "msg": "Order type not supported for this endpoint. Please use the Algo Order API endpoints instead."}
            self.next_id += 1
            self.legacy[self.next_id] = params
            return 200, {"orderId": self.next_id}
        if path == "/fapi/v1/order" and method == "DELETE":
            self.calls.append((method, path, params))
            i = int(params.get("orderId") or 0)
            if i in self.legacy:
                self.legacy.pop(i)
                return 200, {}
            return 400, {"code": -2011, "msg": "Unknown order sent."}
        if path == "/fapi/v2/positionRisk":
            # 只在「平倉單」送出之後才失敗（以前連進場單也算，平倉前的確認就先失敗，r14 的 8-a 因此空跑）
            if self.risk_fail_after_market and any(c[1] == "/fapi/v1/order" and c[2].get("type") == "MARKET"
                                                   and c[2].get("reduceOnly") for c in self.calls):
                self.calls.append((method, path, params))
                return 500, {"msg": "Internal error"}
            if not params.get("symbol") and self.full_list_empty > 0:
                self.full_list_empty -= 1
                self.calls.append((method, path, params))
                return 200, []
        if path == "/fapi/v1/order" and method == "POST" and params.get("type") == "MARKET":
            reduce = bool(params.get("reduceOnly")) or (
                "positionSide" in params and
                ((params["positionSide"] == "LONG") == (params["side"] == "SELL")))
            if reduce and self._mode_ok(params, None)[0]:
                pside = params.get("positionSide") or ("LONG" if params["side"] == "SELL" else "SHORT")
                if self.pos.get((params["symbol"], pside), [0])[0] <= 0:
                    self.calls.append((method, path, params))
                    return 400, {"code": -2022, "msg": "ReduceOnly Order is rejected."}
            if not reduce:
                sym = params["symbol"]
                pside = params.get("positionSide") or ("LONG" if params["side"] == "BUY" else "SHORT")
                q0, e0 = self.pos.get((sym, pside), [0, 0])
                fill = self.mark.get(sym, 100.0)
                st, d = super().__call__(method, path, params, signed, timeout)
                qty = float(params["quantity"])
                if st in (200, 0):
                    self.pos[(sym, pside)] = [q0 + qty, (q0 * e0 + qty * fill) / (q0 + qty)]
                if st == 200:
                    d = dict(d, avgPrice=str(fill), executedQty=str(qty), status="FILLED")
                return st, d
        return super().__call__(method, path, params, signed, timeout)


PROGRAM_ERRORS = ("NameError", "AttributeError", "KeyError", "TypeError", "UnboundLocalError", "Traceback")


class Pre(Exception):
    """前提不成立：測試根本沒走到要測的地方（用法第 5 點 r17）。"""


def need(cond, msg):
    if not cond:
        raise Pre(msg)


def entry_sent(ex, since=0):
    """這段期間有沒有送出進場市價單（非 reduceOnly、非雙向平倉）。"""
    for m, p, prm in ex.calls[since:]:
        if p == "/fapi/v1/order" and prm.get("type") == "MARKET" and not prm.get("reduceOnly"):
            ps = prm.get("positionSide")
            if not ps or (ps == "LONG") == (prm.get("side") == "BUY"):
                return True
    return False


class Ex17(Ex):
    """補：帶 symbol 的部位查詢可指定回空清單；可指定某幣的部位表只在全量查詢時缺漏。"""

    def __init__(self, mode="oneway"):
        super().__init__(mode)
        self.symbol_empty = 0             # 帶 symbol 的 positionRisk 回空清單的次數
        self.symbol_empty_after_market = False   # 平倉市價單送出之後，帶 symbol 的查詢才開始回空清單
        self.full_missing = False         # 全量 positionRisk 永遠缺漏所有幣（模擬全量表異常）

    def __call__(self, method, path, params, signed, timeout):
        params = dict(params or {})
        if path == "/fapi/v2/positionRisk":
            if params.get("symbol") and self.symbol_empty_after_market and any(
                    c[1] == "/fapi/v1/order" and c[2].get("type") == "MARKET" and c[2].get("reduceOnly")
                    for c in self.calls):
                self.calls.append((method, path, params))
                return 200, []
            if params.get("symbol") and self.symbol_empty > 0:
                self.symbol_empty -= 1
                self.calls.append((method, path, params))
                return 200, []
            if not params.get("symbol") and self.full_missing:
                self.calls.append((method, path, params))
                return 200, []
        return super().__call__(method, path, params, signed, timeout)


def realistic(fn, positions=None):
    """把簡化的 _request 替身包一層：查部位（positionRisk）時依帳上部位回傳真實格式的列，其他照原替身。

    行為改成「掛停損前先確認部位」後，替身對查部位回 {} 會被當成查不到、這輪不動（第 2 條 r20）。
    要補的是模擬環境，不是放寬程式（用法第 5 點 r13）。positions 不給就讀 T.STATE（呼叫當下，不綁舊物件）。
    """
    def wrapped(m, path, params=None, signed=False, timeout=15):
        if path == "/fapi/v2/positionRisk":
            src = positions if positions is not None else T.STATE["positions"]
            sym = (params or {}).get("symbol")
            rows = []
            for s, p in src.items():
                if sym and s != sym:
                    continue
                q = float(p.get("qty") or 0) + float(p.get("base") or 0)
                rows.append({"symbol": s, "positionSide": "BOTH",
                             "positionAmt": str(q if p.get("side") == "LONG" else -q),
                             "entryPrice": str(p.get("entry") or 0), "markPrice": str(p.get("entry") or 0),
                             "unRealizedProfit": "0"})
            if sym and not rows:
                rows = [{"symbol": sym, "positionSide": "BOTH", "positionAmt": "0", "entryPrice": "0"}]
            return 200, rows
        return fn(m, path, params, signed, timeout)
    return wrapped
