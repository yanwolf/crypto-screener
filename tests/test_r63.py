"""BINANCE_LESSONS r61 → r63 逐段差異的檢查項目（crypto-screener）。

    python3 -m tests.test_r63
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, ROOT)
from tests import test_r42 as R                                 # noqa: E402
from tests import test_r55 as R55                               # noqa: E402
from tests.fake_exchange import need                            # noqa: E402
from tests.harness import make_case                             # noqa: E402

RESULTS = []
case = make_case(RESULTS)
fresh, open_long, market_posts, main_mod = R.fresh, R.open_long, R.market_posts, R.main_mod


def T():
    return R.T


def close_text(M):
    """對帳結完帳後，用最後一筆交易組平倉通知；回傳各行。"""
    need(T().STATE["trades"], "沒有平倉紀錄（前提）")
    head, text = M.notify_trade_close(T().STATE["trades"][-1])
    return [head] + text.split("\n")


# ════ 第 8 條 r62：按平倉時部位還在、送單前確認那一刻停損剛觸發 ═══════

@case("r62-1", "按平倉時帳上還在、送單前確認那一刻停損剛觸發：不送單、回應寫明「送單前確認…可能是停損觸發」，出場價交給對帳查")
def _():
    ex, clock = fresh()
    open_long(ex)
    q = T().STATE["positions"]["XUSDT"]["qty"]
    state = {"done": False}

    def before(m, p, prm):
        if not state["done"] and p == "/fapi/v2/positionRisk" and prm.get("symbol") == "XUSDT":
            state["done"] = True
            ex.trigger("XUSDT", "LONG", q, 95.0)                # 查詢那一刻停損觸發
    ex.before = before
    need(R55.other_qty(ex) > 0, "按平倉時交易所上部位還在（前提，動作之前記下）")
    n0 = len(ex.calls)
    r = T().close_position("XUSDT")
    need(state["done"], "送單前確認沒有走到（結果前提）")
    if market_posts(ex, n0, close=True) or r.get("ok"):
        return f"停損剛觸發還送單／回報已平倉：{r}"
    err = str(r.get("error"))
    if "送單前確認" not in err or "停損" not in err:
        return f"回應沒照實寫「送單前確認：…可能是停損觸發」：{err}"
    if "查到" in err:
        return f"這種情況沒查成交明細，回應卻寫「查到」：{err}"
    ex.before = None
    T().sync_positions()
    t = T().STATE["trades"][-1] if T().STATE["trades"] else {}
    if t.get("exit") is None or abs(float(t["exit"]) - 95.0) > 1e-9:
        return f"對帳沒照成交明細結帳（出場價 {t.get('exit')}，應為 95）"


@case("r62-2", "待平倉重試時送單前確認那一刻停損剛觸發：不送單、不自己結帳、留給對帳照成交明細結帳")
def _():
    ex, clock = fresh()
    open_long(ex)
    q = T().STATE["positions"]["XUSDT"]["qty"]
    T()._set_pending_close(T().STATE["positions"]["XUSDT"], "測試", "測試")
    state = {"done": False}

    def before(m, p, prm):
        if not state["done"] and p == "/fapi/v2/positionRisk" and prm.get("symbol") == "XUSDT":
            state["done"] = True
            ex.trigger("XUSDT", "LONG", q, 95.0)
    ex.before = before
    need(R55.other_qty(ex) > 0, "重試前交易所上部位還在（前提，動作之前記下）")
    n0 = len(ex.calls)
    T().retry_pending_closes()
    need(state["done"], "送單前確認沒有走到（結果前提）")
    if market_posts(ex, n0, close=True):
        return "停損剛觸發還送了平倉單"
    if T().STATE["trades"]:
        return "重試路徑自己結帳了（出場價沒查成交明細）"
    ex.before = None
    T().sync_positions()
    t = T().STATE["trades"][-1] if T().STATE["trades"] else {}
    if t.get("exit") is None or abs(float(t["exit"]) - 95.0) > 1e-9:
        return f"對帳沒照成交明細結帳（出場價 {t.get('exit')}，應為 95）"


# ════ 第 8 條 r63：通知裡的說明要在自己的欄位 ═══════════════════════

@case("r63-1", "被別人重開、對帳結帳的平倉通知：「別的部位、沒有動它」在「原因」那一行，不在風控（⚠）那一行、不寫「暫停真實下單」")
def _():
    ex, clock = fresh()
    open_long(ex)
    R55.reopen(ex)
    need(R55.other_qty(ex) == R55.OTHER, "別人重開的部位沒放上去（前提）")
    T().sync_positions()
    M = main_mod()
    lines = close_text(M)
    reason = [l for l in lines if l.startswith("原因")]
    if not reason or "別的部位" not in reason[0]:
        return f"原因那一行沒寫明交易所上那張是別的部位：{reason}"
    if any(("暫停真實下單" in l or "僅記錄模擬" in l) for l in lines):
        return "通知借用了風控那一欄，印成「已暫停真實下單」"
    if any(l.startswith("⚠") and "別的部位" in l for l in lines):
        return "「別的部位」放進了風控警告那一行"


@case("r63-2", "出場價查不到的平倉通知：損益那一行寫未知，原因那一行寫記未知，不寫成推估的數字")
def _():
    ex, clock = fresh()
    open_long(ex)
    q = T().STATE["positions"]["XUSDT"]["qty"]
    ex.trigger("XUSDT", "LONG", q, 95.0)
    ex.trades_fail = True
    T().sync_positions()
    M = main_mod()
    lines = close_text(M)
    pnl = [l for l in lines if l.startswith("損益")]
    reason = [l for l in lines if l.startswith("原因")]
    if not pnl or "未知" not in pnl[0]:
        return f"損益那一行沒寫未知：{pnl}"
    if not reason or "記未知" not in reason[0]:
        return f"原因那一行沒寫出場價記未知：{reason}"
    if not lines or "損益未知" not in lines[0]:
        return f"標題沒標損益未知：{lines[0]}"


@case("r63-3", "正常停損出場的平倉通知（對照）：原因只寫交易所出場，沒有「別的部位」也沒有「記未知」")
def _():
    ex, clock = fresh()
    open_long(ex)
    q = T().STATE["positions"]["XUSDT"]["qty"]
    ex.trigger("XUSDT", "LONG", q, 95.0)
    T().sync_positions()
    M = main_mod()
    lines = close_text(M)
    reason = [l for l in lines if l.startswith("原因")]
    if not reason or "別的部位" in reason[0] or "記未知" in reason[0]:
        return f"正常出場的原因多寫了東西：{reason}"


# ════ 本金上限（2026-09-23：模擬網把 3873 U 當 1500 U 跑，虧慢一點、少重置）═══

@case("cap-1", "設了本金上限：階梯、可動用保證金、單筆風險都以上限為基準；設 0 回到實際餘額的階梯；設定可存可載")
def _():
    ex, clock = fresh()
    need(hasattr(T(), "tier_capital") and "capitalCap" in T().CFG, "程式沒有本金上限（前提：2026-09-23 起才有）")
    T().CFG["capitalCap"] = 0
    need(T().tier_capital(3873) == 3000, "沒設上限時 3873 U 應落在 3000 階梯（前提）")
    T().CFG["capitalCap"] = 1500
    if T().tier_capital(3873) != 1500:
        return f"設 1500 上限，階梯應為 1500，實際 {T().tier_capital(3873)}"
    T().CFG["capitalCap"] = 500                                 # 模擬交易所錢包 1000 U：上限 500 要壓得下去
    cs, err = T().capital_state()
    need(cs is not None and cs["wallet"] == 1000, f"取不到本金狀態或錢包不是 1000（前提）：{cs}、{err}")
    if cs["tier"] != 500 or abs(cs["usable"] - 375) > 1e-6:
        return f"可動用應為 500 × 75% = 375，實際階梯 {cs['tier']}、可動用 {cs['usable']}"
    T().CFG["capitalCap"] = 1500
    q, info = T().size_position(3873, 100.0, 95.0, {"step": 0.1})
    if info.get("tier") != 1500 or abs(info.get("riskAmt") - 1500 * T().CFG["riskPct"] / 100) > 1e-6:
        return f"單筆風險基準應為 1500 U，實際 {info.get('tier')}／{info.get('riskAmt')}"
    T().CFG["useTier"] = False
    q2, info2 = T().size_position(3873, 100.0, 95.0, {"step": 0.1})
    T().CFG["useTier"] = True
    if abs(info2.get("riskAmt") - 1500 * T().CFG["riskPct"] / 100) > 1e-6:
        return f"關掉階梯時本金上限沒生效：風險基準 {info2.get('riskAmt')}"
    if "capitalCap" not in T().PERSIST_CFG:
        return "本金上限沒有存進狀態檔（重啟就不見）"


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✓' if not err else '✕'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
