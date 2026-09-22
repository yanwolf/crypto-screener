"""BINANCE_LESSONS r43 → r46 逐段差異的檢查項目（crypto-screener）。

    python3 -m tests.test_r46
"""
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, ROOT)
from tests import test_r42 as R                                 # noqa: E402  共用 fresh()／open_long() 等
from tests.fake_exchange import need                            # noqa: E402
from tests.harness import make_case                             # noqa: E402

RESULTS = []
case = make_case(RESULTS)
fresh, open_long, market_posts, stops_on_exchange, main_mod = \
    R.fresh, R.open_long, R.market_posts, R.stops_on_exchange, R.main_mod


def T():
    return R.T


def deletes(ex, since):
    return [prm for m, p, prm in ex.calls[since:] if m == "DELETE" and p == "/fapi/v1/order"]


def post(M, path, raw):
    """直接呼叫 Handler.do_POST（不開伺服器），回傳 (HTTP 狀態, 內容)。"""
    h = M.Handler.__new__(M.Handler)
    h.headers = {"Content-Length": str(len(raw))}
    h.rfile = io.BytesIO(raw)
    h.path = path
    out = []
    h.send_json = lambda code, body, **k: out.append((code, body))
    h.do_POST()
    need(out, "do_POST 沒有回應（前提）")
    return out[-1]


# ════ 第 15 條：pump-dump-hunter 的「成交 40% 後卡住」 ═════════════

@case("15-9", "平倉成交 40% 後卡在 PARTIALLY_FILLED：撤單、不結帳、停損留著；重試平掉剩下的，出場價含兩段", allow=("平倉失敗",))
def _():
    ex, clock = fresh()
    r0 = open_long(ex)
    q0 = r0["qty"]
    ex.market_new["close"] = "partial_stuck"
    ex.mark["XUSDT"] = 100.0
    n0 = len(ex.calls)
    r = T().close_position("XUSDT")
    need(market_posts(ex, n0, close=True), "平倉單沒有送出（前提）")
    left = ex.pos.get(("XUSDT", "LONG"), [0])[0]
    need(0 < left < q0, f"模擬交易所沒有部分成交（前提）：剩 {left}")
    if r.get("ok") or "XUSDT" not in T().STATE["positions"]:
        return "只成交 40% 就結帳了"
    if not stops_on_exchange(ex):
        return "只成交 40% 就把停損撤了"
    if not deletes(ex, n0):
        return "卡在 PARTIALLY_FILLED 的平倉單沒有撤"
    ex.market_new.pop("close")
    ex.mark["XUSDT"] = 104.0
    T().retry_pending_closes()
    need(ex.pos.get(("XUSDT", "LONG"), [0])[0] <= 1e-9, "重試後交易所上還有部位（前提）")
    sells = [f for f in ex.fills if f["side"] == "SELL"]
    need(len(sells) >= 2, f"平倉成交應至少兩筆（前提），實際 {len(sells)}")
    if not T().STATE["trades"]:
        return "剩下的平掉了卻沒有結帳"
    want = sum(float(f["qty"]) * float(f["price"]) for f in sells) / sum(float(f["qty"]) for f in sells)
    t = T().STATE["trades"][-1]
    if t.get("exit") is None or abs(float(t["exit"]) - want) > 1e-6:
        return f"出場價 {t.get('exit')}，應為各段加權 {want:.6f}"


@case("15-10", "開倉成交 40% 後卡住：撤單，帳上數量＝成交的 40%，停損照樣掛上")
def _():
    ex, clock = fresh()
    ex.mark["XUSDT"] = 100.4
    ex.market_new["open"] = "partial_stuck"
    n0 = len(ex.calls)
    T().open_position("X", "LONG", 100.0, None, stop_pct=5)
    sent = market_posts(ex, n0, close=False)
    need(sent, "進場單沒有送出（前提）")
    got = ex.pos.get(("XUSDT", "LONG"), [0])[0]
    need(0 < got < float(sent[0]["quantity"]), f"模擬交易所沒有部分成交（前提）：{got}")
    if not deletes(ex, n0):
        return "卡在 PARTIALLY_FILLED 的進場單沒有撤（之後才成交的數量沒人知道）"
    pos = T().STATE["positions"].get("XUSDT") or {}
    if abs(float(pos.get("qty") or 0) - got) > 1e-9:
        return f"帳上數量 {pos.get('qty')}，實際成交 {got}"
    if not stops_on_exchange(ex):
        return "停損沒有掛上"


# ════ 第 8 條 r44、r45：格式錯的輸入、空的輸入、讀不到當成沒有 ═══════

@case("8-ba", "請求內容解析得了但不是物件（[]、null）：回 400、什麼都不改（以前處理函式 .get() 丟例外）")
def _():
    ex, clock = fresh()
    M = main_mod()
    M.push_all = lambda *a, **k: None
    T().CFG["riskPct"] = 0.5
    codes = [code for code, _body in (post(M, "/api/trade/config", raw) for raw in (b"[]", b"null", b'"x"'))]
    code_bad, _b = post(M, "/api/trade/config", b"{riskPct:")
    need(code_bad == 400, f"格式錯的 JSON 應回 400（本來就有，前提）：{code_bad}")
    if codes != [400, 400, 400]:
        return f"不是物件的請求內容回 {codes}（應都是 400）"
    if T().CFG["riskPct"] != 0.5:
        return "設定被改了"


@case("8-bb", "設定端點收到空的內容（沒有要改的欄位）：回錯誤，不能回成功")
def _():
    ex, clock = fresh()
    M = main_mod()
    c1, _ = M.trade_handle_safe("/api/trade/config", {})
    c2, _ = M.trade_handle_safe("/api/trade/auto", {"adminKey": "k"})
    try:
        c3, _ = M.mon_handle("/api/monitor/config", {})
    except Exception as e:
        c3 = f"例外 {type(e).__name__}"
    if [c1, c2, c3] != [400, 400, 400]:
        return f"空的設定請求回 {[c1, c2, c3]}（應都是 400；回 200 等於什麼都沒改卻顯示成功）"


@case("8-bc", "切到模擬網時讀不到正式網帳本：要推播講明（讀不到不等於沒有持倉）")
def _():
    ex, clock = fresh()
    M = main_mod()
    with open(os.path.join(M.CACHE_DIR, "trader.live.json"), "w", encoding="utf-8") as f:
        f.write('{"state": {"positions": {"XUSDT"')
    pushed = []
    M.push_all = lambda t, x: pushed.append(t)
    fn = getattr(M, "warn_live_ledger", None)
    if fn is None:
        return "沒有可以單獨檢查的正式網帳本檢查（還埋在 main() 裡，讀不到時 except: pass）"
    buf, old = io.StringIO(), sys.stderr
    sys.stderr = buf
    try:
        fn()
    finally:
        sys.stderr = old
    if not [t for t in pushed if "正式網帳本" in t]:
        return "讀不到正式網帳本時沒有推播"


@case("8-bd", "正式網帳本讀得到、裡面有持倉：照樣推播（改寫成函式後原本的行為不能掉）")
def _():
    ex, clock = fresh()
    M = main_mod()
    with open(os.path.join(M.CACHE_DIR, "trader.live.json"), "w", encoding="utf-8") as f:
        f.write('{"state": {"positions": {"XUSDT": {"side": "LONG"}}}}')
    pushed = []
    M.push_all = lambda t, x: pushed.append(t)
    fn = getattr(M, "warn_live_ledger", None)
    if fn is None:
        return "沒有 warn_live_ledger"
    fn()
    if not [t for t in pushed if "正式網仍有持倉" in t]:
        return "正式網帳本有持倉卻沒有推播"


# ════ 用法第 5 點 r44、r45：框架的重設要自動、而且驗證得到 ═══════════

@case("u-4", "每個情境的重設：trader、main 所有底線開頭的模組層級 dict／list／set 塞探針後，fresh() 都要還原")
def _():
    ex, clock = fresh()
    M = main_mod()
    probed = []
    for mod in (T(), M):
        for k, v in list(vars(mod).items()):
            if k.startswith("_") and not k.startswith("__") and isinstance(v, (dict, list, set)):
                if isinstance(v, dict):
                    v["__probe__"] = 1
                elif isinstance(v, list):
                    v.append("__probe__")
                else:
                    v.add("__probe__")
                probed.append((mod.__name__, k))
    need(len(probed) >= 5, f"探針放得太少（前提）：{probed}")
    fresh()
    M = main_mod()
    left = []
    for mod in (T(), M):
        for k, v in vars(mod).items():
            if k.startswith("_") and isinstance(v, (dict, list, set)) and "__probe__" in v:
                left.append(f"{mod.__name__}.{k}")
    if left:
        return f"重設後探針還在：{left}"


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✓' if not err else '✕'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
