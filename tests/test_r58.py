"""BINANCE_LESSONS r56 → r58 逐段差異的檢查項目（crypto-screener）。

    python3 -m tests.test_r58
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
fresh, open_long, market_posts = R.fresh, R.open_long, R.market_posts


def T():
    return R.T


def preflight_item(name):
    import importlib
    import preflight
    importlib.reload(preflight)
    res = {r["item"]: r for r in preflight.check()}
    need(res, "自檢沒有結果（前提）")
    return res.get(name)


# ════ 第 8 條 r57、r58：手動平倉遇到「交易所端已平掉」 ═══════════════

@case("r58-1", "交易所端已平掉、別人重開（兩個證據成立）：手動平倉不送單、不回報已平倉，回應講明這次沒有送單")
def _():
    ex, clock = fresh()
    open_long(ex)
    R55.reopen(ex)
    need(R55.other_qty(ex) == R55.OTHER, "別人重開的部位沒放上去（前提）")
    n0 = len(ex.calls)
    r = T().close_position("XUSDT")
    if market_posts(ex, n0, close=True):
        return "送了平倉單（會平到別人的部位）"
    if r.get("ok"):
        return f"回報已平倉，實際沒有送單：{r}"
    if "不送平倉單" not in str(r.get("error")):
        return f"回應沒講明這次沒有送單：{r}"


@case("r58-2", "交易所端已平掉、沒有重開：手動平倉同樣不送單、講明沒送，交給對帳照成交明細結帳")
def _():
    ex, clock = fresh()
    open_long(ex)
    q = T().STATE["positions"]["XUSDT"]["qty"]
    ex.trigger("XUSDT", "LONG", q, 95.0)
    need(R55.other_qty(ex) == 0, "交易所上應已沒有部位（前提）")
    n0 = len(ex.calls)
    r = T().close_position("XUSDT")
    if market_posts(ex, n0, close=True) or r.get("ok"):
        return f"交易所上已經沒有部位，手動平倉卻送單或回報已平倉：{r}"
    if "不送平倉單" not in str(r.get("error")):
        return f"回應沒講明這次沒有送單：{r}"
    T().sync_positions()
    if not T().STATE["trades"] or abs(float(T().STATE["trades"][-1].get("exit") or 0) - 95.0) > 1e-9:
        return "對帳沒有照成交明細結帳（出場價應為 95）"


# ════ pump-dump-hunter r57、gold-scalper r58：自檢「帳上成交價 vs 交易所均價」 ═════

@case("r57-1", "自檢：均價有出入但沒有平倉成交 → 列出來、說明「同一筆，照常管理」")
def _():
    ex, clock = fresh()
    open_long(ex)
    T().STATE["positions"]["XUSDT"]["entry"] = 100.0
    item = preflight_item("帳上成交價與交易所均價")
    if not item or item.get("status") != "warn" or "同一筆" not in " ".join(item.get("rows") or [item.get("msg", "")]):
        return f"自檢沒列出這筆、或沒說明會怎麼處理：{item}"


@case("r57-2", "自檢：交易所端重開（兩個證據成立）→ 列出來、說明下一輪對帳會結帳、不動別人的部位")
def _():
    ex, clock = fresh()
    open_long(ex)
    R55.reopen(ex)
    need(R55.other_qty(ex) == R55.OTHER, "別人重開的部位沒放上去（前提）")
    item = preflight_item("帳上成交價與交易所均價")
    if not item or item.get("status") != "warn" or "別人重開" not in " ".join(item.get("rows") or [item.get("msg", "")]):
        return f"自檢沒列出重開的這筆：{item}"
    if "XUSDT" not in T().STATE["positions"]:
        return "自檢動了帳本（自檢只能讀）"


@case("r57-3", "自檢：帳上成交價都跟交易所一致 → 通過")
def _():
    ex, clock = fresh()
    open_long(ex)
    item = preflight_item("帳上成交價與交易所均價")
    if not item or item.get("status") != "ok":
        return f"一致時應通過：{item}"


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✓' if not err else '✕'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
