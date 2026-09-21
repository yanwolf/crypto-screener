"""BINANCE_LESSONS r15 → r17 逐段差異的檢查項目（crypto-screener 從 r15 回來，含 r16、r17）。

每一項都有「前提」斷言（用法第 5 點 r17）：先確認要測的動作真的發生了（單真的送出、守衛真的查了、
對帳真的跑了），前提不成立就回報「前提不成立」，不會因為根本沒走到而空跑通過。

    python3 -m tests.test_r17
"""
import io
import os
import sys
import importlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, ROOT)
import trader as T                                              # noqa: E402
from tests.fake_exchange import Ex17, Clock, PROGRAM_ERRORS, Pre, need  # noqa: E402

RESULTS = []


def case(tag, desc, allow=()):
    """allow：這一項刻意注入、預期會出現在錯誤輸出的字串（其他程式錯誤照樣攔下）。"""
    def deco(fn):
        buf, old = io.StringIO(), sys.stderr
        sys.stderr = buf
        try:
            err = fn()
        except Pre as e:
            err = f"前提不成立：{e}"
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        finally:
            sys.stderr = old
        logged = [l for l in buf.getvalue().splitlines() if any(k in l for k in PROGRAM_ERRORS)
                  and not any(a in l for a in allow)]
        if not err and logged:
            err = f"程式錯誤被吞掉（第 14 條）：{logged[0][:120]}"
        RESULTS.append((tag, desc, err))
        return fn
    return deco



def fresh(mode="oneway"):
    importlib.reload(T)
    clock = Clock()
    T.time = clock
    T.save_state = lambda: None
    T.CFG.update({"key": "k", "secret": "s", "dryRun": False, "leverage": 3, "riskPct": 0.5,
                  "breakevenR": 1.0, "maxPositions": 8})
    for s in ("XUSDT", "YUSDT"):
        T._filters[s] = {"status": "TRADING", "tick": 0.01, "step": 0.1, "minQty": 0.1, "minNotional": 5}
    T._filters_ts = 9e18
    T._algo_supported[0] = True
    ex = Ex17(mode)
    T._request_raw = ex
    return ex, clock


def markets(ex, since=0, reduce=None):
    out = [c for c in ex.calls[since:] if c[1] == "/fapi/v1/order" and c[2].get("type") == "MARKET"]
    if reduce is not None:
        out = [c for c in out if bool(c[2].get("reduceOnly")) == reduce]
    return out


def open_long(ex, mark=100.4):
    ex.mark["XUSDT"] = mark
    n0 = len(ex.calls)
    r = T.open_position("X", "LONG", 100.0, None, stop_pct=5)
    need(markets(ex, n0, reduce=False), f"進場單沒有送出：{r.get('error')}")
    need("XUSDT" in T.STATE["positions"], f"進場後帳上沒有部位：{r.get('error')}")
    return r


# ════ 第 1 條 r16：查停損時 404 也要進入退回 ════════════════

@case("1-f", "服務重啟後查停損 Algo 回 404：也要進入退回、改看舊端點；舊端點停損不見要補掛")
def _():
    ex, clock = fresh()
    ex.algo_404, ex.legacy_accept = True, True
    open_long(ex)
    need(any(o.get("via") == "legacy" for o in T.STATE["positions"]["XUSDT"]["orders"]), "停損沒有掛在舊端點")
    T._algo_supported[0], T._algo_retry_at[0] = None, 0.0     # 重啟：退回旗標歸零
    for o in list(T.STATE["positions"]["XUSDT"]["orders"]):
        o["via"] = "algo"                                     # 最糟情況：帳上也沒記到舊端點
    ex.legacy.clear()                                         # 舊端點的停損不見了
    n0 = len(ex.calls)
    for _ in range(4):
        T.guard_positions()
    need(sum(1 for c in ex.calls[n0:] if c[1] == "/fapi/v1/openAlgoOrders") >= 4, "守衛沒有每輪都查 Algo")
    placed = [c for c in ex.calls[n0:] if c[1] == "/fapi/v1/order" and c[2].get("type") == "STOP_MARKET"]
    if not placed:
        return "Algo 查詢 404 沒有進入退回，守衛每輪都「查詢失敗、跳過」，舊端點停損不見了也沒補"


# ════ 第 2 條 r16、r17：逐幣查詢，空清單＝查不到 ═══════════

@case("2-c", "平倉被拒、確認查詢（帶 symbol）回空清單：不能判成已平倉、不能撤停損")
def _():
    ex, clock = fresh()
    open_long(ex)
    ex.reject_market.add("XUSDT")
    ex.symbol_empty_after_market = True                       # 修正：平倉被拒「之後」的確認查詢才回空清單
    n0 = len(ex.calls)
    r = T.close_position("XUSDT")
    need(markets(ex, n0, reduce=True), "平倉單沒有送出（沒走到「被拒後確認」那一步）")
    if r.get("ok") or "XUSDT" not in T.STATE["positions"]:
        return "逐幣查詢回空清單被當成「數量 0」，部位被記成已平倉"
    if any(c[0] == "DELETE" for c in ex.calls[n0:]):
        return "逐幣查詢回空清單就撤了停損"


@case("2-c2", "平倉前確認這一側：逐幣查詢回空清單時不能判成「那一側已不在」")
def _():
    ex, clock = fresh()
    open_long(ex)
    ex.symbol_empty = 1                                       # 只有平倉前那一次回空清單
    n0 = len(ex.calls)
    r = T.close_position("XUSDT")
    need(any(c[1] == "/fapi/v2/positionRisk" and c[2].get("symbol") for c in ex.calls[n0:]), "沒有做逐幣查詢")
    if "gone" in str(r) or "已經沒有自己的部位" in str(r.get("error", "")):
        return "逐幣查詢回空清單被當成「這一側已不在」"


@case("2-d", "pending 到期前確認交易所上沒有：要逐幣查，不能用全量表")
def _():
    ex, clock = fresh()
    T.STATE["pending"]["XUSDT"] = {"side": "LONG", "qty": 1.0, "stopPct": 5, "note": "", "base": 0.0,
                                   "params": {k: T.CFG.get(k) for k in T.PARAM_KEYS},
                                   "ts": int(clock.now * 1000)}
    ex.pos[("XUSDT", "LONG")] = [1.0, 100.2]                  # 其實成交了
    ex.full_missing = True                                    # 但全量表一直缺漏
    clock.now += 190
    n0 = len(ex.calls)
    T.sync_positions()
    need(any(c[1] == "/fapi/v2/positionRisk" for c in ex.calls[n0:]), "對帳沒有跑")
    if "XUSDT" not in T.STATE["pending"] and "XUSDT" not in T.STATE["positions"]:
        return "全量表缺漏就判定未成交，交易所上的部位沒人管"
    # 對照組：逐幣查詢正常時，全量表缺漏也要靠逐幣查到並認領
    need("XUSDT" in T.STATE["positions"], "對照組：逐幣查得到部位卻沒認領，上面的「沒丟」可能只是什麼都沒做")


@case("2-e", "撤孤兒單前確認沒有部位：逐幣查詢回空清單時不能撤")
def _():
    ex, clock = fresh()
    ex.algo[5001] = {"symbol": "XUSDT", "type": "STOP_MARKET", "side": "SELL"}
    ex.symbol_empty = 1
    n0 = len(ex.calls)
    r = T.cancel_orphan("XUSDT", "5001")
    need(any(c[1] == "/fapi/v2/positionRisk" for c in ex.calls[n0:]), "沒有查部位")
    if r.get("ok") or any(c[0] == "DELETE" for c in ex.calls[n0:]):
        return "查部位回空清單被當成「沒有部位」就撤了"
    # 對照組：查部位正常、確實沒有部位時，同一張孤兒單要撤得掉
    m0 = len(ex.calls)
    r2 = T.cancel_orphan("XUSDT", "5001")
    need(r2.get("ok") and any(c[0] == "DELETE" for c in ex.calls[m0:]),
         f"對照組：確實沒有部位時也撤不掉（{r2}），上面的「沒撤」可能是別的原因")


# ════ 第 3 條 r16：基準查不到不送、基準走完生命週期 ═════════

@case("3-f", "送單前基準查不到（逐幣回空清單）：不送單、不留 pending")
def _():
    ex, clock = fresh()
    ex.mark["XUSDT"] = 100.4
    ex.symbol_empty = 1
    n0 = len(ex.calls)
    r = T.open_position("X", "LONG", 100.0, None, stop_pct=5)
    need(any(c[1] == "/fapi/v2/positionRisk" and c[2].get("symbol") for c in ex.calls[n0:]), "沒有查基準")
    if markets(ex, n0, reduce=False):
        return "基準查不到仍送出進場單"
    if "XUSDT" in T.STATE["pending"]:
        return "沒送單卻留下 pending"
    if r.get("ok"):
        return "沒送單卻回報成功"
    # 對照組：基準查得到時，同樣的呼叫確實會送單
    m0 = len(ex.calls)
    r2 = T.open_position("X", "LONG", 100.0, None, stop_pct=5)
    need(markets(ex, m0, reduce=False), f"對照組：基準查得到時也沒送單（{r2.get('error')}），上面的「沒送」可能是別的原因")


@case("3-i", "有基準時認領：用基準均價反推這張單的成交價，並講明是估計值")
def _():
    ex, clock = fresh()
    T.STATE["pending"]["XUSDT"] = {"side": "LONG", "qty": 1.0, "stopPct": 5, "note": "", "base": 2.0,
                                   "baseEntry": 90.0, "params": {k: T.CFG.get(k) for k in T.PARAM_KEYS},
                                   "ts": int(clock.now * 1000)}
    ex.pos[("XUSDT", "LONG")] = [3.0, (2.0 * 90.0 + 1.0 * 100.2) / 3.0]
    clock.now += 40
    T.drain_alerts()
    T.sync_positions()
    p = T.STATE["positions"].get("XUSDT")
    need(p is not None, "沒有認領")
    if abs(p["entry"] - 100.2) > 1e-6:
        return f"認領的進場價 {p['entry']:g} 是合併均價，應反推為 100.2"
    text = " ".join(p.get("warnings") or []) + " ".join(a["text"] for a in T.drain_alerts())
    if "估計" not in text:
        return "沒有講明進場價是估計值"


@case("3-j", "有基準時的即時損益：只算自己的，不含同側別人的")
def _():
    ex, clock = fresh()
    ex.pos[("XUSDT", "LONG")] = [2.0, 90.0]
    r = open_long(ex, mark=100.0)
    mine, entry = r["qty"], r["entry"]
    ex.mark["XUSDT"] = 110.0
    lp = T.live_positions(max_age=0).get("XUSDT")
    need(lp is not None, "即時部位查不到")
    want = (110.0 - entry) * mine
    if abs((lp.get("pnl") or 0) - want) > 1e-6:
        return f"即時損益 {lp.get('pnl')}，應只算自己的 {want:.2f}"


# ════ 第 8 條 r16、r17：守衛補掛 -2021 出場；守衛每個部位都跑到 ═════

def _missing_stop(ex, clock, trigger_2021=True):
    open_long(ex)
    for o in list(ex.algo):
        if ex.algo[o].get("type") == "STOP_MARKET":
            ex.algo.pop(o)
    if trigger_2021:
        ex.reject_algo = lambda params: ((400, {"code": -2021, "msg": "Order would immediately trigger."})
                                         if params.get("type") == "STOP_MARKET" else None)


@case("8-i", "守衛補掛遇 -2021：直接走平倉流程（不管 guardClose 設定）")
def _():
    ex, clock = fresh()
    _missing_stop(ex, clock)
    T.CFG["guardClose"] = False
    n0 = len(ex.calls)
    for _ in range(4):
        T.guard_positions()
    need(any(c[1] == "/fapi/v1/algoOrder" and c[2].get("type") == "STOP_MARKET" for c in ex.calls[n0:]),
         "守衛沒有走到補掛那一步")
    if not markets(ex, n0, reduce=True):
        return "補掛遇 -2021 只告警、沒有送平倉單"
    if "XUSDT" in T.STATE["positions"]:
        return "平倉成功卻沒結帳"


@case("8-i2", "守衛補掛遇 -2021、平倉又被拒：記待平倉，不能記成已平倉")
def _():
    ex, clock = fresh()
    _missing_stop(ex, clock)
    ex.reject_market.add("XUSDT")
    n0 = len(ex.calls)
    for _ in range(4):
        T.guard_positions()
    need(markets(ex, n0, reduce=True), "沒有送出平倉單")
    p = T.STATE["positions"].get("XUSDT")
    if not p or not p.get("pendingClose"):
        return "平倉被拒後沒有記待平倉"


@case("8-j", "守衛：某個部位丟例外，其他部位照樣檢查；出錯的部位要告警", allow=("BADUSDT",))
def _():
    ex, clock = fresh()
    open_long(ex)
    good = T.STATE["positions"].pop("XUSDT")
    T.STATE["positions"]["BADUSDT"] = {"symbol": "BADUSDT", "side": "LONG"}   # 缺欄位，檢查時會丟例外
    T.STATE["positions"]["XUSDT"] = good                      # 修正：出問題的排在前面，否則測不到
    for o in list(ex.algo):
        if ex.algo[o].get("type") == "STOP_MARKET":
            ex.algo.pop(o)
    n0 = len(ex.calls)
    for _ in range(4):
        try:
            T.guard_positions()
        except Exception:
            pass
    need(any(c[1] == "/fapi/v1/openAlgoOrders" and c[2].get("symbol") == "XUSDT" for c in ex.calls[n0:]),
         "守衛完全沒查到 XUSDT")
    if not any(c[1] == "/fapi/v1/algoOrder" and c[2].get("type") == "STOP_MARKET" for c in ex.calls[n0:]):
        return "一個部位丟例外，其他部位的停損就沒補掛"
    if not any("BADUSDT" in a["text"] and "出錯" in a["title"] for a in T.drain_alerts()):
        return "出錯的部位沒有告警（錯誤被守衛的 try 吞掉，第 14 條）"


@case("8-k", "背景迴圈：對帳或移損丟例外，這一輪的守衛與告警照樣執行")
def _():
    ex, clock = fresh()
    import main as M
    importlib.reload(M)
    M.trader = T
    pushed = []
    M.push_all = lambda title, text: pushed.append(title)
    open_long(ex)
    for o in list(ex.algo):
        if ex.algo[o].get("type") == "STOP_MARKET":
            ex.algo.pop(o)
    T.manage_positions = lambda: (_ for _ in ()).throw(RuntimeError("移損模組壞了"))
    rnd = getattr(M, "position_round", None)
    need(rnd is not None, "沒有可單獨執行一輪的 position_round（迴圈寫死在無窮迴圈裡，無法驗證）")
    n0 = len(ex.calls)
    for _ in range(4):
        rnd(20)
    if not any(c[1] == "/fapi/v1/algoOrder" and c[2].get("type") == "STOP_MARKET" for c in ex.calls[n0:]):
        return "移損丟例外，守衛整輪被跳過，停損不見了沒補"
    if not any("停損" in t for t in pushed):
        return f"告警沒有送出：{pushed}"


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✕' if err else '✓'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
