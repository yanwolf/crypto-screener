"""模擬幣安合約帳戶，在 _request_raw（HTTP 回應）這一層攔截，讓真的 trader.py 跑過。

要跟真實幣安一致（BINANCE_LESSONS 用法第 5 點）：條件單送舊端點回 -4120、單向空單為負數、
雙向回 LONG/SHORT 兩列、雙向超量平倉被拒。test_r11、test_r14 共用。
"""
import atexit
import json
import os
import sys
import time as _real_time

import trader as T

# 突變命中紀錄：每一項測試裡，被突變的查詢實際被呼叫幾次（0 次＝這項與突變無關，r23）
CURRENT = ["?"]
HITS = {}
TRACE = []                          # TRACE_FILLS=1 時記錄每次成交明細查詢（第 22 種）


def _mutation_hit():
    HITS[CURRENT[0]] = HITS.get(CURRENT[0], 0) + 1


def _dump_hits():
    path = os.environ.get("MUTATION_LOG")
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(HITS, fh, ensure_ascii=False)
    tp = os.environ.get("TRACE_FILLS_LOG")
    if tp and TRACE:
        with open(tp, "a", encoding="utf-8") as fh:
            for r in TRACE:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")


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
        self.inject_log = []
        self.fills = []                  # 成交明細（userTrades）
        self.slip = 0.001                # 市價單滑價
        self.trades_fail = False         # 成交明細查詢失敗
        self.same_ms = False             # 所有成交落在同一毫秒
        self.clock_lag_ms = 0            # 交易所時鐘比本機慢幾毫秒（成交時間戳往前）
        self.next_order = 70000          # 市價單單號（每張不同）
        self.orders = {}                 # 市價單單號 → 最終狀態（GET /fapi/v1/order 查得到，第 15 條）
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
        """外部請求的唯一入口。r25：命中次數算「被突變的查詢被呼叫」，不是「突變分支被執行」——
        情境自己注入的回空清單會先回，走不到突變分支，以前因此被誤判成「無關」。"""
        if (path == "/fapi/v2/positionRisk" and (params or {}).get("symbol")
                and os.environ.get("MUTATE_SYMBOL_EMPTY")):
            _mutation_hit()
        return self._handle(method, path, params, signed, timeout)

    def _fill(self, sym, side, pside, qty, px, realized=0.0, order_id=None):
        """記一筆成交。r31：不用退化值——遞增的成交 id、非零手續費、平倉那筆的 realizedPnl（打平出場時剛好是 0）。
        same_ms：讓所有成交落在同一毫秒，測「界線用時間」會漏掉同一毫秒的另一筆。"""
        n = len(self.fills)
        self.fills.append({"id": 5000 + n, "symbol": sym, "side": side,
                           "positionSide": pside if self.mode == "hedge" else "BOTH",
                           "qty": str(qty), "price": str(px),
                           "time": int(T.time.time() * 1000) + (0 if self.same_ms else n) - self.clock_lag_ms,
                           "commission": str(round(qty * px * 0.0004, 8)), "realizedPnl": str(round(realized, 8)),
                           "orderId": order_id if order_id is not None else 900000 + n})

    def trigger(self, sym, pside, qty, px):
        """模擬交易所端的停損／停利觸發：減部位、記一筆成交（價格＝實際成交價）。"""
        q, e = self.pos.get((sym, pside), [0, 0])
        self.pos[(sym, pside)] = [max(0.0, q - qty), e]
        sgn = 1 if pside == "LONG" else -1
        self._fill(sym, "SELL" if pside == "LONG" else "BUY", pside, qty, px, realized=(px - e) * sgn * qty)

    def _inject(self, kind, path, params):
        """注入觸發時記下當下已送出哪些單（第 18 種：斷言注入是在被測那一步觸發）。"""
        sent = [c[2] for c in self.calls if c[1] in ("/fapi/v1/order", "/fapi/v1/algoOrder") and c[0] == "POST"]
        rec = {"kind": kind, "at": len(self.calls),
               "entries": sum(1 for p in sent if p.get("type") == "MARKET" and not p.get("reduceOnly")),
               "closes": sum(1 for p in sent if p.get("type") == "MARKET" and p.get("reduceOnly")),
               "stops": sum(1 for p in sent if p.get("type") == "STOP_MARKET"),
               "deletes": sum(1 for c in self.calls if c[0] == "DELETE")}
        self.inject_log.append(rec)
        if os.environ.get("INJECT_LOG"):
            print(f"    · 注入 {kind} 於第 {rec['at']} 個請求（已送進場 {rec['entries']}、平倉 {rec['closes']}、"
                  f"停損 {rec['stops']}、撤單 {rec['deletes']}）［{CURRENT[0]}］", file=sys.__stdout__)

    def _handle(self, method, path, params, signed, timeout):
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
                return 200, []                                # 突變測試：逐幣查詢一律回空清單（計數在 __call__）
            return 200, self.rows(params.get("symbol"))
        if path == "/fapi/v1/userTrades":
            if os.environ.get("TRACE_FILLS"):
                # 第 22 種的找法：記下每項測試查成交明細的結果，列出所有走進這條路的測試
                TRACE.append({"case": CURRENT[0], "fail": self.trades_fail,
                              "rows": sum(1 for f in self.fills if f["symbol"] == params.get("symbol")),
                              "sides": sorted({f["side"] for f in self.fills if f["symbol"] == params.get("symbol")})})
            if self.trades_fail:
                return 500, {"msg": "Internal error"}
            rows = [dict(f) for f in self.fills if f["symbol"] == params.get("symbol")]
            if params.get("orderId") is not None:
                rows = [f for f in rows if f["orderId"] == int(params["orderId"])]
            elif params.get("fromId") is not None:
                rows = [f for f in rows if f["id"] >= int(params["fromId"])]
            else:
                since = params.get("startTime")
                rows = [f for f in rows if since is None or f["time"] >= int(since)]
            # 照 limit 截斷（沒給時 500，跟幣安一樣）。以前一律回全部——「只查一頁」的錯永遠測不出來（r31 退化值）
            return 200, rows[:int(params.get("limit") or 500)]
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
            # 實際成交價刻意跟標記價差一點（滑價），測試才分得出程式用的是成交價還是標記價（r28）
            mk = self.mark.get(sym, 100.0)
            fill = round(mk * (1 - self.slip) if side == "SELL" else mk * (1 + self.slip), 6)
            self.next_order += 1
            oid = self.next_order
            if reduce:
                if self.mode == "hedge" and qty > q + 1e-9:
                    return 400, {"code": -4118, "msg": "ReduceOnly Order Failed."}
                self.pos[(sym, pside)] = [max(0.0, q - qty), e]
                self._fill(sym, side, pside, qty, fill, realized=(fill - e) * (1 if pside == "LONG" else -1) * qty,
                           order_id=oid)
            else:
                # 部位均價＝加權均價（r31：不能只記最後一筆；跟成交紀錄一致）
                self.pos[(sym, pside)] = [q + qty, (q * e + qty * fill) / (q + qty)]
                self._fill(sym, side, pside, qty, fill, order_id=oid)
                if self.entry_timeout:
                    return 0, {"error": "timed out"}
            res = {"orderId": oid, "avgPrice": str(fill), "executedQty": str(qty), "origQty": str(qty), "status": "FILLED"}
            self.orders[oid] = dict(res, symbol=sym)
            if params.get("newOrderRespType") != "RESULT":
                # 第 15 條：真的幣安沒帶 RESULT 時回 ACK——status NEW、成交 0、均價 0（以前一律回 FILLED，退化值）
                return 200, {"orderId": oid, "symbol": sym, "status": "NEW", "executedQty": "0",
                             "origQty": str(qty), "avgPrice": "0.00", "cumQuote": "0"}
            return 200, res
        if path == "/fapi/v1/order" and method == "GET":
            o = self.orders.get(int(params.get("orderId") or 0))
            return (200, dict(o)) if o else (400, {"code": -2013, "msg": "Order does not exist."})
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

    def _handle(self, method, path, params, signed, timeout):
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
            o = self.orders.get(i)
            if o is not None:                                     # 市價單（第 15 條）：還沒到最終狀態才撤得掉
                if o.get("status") in ("NEW", "PARTIALLY_FILLED"):
                    o["status"] = "CANCELED"
                    return 200, dict(o)
                return 400, {"code": -2011, "msg": "Unknown order sent."}
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
                st, d = super()._handle(method, path, params, signed, timeout)
                qty = float(params["quantity"])
                if st in (200, 0):
                    self.pos[(sym, pside)] = [q0 + qty, (q0 * e0 + qty * fill) / (q0 + qty)]
                if st == 200:
                    if isinstance(d, dict) and d.get("orderId") in self.orders:
                        self.orders[d["orderId"]]["avgPrice"] = str(fill)
                    if d.get("status") == "FILLED":           # ACK（沒帶 RESULT）照樣回 NEW／成交 0
                        d = dict(d, avgPrice=str(fill), executedQty=str(qty), status="FILLED")
                return st, d
        return super()._handle(method, path, params, signed, timeout)


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

    def _handle(self, method, path, params, signed, timeout):
        params = dict(params or {})
        if path == "/fapi/v2/positionRisk":
            if params.get("symbol") and self.symbol_empty_after_market and any(
                    c[1] == "/fapi/v1/order" and c[2].get("type") == "MARKET" and c[2].get("reduceOnly")
                    for c in self.calls):
                self.calls.append((method, path, params))
                self._inject("逐幣回空（平倉單送出後）", path, params)
                return 200, []
            if params.get("symbol") and self.symbol_empty > 0:
                self.symbol_empty -= 1
                self.calls.append((method, path, params))
                self._inject("逐幣回空", path, params)
                return 200, []
            if not params.get("symbol") and self.full_missing:
                self.calls.append((method, path, params))
                self._inject("全量回空", path, params)
                return 200, []
        return super()._handle(method, path, params, signed, timeout)


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


def injected_at_step(ex, since):
    """第 18 種：計數式注入（「接下來第 N 次查詢回空」）要打在被測那一步的第一次逐幣查詢上。
    since＝被測那一步開始前的請求數。回傳 (是否正好打中, 說明)。"""
    first = next((i for i, c in enumerate(ex.calls[since:], since)
                  if c[1] == "/fapi/v2/positionRisk" and c[2].get("symbol")), None)
    recs = [r for r in ex.inject_log if r["at"] > since]
    if first is None:
        return False, "被測那一步沒有逐幣查詢"
    if not recs:
        return False, "注入沒有觸發"
    return recs[0]["at"] == first + 1, f"注入在第 {recs[0]['at']} 個請求、這一步第一次逐幣查詢在第 {first + 1} 個"


def titled(alerts, title):
    """第 21 種（r31）：指定是哪一則——標題完全相等的那幾則，不在全部輸出裡找字。
    alerts：trader 出口的 {"title","text"}，或推播的 (title, text)。"""
    out = []
    for a in alerts:
        t, x = (a.get("title"), a.get("text")) if isinstance(a, dict) else (a[0], a[1])
        if t == title:
            out.append({"title": t, "text": x or ""})
    return out


class Ex42(Ex17):
    """第 15 條（r42）：市價單「交易所收下了、但回應不是成交」。

    market_new = {"open"|"close": 方式}，方式：
      now      回應 NEW／成交 0，但其實已經成交（查單回 FILLED）——ACK 的樣子
      later    回應 NEW／成交 0，再經過 later_after 個請求才成交
      never    回應 NEW／成交 0，最後沒成交（查單回 EXPIRED、成交 0）
      partial  回應 NEW／成交 0，最後只成交一半（查單回 EXPIRED、成交一半）
      stuck    回應 NEW／成交 0，一直是 NEW（撤單後變 CANCELED、成交 0）
      partial_stuck  回應 NEW／成交 0，成交 40% 後卡在 PARTIALLY_FILLED（撤單後 CANCELED、成交 40%；r44 pump-dump-hunter）
    跟有沒有帶 newOrderRespType=RESULT 無關：帶了 RESULT 也可能回 NEW。
    """

    def __init__(self, mode="oneway"):
        super().__init__(mode)
        self.market_new = {}
        self.drop_avg = False                # r42：回應與查單都沒有 avgPrice（gold-scalper 2026-09-22 實單的樣子）
        self.later_after = 3
        self.deferred = []                   # [剩幾個請求, 單號, 參數]

    @staticmethod
    def is_close(params):
        if params.get("reduceOnly"):
            return True
        ps = params.get("positionSide")
        return bool(ps) and ((ps == "LONG") == (params.get("side") == "SELL"))

    def _apply(self, params):
        """真的成交一次（走原本的模擬路徑），但不在 calls 裡多記一張送單。"""
        n = len(self.calls)
        st, d = super()._handle("POST", "/fapi/v1/order", dict(params), True, 15)
        del self.calls[n:]
        return st, d

    def _tick(self):
        for item in list(self.deferred):
            item[0] -= 1
            if item[0] <= 0:
                self.deferred.remove(item)
                _n, oid, params = item
                st, d = self._apply(params)
                o = self.orders[oid]
                if st == 200:
                    o.update(status="FILLED", executedQty=str(params["quantity"]),
                             avgPrice=(self.orders.get(d.get("orderId")) or d).get("avgPrice"))
                else:
                    o.update(status="EXPIRED")

    def _handle(self, method, path, params, signed, timeout):
        st, d = self._handle42(method, path, dict(params or {}), signed, timeout)
        if self.drop_avg and path == "/fapi/v1/order" and isinstance(d, dict) and "avgPrice" in d:
            d = {k: v for k, v in d.items() if k != "avgPrice"}
        return st, d

    def _handle42(self, method, path, params, signed, timeout):
        self._tick()
        if path == "/fapi/v1/order" and method == "POST" and params.get("type") == "MARKET":
            how = self.market_new.get("close" if self.is_close(params) else "open")
            if how:
                return self._market_new(how, method, path, params)
        return super()._handle(method, path, params, signed, timeout)

    def _market_new(self, how, method, path, params):
        sym, qty = params["symbol"], float(params["quantity"])
        ack = {"symbol": sym, "status": "NEW", "executedQty": "0", "origQty": str(qty), "avgPrice": "0.00", "cumQuote": "0"}
        self._inject(f"市價單回 NEW（{how}）", path, params)
        if how == "now":
            n = len(self.calls)
            st, d = super()._handle(method, path, dict(params, newOrderRespType="RESULT"), True, 15)
            self.calls[n:] = [(method, path, params)]
            if st != 200:
                return st, d
            return 200, dict(ack, orderId=d["orderId"])
        self.calls.append((method, path, params))
        self.next_order += 1
        oid = self.next_order
        o = dict(ack, orderId=oid)
        if how == "later":
            self.deferred.append([self.later_after, oid, dict(params, newOrderRespType="RESULT")])
        elif how == "never":
            o["status"] = "EXPIRED"
        elif how == "partial_stuck":
            part = round(int(qty * 0.4 * 10) / 10, 1)
            st, d = self._apply(dict(params, quantity=part, newOrderRespType="RESULT"))
            o.update(status="PARTIALLY_FILLED", executedQty=str(part),
                     avgPrice=(self.orders.get(d.get("orderId")) or {}).get("avgPrice", "0"))
        elif how == "partial":
            half = round(int(qty / 2 * 10) / 10, 1)
            st, d = self._apply(dict(params, quantity=half, newOrderRespType="RESULT"))
            o.update(status="EXPIRED", executedQty=str(half),
                     avgPrice=(self.orders.get(d.get("orderId")) or {}).get("avgPrice", "0"))
        self.orders[oid] = o
        return 200, dict(ack, orderId=oid)
