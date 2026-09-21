"""模擬幣安合約帳戶，在 _request_raw（HTTP 回應）這一層攔截，讓真的 trader.py 跑過。

要跟真實幣安一致（BINANCE_LESSONS 用法第 5 點）：條件單送舊端點回 -4120、單向空單為負數、
雙向回 LONG/SHORT 兩列、雙向超量平倉被拒。test_r11、test_r14 共用。
"""
import trader as T


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
