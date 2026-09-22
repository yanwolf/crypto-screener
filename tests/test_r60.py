"""BINANCE_LESSONS r58 → r60 逐段差異的檢查項目（crypto-screener）。

用法第 5 點 r59、r60：每個「某一步查不到」的設計都要有直接測試——部位正常開好、只在那一步查不到
（模擬交易所的 fail_when，只讓那一個查詢回 500），並用結果前提確認注入真的在那一步觸發。
突變檢查把整個逐幣查詢換掉，失敗多半在準備階段（部位根本沒開成），證明不了「那一步查不到時該怎樣」。
預期照這個專案的設計：查不到一律不送單、不結帳、不算失敗（第 2 條、第 3 條 r16、第 8 條 r54）。

    python3 -m tests.test_r60
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, ROOT)
from tests import test_r42 as R                                 # noqa: E402
from tests import test_r55 as R55                               # noqa: E402
from tests.fake_exchange import need, entry_sent                # noqa: E402
from tests.harness import make_case                             # noqa: E402

RESULTS = []
case = make_case(RESULTS)
fresh, open_long, market_posts, stops = R.fresh, R.open_long, R.market_posts, R.stops_on_exchange


def T():
    return R.T


def per_sym(m, p, prm):
    return p == "/fapi/v2/positionRisk" and prm.get("symbol") == "XUSDT"


def arm(ex, fn):
    """從現在起讓 fn 符合的查詢回 500；回傳注入紀錄的起點（結果前提用）。"""
    k0 = len(ex.inject_log)
    ex.fail_when = fn
    return k0


def hit(ex, k0):
    return any(r["kind"].startswith("查不到") for r in ex.inject_log[k0:])


def algo_posts(ex, since):
    return [c for c in ex.calls[since:] if c[0] == "POST" and c[1] == "/fapi/v1/algoOrder"]


def algo_deletes(ex, since):
    return [c for c in ex.calls[since:] if c[0] == "DELETE" and c[1] == "/fapi/v1/algoOrder"]


# ════ 第 8 條 r59、r60：手動平倉與對帳的回應 ═════════════════════

@case("r60-1", "交易所端重開（兩個證據成立）：手動平倉的回應寫「查到這筆的平倉成交」時，成交明細真的查了、而且湊滿數量")
def _():
    ex, clock = fresh()
    open_long(ex)
    R55.reopen(ex)
    need(R55.other_qty(ex) == R55.OTHER, "別人重開的部位沒放上去（前提）")
    n0 = len(ex.calls)
    r = T().close_position("XUSDT")
    said = "查到這筆的平倉成交" in str(r.get("error"))
    queried = any(c[1] == "/fapi/v1/userTrades" for c in ex.calls[n0:])
    if said and not queried:
        return "回應寫查到平倉成交，這次卻根本沒查成交明細"
    if not said:
        return f"回應沒講明是查到平倉成交才判定的：{r}"


@case("r60-2", "交易所端重開、成交明細查不到：手動平倉的回應不能寫「查到平倉成交」，要講明判斷不了、沒送單")
def _():
    ex, clock = fresh()
    open_long(ex)
    R55.reopen(ex)
    need(R55.other_qty(ex) == R55.OTHER, "別人重開的部位沒放上去（前提）")
    k0 = arm(ex, lambda m, p, prm: p == "/fapi/v1/userTrades")
    r = T().close_position("XUSDT")
    need(hit(ex, k0), "成交明細查詢沒有走到（結果前提）")
    if "查到這筆的平倉成交" in str(r.get("error")) or r.get("ok"):
        return f"查不到卻寫成查到／回報成功：{r}"
    if "判斷不了" not in str(r.get("error")):
        return f"沒講明判斷不了：{r}"


@case("r60-3", "對帳替被別人重開的那筆結帳：原因寫明交易所上現在那張是別的部位、沒有動它")
def _():
    ex, clock = fresh()
    open_long(ex)
    R55.reopen(ex)
    need(R55.other_qty(ex) == R55.OTHER, "別人重開的部位沒放上去（前提）")
    T().sync_positions()
    need(T().STATE["trades"], "對帳沒有結帳（前提：r56 起兩個證據成立會結帳）")
    why = str(T().STATE["trades"][-1].get("reason"))
    if "別的部位" not in why:
        return f"原因沒講明交易所上那張是別的部位：{why}"


@case("r60-4", "對帳結帳時出場價查不到：原因寫明記未知，出場價與損益都是未知（不推估）")
def _():
    ex, clock = fresh()
    open_long(ex)
    q = T().STATE["positions"]["XUSDT"]["qty"]
    ex.trigger("XUSDT", "LONG", q, 95.0)
    k0 = arm(ex, lambda m, p, prm: p == "/fapi/v1/userTrades")
    T().sync_positions()
    need(hit(ex, k0), "成交明細查詢沒有走到（結果前提）")
    need(T().STATE["trades"], "對帳沒有結帳（前提）")
    t = T().STATE["trades"][-1]
    if t.get("exit") is not None or t.get("pnl") is not None:
        return f"成交明細查不到，出場價／損益卻有數字（推估）：{t.get('exit')}／{t.get('pnl')}"
    if "記未知" not in str(t.get("reason")):
        return f"原因沒講明出場價記未知：{t.get('reason')}"


# ════ 用法第 5 點 r59、r60：每個「某一步查不到」的直接測試 ═══════════

@case("q-1", "平倉送單前逐幣查部位查不到（手動平倉）：不送、不結帳、停損留著、記待平倉")
def _():
    ex, clock = fresh()
    open_long(ex)
    need(stops(ex), "開倉後有停損（前提）")
    k0 = arm(ex, per_sym)
    n0 = len(ex.calls)
    r = T().close_position("XUSDT")
    need(hit(ex, k0), "逐幣查部位沒有走到（結果前提）")
    if market_posts(ex, n0, close=True) or r.get("ok"):
        return f"查不到還送了平倉單／回報成功：{r}"
    if "XUSDT" not in T().STATE["positions"] or not stops(ex):
        return "查不到就結帳或撤了停損"
    if not T().STATE["positions"]["XUSDT"].get("pendingClose"):
        return "沒記成待平倉（之後沒人重試）"


@case("q-2", "待平倉重試時逐幣查部位查不到：不送、待平倉保留（下一輪再試）")
def _():
    ex, clock = fresh()
    open_long(ex)
    T()._set_pending_close(T().STATE["positions"]["XUSDT"], "測試", "測試")
    k0 = arm(ex, per_sym)
    n0 = len(ex.calls)
    T().retry_pending_closes()
    need(hit(ex, k0), "逐幣查部位沒有走到（結果前提）")
    if market_posts(ex, n0, close=True):
        return "查不到還送了平倉單"
    if not (T().STATE["positions"].get("XUSDT") or {}).get("pendingClose"):
        return "待平倉被清掉了"


@case("q-3", "守衛查掛單查不到：不算停損不在（連查四輪都不補掛、不平倉、不累加）")
def _():
    ex, clock = fresh()
    open_long(ex)
    for k in [k for k, v in ex.algo.items() if v.get("type") == "STOP_MARKET"]:
        ex.algo.pop(k)                                          # 停損真的不在：查得到的話守衛會補
    k0 = arm(ex, lambda m, p, prm: p in ("/fapi/v1/openAlgoOrders", "/fapi/v1/openOrders"))
    n0 = len(ex.calls)
    for _i in range(4):
        T().guard_positions()
    need(hit(ex, k0), "查掛單沒有走到（結果前提）")
    if algo_posts(ex, n0) or market_posts(ex, n0, close=True):
        return "查不到掛單就當成停損不在、補掛或平倉了"
    if T()._missing_streak.get("XUSDT", 0):
        return f"查不到也累加了「停損不在」的次數：{T()._missing_streak.get('XUSDT')}"


@case("q-4", "守衛補掛前確認部位查不到：這輪不掛、不算補掛失敗")
def _():
    ex, clock = fresh()
    open_long(ex)
    for k in [k for k, v in ex.algo.items() if v.get("type") == "STOP_MARKET"]:
        ex.algo.pop(k)
    k0 = arm(ex, per_sym)
    n0 = len(ex.calls)
    for _i in range(3):
        T().guard_positions()
    need(hit(ex, k0), "補掛前確認部位沒有走到（結果前提）")
    if algo_posts(ex, n0):
        return "部位查不到還補掛了停損（部位可能已經沒了，掛出去就是孤兒單）"
    if T()._replace_fails.get("XUSDT", 0):
        return "查不到被算成補掛失敗（會誤發告警）"


@case("q-5", "移損前確認部位查不到：不撤舊停損、不掛新停損")
def _():
    ex, clock = fresh()
    open_long(ex)
    pos = T().STATE["positions"]["XUSDT"]
    need(stops(ex), "開倉後有停損（前提）")
    k0 = arm(ex, per_sym)
    n0 = len(ex.calls)
    T().move_to_breakeven(pos, 108.0, force=True, reason="測試")
    need(hit(ex, k0), "移損前確認部位沒有走到（結果前提）")
    if algo_deletes(ex, n0) or algo_posts(ex, n0):
        return "部位查不到還撤了舊停損或掛了新停損"
    if not stops(ex):
        return "停損不見了"


@case("q-6", "對帳的全量部位表查不到：不動帳、回報對帳失敗")
def _():
    ex, clock = fresh()
    open_long(ex)
    k0 = arm(ex, lambda m, p, prm: p == "/fapi/v2/positionRisk" and not prm.get("symbol"))
    r = T().sync_positions()
    need(hit(ex, k0), "全量部位表查詢沒有走到（結果前提）")
    if "XUSDT" not in T().STATE["positions"] or T().STATE["trades"]:
        return "全量表查不到就結帳了"
    if not (r or {}).get("error"):
        return f"沒回報對帳失敗：{r}"


@case("q-7", "對帳時全量表沒有這檔、逐幣補查又查不到：不結帳（查不到不等於沒有）")
def _():
    ex, clock = fresh()
    open_long(ex)
    ex.pos[("XUSDT", "LONG")][0] = 0.0                          # 全量表上這檔是 0（偶發）
    k0 = arm(ex, per_sym)
    T().sync_positions()
    need(hit(ex, k0), "逐幣補查沒有走到（結果前提）")
    if "XUSDT" not in T().STATE["positions"] or T().STATE["trades"]:
        return "逐幣補查查不到就結帳了"


@case("q-8", "開倉前查基準部位查不到：不送進場單、不留待確認")
def _():
    ex, clock = fresh()
    ex.mark["XUSDT"] = 100.4
    k0 = arm(ex, per_sym)
    n0 = len(ex.calls)
    r = T().open_position("X", "LONG", 100.0, None, stop_pct=5)
    need(hit(ex, k0), "查基準部位沒有走到（結果前提）")
    if entry_sent(ex, n0) or r.get("ok"):
        return f"基準查不到還送了進場單：{r}"
    if "XUSDT" in T().STATE["pending"]:
        return "沒送單卻留下待確認"


@case("q-9", "開倉前查帳戶權益查不到：不送進場單")
def _():
    ex, clock = fresh()
    ex.mark["XUSDT"] = 100.4
    k0 = arm(ex, lambda m, p, prm: p in ("/fapi/v2/account", "/fapi/v2/balance"))
    n0 = len(ex.calls)
    r = T().open_position("X", "LONG", 100.0, None, stop_pct=5)
    need(hit(ex, k0), "查帳戶沒有走到（結果前提）")
    if entry_sent(ex, n0) or r.get("ok"):
        return f"帳戶查不到還送了進場單：{r}"


@case("q-10", "開倉成交後等部位出現時逐幣查不到：記待認領、不掛停損到查不到的部位上、不回報成功")
def _():
    ex, clock = fresh()
    ex.mark["XUSDT"] = 100.4
    k0 = arm(ex, lambda m, p, prm: per_sym(m, p, prm) and entry_sent(ex, 0))
    r = T().open_position("X", "LONG", 100.0, None, stop_pct=5)
    need(entry_sent(ex, 0), "進場單沒有送出（前提）")
    need(hit(ex, k0), "成交後的逐幣查詢沒有走到（結果前提）")
    if r.get("ok"):
        return f"部位查不到卻回報開倉成功：{r}"
    if "XUSDT" not in T().STATE["pending"]:
        return "沒記成待認領（交易所上可能有部位、沒人接手）"


@case("q-11", "開倉的市價單回 NEW、查單又查不到：撤單後用部位增加量記帳並警告，停損照樣掛")
def _():
    ex, clock = fresh()
    ex.mark["XUSDT"] = 100.4
    ex.market_new["open"] = "now"
    k0 = arm(ex, lambda m, p, prm: p == "/fapi/v1/order")
    r = T().open_position("X", "LONG", 100.0, None, stop_pct=5)
    need(hit(ex, k0), "查單沒有走到（結果前提）")
    pos = T().STATE["positions"].get("XUSDT") or {}
    got = ex.pos.get(("XUSDT", "LONG"), [0])[0]
    need(got > 0, "交易所上應有部位（前提）")
    if abs(float(pos.get("qty") or 0) - got) > 1e-9:
        return f"帳上數量 {pos.get('qty')}，交易所 {got}"
    if not stops(ex):
        return "停損沒掛"
    if not any("確認不了" in str(w) for w in (r.get("warnings") or [])):
        return f"沒有警告成交狀態確認不了：{r.get('warnings')}"


@case("q-12", "平倉的市價單回 NEW、查單又查不到：再查部位——確認沒了才結帳")
def _():
    ex, clock = fresh()
    open_long(ex)
    ex.market_new["close"] = "now"
    k0 = arm(ex, lambda m, p, prm: p == "/fapi/v1/order")
    r = T().close_position("XUSDT")
    need(hit(ex, k0), "查單沒有走到（結果前提）")
    need(ex.pos.get(("XUSDT", "LONG"), [0])[0] <= 1e-9, "交易所上應已平掉（前提）")
    if not r.get("ok") or "XUSDT" in T().STATE["positions"]:
        return f"查單查不到、但部位確認沒了，應結帳：{r}"


@case("q-13", "平倉的市價單回 NEW、查單查不到、再查部位也查不到：不結帳、停損留著、記待平倉")
def _():
    ex, clock = fresh()
    open_long(ex)
    ex.market_new["close"] = "stuck"
    state = {"sent": False}

    def f(m, p, prm):
        if p == "/fapi/v1/order":
            state["sent"] = True
            return True
        return state["sent"] and per_sym(m, p, prm)
    k0 = arm(ex, f)
    r = T().close_position("XUSDT")
    need(hit(ex, k0), "查單沒有走到（結果前提）")
    if r.get("ok") or "XUSDT" not in T().STATE["positions"]:
        return f"什麼都確認不了卻結帳了：{r}"
    if not stops(ex):
        return "停損被撤了"


@case("q-14", "撤孤兒單前查部位查不到：不撤")
def _():
    ex, clock = fresh()
    ex.algo[4242] = {"symbol": "XUSDT", "side": "SELL", "type": "STOP_MARKET", "closePosition": "true"}
    need(4242 in ex.algo, "孤兒單沒放上去（前提）")
    k0 = arm(ex, per_sym)
    r = T().cancel_orphan("XUSDT", "4242")
    need(hit(ex, k0), "查部位沒有走到（結果前提）")
    if 4242 not in ex.algo or r.get("ok"):
        return f"查不到部位還撤了：{r}"


@case("q-14b", "對照：撤孤兒單前查得到部位、交易所上沒有部位 → 撤掉（查得到時照常運作）")
def _():
    ex, clock = fresh()
    ex.algo[4242] = {"symbol": "XUSDT", "side": "SELL", "type": "STOP_MARKET", "closePosition": "true"}
    need(4242 in ex.algo, "孤兒單沒放上去（前提）")
    r = T().cancel_orphan("XUSDT", "4242")
    if 4242 in ex.algo or not r.get("ok"):
        return f"查得到、沒有部位，孤兒單卻沒撤：{r}"


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✓' if not err else '✕'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
