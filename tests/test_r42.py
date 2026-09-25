"""BINANCE_LESSONS r39 → r42 逐段差異的檢查項目（crypto-screener 從 r39 回來，含 r40、r41、r42 第 15 條）。

第 15 條（市價單「收下 ≠ 成交」）沒辦法向交易所自檢（要真的送單），這裡用模擬交易所的
status: NEW 回應跑開倉與平倉（tests/fake_exchange.py 的 Ex42）。
新介面（NOTIFY_ERR、CONF_ERR、自檢新項目）一律用 getattr 取、或最後才檢查，
在舊版程式上是斷言失敗，不是測試崩掉（用法第 5 點 r38、r40）。

    python3 -m tests.test_r42
"""
import io
import os
import sys
import json
import tempfile
import importlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, ROOT)
import trader as T                                              # noqa: E402
from tests.fake_exchange import Ex42, Clock, need, entry_sent   # noqa: E402

RESULTS = []
BACKFILLS = []                                                  # fresh() 收下的背景補登工作（r71）
from tests.harness import make_case                             # noqa: E402
case = make_case(RESULTS)


def fresh(mode="oneway", keep_save=False, ex=None):
    importlib.reload(T)
    clock = Clock()
    T.time = clock
    BACKFILLS[:] = []
    T._start_backfill_thread = lambda fn: BACKFILLS.append(fn)     # r71：背景補登不真的開執行緒，收下來由測試自己跑
    if not keep_save:
        T.save_state = lambda: None
    T.CFG.update({"key": "k", "secret": "s", "dryRun": False, "leverage": 3, "riskPct": 0.5,
                  "breakevenR": 1.0, "maxPositions": 8})
    T._filters["XUSDT"] = {"status": "TRADING", "tick": 0.01, "step": 0.1, "minQty": 0.1, "minNotional": 5}
    T._filters_ts = 9e18
    T._algo_supported[0] = True
    ex = ex or Ex42(mode)
    T._request_raw = ex
    return ex, clock


def open_long(ex, mark=100.4):
    ex.mark["XUSDT"] = mark
    n0 = len(ex.calls)
    r = T.open_position("X", "LONG", 100.0, None, stop_pct=5)
    need(entry_sent(ex, n0), f"進場單沒有送出：{r.get('error')}")
    need("XUSDT" in T.STATE["positions"], f"進場後帳上沒有部位（前提）：{r.get('error')}")
    return r


def market_posts(ex, since=0, close=None):
    out = []
    for m, p, prm in ex.calls[since:]:
        if m == "POST" and p == "/fapi/v1/order" and prm.get("type") == "MARKET":
            if close is None or Ex42.is_close(prm) == close:
                out.append(prm)
    return out


def stops_on_exchange(ex):
    return [v for v in ex.algo.values() if (v.get("type") or v.get("orderType")) == "STOP_MARKET"]


def corrupt_file():
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"state": {"positions": {"XUSDT": {"side": "LONG"')
    try:
        json.load(open(path, encoding="utf-8"))
        ok = True
    except ValueError:
        ok = False
    need(not ok, "狀態檔其實解析得了（前提：要測的是壞檔）")
    return path


def main_mod():
    import main as M
    importlib.reload(M)
    M.trader = T
    M.CACHE_DIR = tempfile.mkdtemp()
    for k in list(M.NOTIFY):
        M.NOTIFY[k] = 587 if k == "smtp_port" else ""
    M._tg.update({"token": "", "chats": []})
    return M


# ════ 第 15 條（r42）：市價單「收下 ≠ 成交」 ═══════════════════

@case("15-1", "開倉與平倉的市價單都帶 newOrderRespType=RESULT")
def _():
    ex, clock = fresh()
    n0 = len(ex.calls)
    open_long(ex)
    need(market_posts(ex, n0, close=False), "沒有進場單（前提）")
    if any(p.get("newOrderRespType") != "RESULT" for p in market_posts(ex, n0, close=False)):
        return "進場單沒帶 newOrderRespType=RESULT"
    n1 = len(ex.calls)
    T.close_position("XUSDT")
    closes = market_posts(ex, n1, close=True)
    need(closes, "平倉單沒有送出（前提）")
    if any(p.get("newOrderRespType") != "RESULT" for p in closes):
        return "平倉單沒帶 newOrderRespType=RESULT（預設 ACK：status NEW、成交 0）"


@case("15-3a", "平倉單回 NEW、最後沒成交（EXPIRED 0）：不能結帳、不能撤停損，要記待平倉（最危險的一點）",
      allow=("平倉失敗",))
def _():
    ex, clock = fresh()
    open_long(ex)
    need(stops_on_exchange(ex), "開倉後交易所上沒有停損（前提）")
    ex.market_new["close"] = "never"
    n0 = len(ex.calls)
    r = T.close_position("XUSDT")
    need(market_posts(ex, n0, close=True), "平倉單沒有送出（前提）")
    need(ex.pos.get(("XUSDT", "LONG"), [0])[0] > 0, "交易所上的部位不在了（前提：這張平倉單沒成交）")
    if r.get("ok"):
        return f"交易所只是收下、沒有成交，卻回報平倉成功：{r}"
    if not stops_on_exchange(ex):
        return "停損被撤了：交易所上部位還在，停損卻沒了（裸倉）"
    if not (T.STATE["positions"].get("XUSDT") or {}).get("pendingClose"):
        return "沒有記成待平倉（之後沒人重試）"


@case("15-3b", "平倉單回 NEW、之後才成交：用單號查到 FILLED 才結帳，出場價是實際成交價")
def _():
    ex, clock = fresh()
    open_long(ex)
    ex.market_new["close"] = "later"
    n0 = len(ex.calls)
    T.close_position("XUSDT")
    need(market_posts(ex, n0, close=True), "平倉單沒有送出（前提）")
    fills = [f for f in ex.fills if f["side"] == "SELL"]
    need(fills, "模擬交易所沒有平倉成交（前提）")
    if "XUSDT" in T.STATE["positions"]:
        return "查到 FILLED 了卻沒有結帳"
    if not T.STATE["trades"]:
        return "沒有平倉紀錄"
    t = T.STATE["trades"][-1]
    if t.get("exit") is None or abs(float(t["exit"]) - float(fills[-1]["price"])) > 1e-9:
        return f"出場價 {t.get('exit')}，應為實際成交價 {fills[-1]['price']}"


@case("15-3c", "平倉只成交一半（EXPIRED 部分）：不結帳、停損留著；重試平掉剩下的，出場價含兩段成交", allow=("平倉失敗",))
def _():
    ex, clock = fresh()
    r0 = open_long(ex)
    q0 = r0["qty"]
    ex.market_new["close"] = "partial"
    ex.mark["XUSDT"] = 100.0
    r = T.close_position("XUSDT")
    left = ex.pos.get(("XUSDT", "LONG"), [0])[0]
    need(0 < left < q0, f"模擬交易所沒有部分成交（前提）：剩 {left}")
    if r.get("ok") or "XUSDT" not in T.STATE["positions"]:
        return "只成交一半就結帳了"
    if not stops_on_exchange(ex):
        return "只成交一半就把停損撤了"
    ex.market_new.pop("close")
    ex.mark["XUSDT"] = 104.0
    T.retry_pending_closes()
    need(ex.pos.get(("XUSDT", "LONG"), [0])[0] <= 1e-9, "重試後交易所上還有部位（前提）")
    if "XUSDT" in T.STATE["positions"]:
        return "剩下的平掉了卻沒有結帳"
    sells = [f for f in ex.fills if f["side"] == "SELL"]
    need(len(sells) >= 2, f"平倉成交應至少兩筆（前提），實際 {len(sells)}")
    want = sum(float(f["qty"]) * float(f["price"]) for f in sells) / sum(float(f["qty"]) for f in sells)
    if not T.STATE["trades"]:
        return "沒有平倉紀錄"
    t = T.STATE["trades"][-1]
    if t.get("exit") is None or abs(float(t["exit"]) - want) > 1e-6:
        return f"出場價 {t.get('exit')}，應為兩段加權 {want:.6f}（只用了最後一張單的均價）"
    if abs(float(t.get("qty") or 0) - q0) > 1e-9:
        return f"結帳數量 {t.get('qty')}，應為 {q0}"


@case("15-3d", "平倉單一直是 NEW：查不到最終狀態就撤掉這張單，不結帳、停損留著", allow=("平倉失敗",))
def _():
    ex, clock = fresh()
    open_long(ex)
    ex.market_new["close"] = "stuck"
    n0 = len(ex.calls)
    r = T.close_position("XUSDT")
    need(market_posts(ex, n0, close=True), "平倉單沒有送出（前提）")
    if r.get("ok") or "XUSDT" not in T.STATE["positions"]:
        return "平倉單一直是 NEW，卻當成平掉了"
    if not stops_on_exchange(ex):
        return "停損被撤了"
    if not any(m == "DELETE" and p == "/fapi/v1/order" for m, p, _ in ex.calls[n0:]):
        return "卡住的平倉單沒有撤（之後才成交會變成沒人知道的數量）"


@case("15-3e", "守衛：停損不見＋補掛時已穿過（-2021）→ 平倉單回 NEW 沒成交：不能結帳，要待平倉", allow=("平倉失敗",))
def _():
    ex, clock = fresh()
    open_long(ex)
    for k in [k for k, v in ex.algo.items() if v.get("type") == "STOP_MARKET"]:
        ex.algo.pop(k)                                          # 停損不見了
    ex.reject_algo = lambda p: (400, {"code": -2021, "msg": "Order would immediately trigger."}) \
        if p.get("type") == "STOP_MARKET" else None
    ex.market_new["close"] = "never"
    n0 = len(ex.calls)
    for _i in range(4):
        T.guard_positions()
        clock.sleep(20)
    need(market_posts(ex, n0, close=True), "守衛沒有走到平倉（前提）")
    if "XUSDT" not in T.STATE["positions"]:
        return "平倉單沒成交，守衛卻結帳了（部位還在交易所上）"
    if not (T.STATE["positions"].get("XUSDT") or {}).get("pendingClose"):
        return "沒有記成待平倉"


@case("15-2", "開倉單回 NEW、最後沒成交（EXPIRED 0）：直接判定沒成交、pending 丟掉、不掛停損")
def _():
    ex, clock = fresh()
    ex.mark["XUSDT"] = 100.4
    ex.market_new["open"] = "never"
    n0 = len(ex.calls)
    r = T.open_position("X", "LONG", 100.0, None, stop_pct=5)
    need(entry_sent(ex, n0), "進場單沒有送出（前提）")
    if r.get("ok"):
        return f"沒成交卻回報開倉成功：{r}"
    if "XUSDT" in T.STATE["pending"]:
        return "交易所明確說沒成交，還留著待認領（佔名額、還會等 3 分鐘）"
    if "XUSDT" in T.STATE["positions"]:
        return "沒成交卻記了帳"


@case("15-4a", "開倉只成交一半：帳上數量用成交的，不用送出的；停損照樣掛上")
def _():
    ex, clock = fresh()
    ex.mark["XUSDT"] = 100.4
    ex.market_new["open"] = "partial"
    n0 = len(ex.calls)
    T.open_position("X", "LONG", 100.0, None, stop_pct=5)
    sent = market_posts(ex, n0, close=False)
    need(sent, "進場單沒有送出（前提）")
    got = ex.pos.get(("XUSDT", "LONG"), [0])[0]
    need(0 < got < float(sent[0]["quantity"]), f"模擬交易所沒有部分成交（前提）：{got}")
    pos = T.STATE["positions"].get("XUSDT") or {}
    if abs(float(pos.get("qty") or 0) - got) > 1e-9:
        return f"帳上數量 {pos.get('qty')}，實際成交 {got}（送出 {sent[0]['quantity']}）"
    if not stops_on_exchange(ex):
        return "停損沒有掛上"


@case("15-4b", "開倉回 NEW（其實已成交，ACK 的樣子）：用查單拿到的成交量與均價記帳")
def _():
    ex, clock = fresh()
    ex.mark["XUSDT"] = 100.4
    ex.market_new["open"] = "now"
    n0 = len(ex.calls)
    T.open_position("X", "LONG", 100.0, None, stop_pct=5)
    sent = market_posts(ex, n0, close=False)
    need(sent, "進場單沒有送出（前提）")
    pos = T.STATE["positions"].get("XUSDT") or {}
    if abs(float(pos.get("qty") or 0) - float(sent[0]["quantity"])) > 1e-9:
        return f"帳上數量 {pos.get('qty')}，應為成交量 {sent[0]['quantity']}"
    if not pos.get("entry") or abs(float(pos["entry"]) - 100.4) > 1e-9:
        return f"進場價 {pos.get('entry')}，應為成交均價 100.4"


@case("15-5", "停損掛不上 → 市價平掉（沒有結帳）：這筆平倉的成交價不能留給下一筆同幣的部位當出場價",
      allow=("停損掛單失敗",))
def _():
    ex, clock = fresh()
    ex.mark["XUSDT"] = 100.4
    ex.reject_algo = lambda p: (400, {"code": -1111, "msg": "注入：停損被拒"}) if p.get("type") == "STOP_MARKET" else None
    n0 = len(ex.calls)
    T.open_position("X", "LONG", 100.0, None, stop_pct=5)
    need(market_posts(ex, n0, close=True), "停損掛不上後沒有平倉（前提）")
    need("XUSDT" not in T.STATE["positions"], "平掉後帳上還有部位（前提）")
    ex.reject_algo = None
    r = open_long(ex)
    ex.trigger("XUSDT", "LONG", r["qty"], 97.0)
    T.sync_positions()
    t = T.STATE["trades"][-1] if T.STATE["trades"] else {}
    need(t.get("symbol") == "XUSDT", "第二筆沒有結帳（前提）")
    if t.get("exit") is None or abs(float(t["exit"]) - 97.0) > 1e-9:
        return f"出場價 {t.get('exit')}，應為 97.0（用到上一筆沒結帳的平倉成交價）"


@case("15-6", "自檢有第 15 條的判讀項目，而且通過")
def _():
    ex, clock = fresh()
    import preflight
    importlib.reload(preflight)
    res = {r["item"]: r for r in preflight.check()}
    need(res, "自檢沒有結果（前提）")
    item = res.get("市價單成交判讀")
    if not item or item["status"] != "ok":
        return f"自檢沒有第 15 條的判讀項目或沒通過：{item}"


@case("15-7", "r42：開倉回應沒有均價（同側有別人的部位）：用單號在成交明細算實際成交價，不用標記價")
def _():
    ex, clock = fresh()
    ex.pos[("XUSDT", "LONG")] = [2.0, 90.0]                     # 共用帳號同側已有別人的部位（基準 > 0）
    ex.drop_avg = True
    r = open_long(ex, mark=100.4)
    own = [f for f in ex.fills if f["side"] == "BUY"]
    need(own, "模擬交易所沒有進場成交（前提）")
    want = float(own[-1]["price"])
    need(abs(want - 100.4) > 1e-6, "成交價跟標記價一樣，分不出來（前提）")
    pos = T.STATE["positions"].get("XUSDT") or {}
    if not pos.get("entry") or abs(float(pos["entry"]) - want) > 1e-9:
        return f"進場價 {pos.get('entry')}，應為成交明細的 {want}（{r.get('warnings')}）"


@case("15-8", "r42：平倉回應沒有均價（同側有別人的部位）：用單號在成交明細算出場價，不能記未知或亂估")
def _():
    ex, clock = fresh()
    ex.pos[("XUSDT", "LONG")] = [2.0, 90.0]
    open_long(ex, mark=100.4)
    ex.drop_avg = True
    ex.mark["XUSDT"] = 103.0
    n0 = len(ex.calls)
    T.close_position("XUSDT")
    need(market_posts(ex, n0, close=True), "平倉單沒有送出（前提）")
    sells = [f for f in ex.fills if f["side"] == "SELL"]
    need(sells, "模擬交易所沒有平倉成交（前提）")
    if not T.STATE["trades"]:
        return "沒有平倉紀錄"
    t = T.STATE["trades"][-1]
    if t.get("exit") is None or abs(float(t["exit"]) - float(sells[-1]["price"])) > 1e-9:
        return f"出場價 {t.get('exit')}，應為成交明細的 {sells[-1]['price']}"


# ════ 第 8 條 r40：讀取失敗期間的假恢復、對帳、半套參數、推播沒設定 ═══

@case("8-an", "讀取失敗期間原檔不見了（被改名、搬走）：不能當成全新開始（假恢復）")
def _():
    ex, clock = fresh(keep_save=True)
    T.AUTO["on"] = True
    T.auto_roll_day()
    path = corrupt_file()
    T.load_state(path)
    ok0, _w = T.auto_can_trade("XUSDT")
    need(not ok0, "讀取失敗時本來就該暫停開新倉（前提）")
    os.replace(path, path + ".moved")
    T.retry_load_state()
    ok, why = T.auto_can_trade("XUSDT")
    if ok:
        return "原檔不見了就「恢復」開新倉——帳是空的，交易所上的部位會被當成別人的"


@case("8-ao", "讀取失敗期間的存檔：原檔不動，這段期間的狀態寫到旁邊的 .unloaded（不是靜靜丟掉）")
def _():
    ex, clock = fresh(keep_save=True)
    path = corrupt_file()
    bad = open(path, encoding="utf-8").read()
    T.load_state(path)
    T.CFG["riskPct"] = 1.25
    T.save_state()
    need(open(path, encoding="utf-8").read() == bad, "原檔被蓋掉了（r39 就該擋住，前提）")
    side = path + ".unloaded"
    if not os.path.exists(side):
        return "讀取失敗期間的變更沒有留下來（沒有 .unloaded）"
    if json.load(open(side, encoding="utf-8")).get("cfg", {}).get("riskPct") != 1.25:
        return ".unloaded 裡沒有這段期間的設定"


@case("8-ap", "讀取失敗期間手動對帳：不動帳，並講明「無法對帳」（不能回「無異動」）")
def _():
    ex, clock = fresh(keep_save=True)
    T.load_state(corrupt_file())
    r = T.sync_positions()
    if "無法對帳" not in str((r or {}).get("error")):
        return f"沒講明無法對帳：{r}"


@case("8-aq", "讀取失敗期間撤孤兒單：帳沒載入分不出是不是自己的，不撤")
def _():
    ex, clock = fresh(keep_save=True)
    T.load_state(corrupt_file())
    ex.algo[4242] = {"symbol": "XUSDT", "side": "SELL", "type": "STOP_MARKET", "closePosition": "true"}
    need(4242 in ex.algo, "孤兒單沒有放上去（前提）")
    r = T.cancel_orphan("XUSDT", "4242")
    if 4242 not in ex.algo:
        return "帳沒載入就撤了一張條件單"
    if r.get("ok"):
        return f"回報撤單成功：{r}"


@case("8-ar", "自動下單設定：一個值不合法，整批不套用（以前 on 先打開、後面丟例外，停在半套）")
def _():
    ex, clock = fresh()
    M = main_mod()
    M.push_all = lambda *a, **k: None
    T.AUTO["on"] = False
    T.AUTO["maxPerDay"] = 6
    code, body = M.trade_handle_safe("/api/trade/auto", {"on": True, "maxPerDay": "abc"})
    if T.AUTO["on"] is not False:
        return "其中一個值不合法，自動下單照樣被打開了（半套）"
    if code != 400:
        return f"沒有明確回報不合法（HTTP {code}）：{body}"
    if "maxPerDay" not in str(body.get("error")):
        return f"沒講是哪個欄位：{body}"


@case("8-as", "監控設定：一個值不合法，整批不套用（以前觀察清單先換掉、topN 才丟例外）")
def _():
    ex, clock = fresh()
    M = main_mod()
    M.MON["watch"] = ["aaa"]
    try:
        code, body = M.mon_handle("/api/monitor/config", {"watch": ["bbb"], "topN": "x"})
    except Exception as e:
        code, body = 500, {"error": f"{type(e).__name__}"}
    if M.MON["watch"] != ["aaa"]:
        return f"觀察清單被換掉了（半套）：{M.MON['watch']}"
    if code != 400:
        return f"沒有明確回報不合法（HTTP {code}）"


@case("8-at", "一個推播管道都沒設定：記一次到錯誤區（不是每則都靜靜消失），自檢列為異常")
def _():
    ex, clock = fresh()
    M = main_mod()
    buf, old = io.StringIO(), sys.stderr
    sys.stderr = buf
    try:
        M.push_all("測試一", "內容")
        M.push_all("測試二", "內容")
    finally:
        sys.stderr = old
    errs = (getattr(M, "NOTIFY_ERR", None) or {}).get("errors") or []
    hits = [e for e in errs if "沒有設定任何推播管道" in e.get("msg", "")]
    if len(hits) != 1:
        return f"沒有設定推播管道的紀錄應該正好一筆，實際 {len(hits)}"
    import preflight
    res = {r["item"]: r for r in preflight.check()}
    item = res.get("推播管道")
    if not item or item["status"] != "fail":
        return f"自檢沒有列出推播管道沒設定：{item}"


@case("8-au", "telegram.json 讀不到：壞檔另存、記錯誤區、之後存檔不覆寫原檔")
def _():
    ex, clock = fresh()
    M = main_mod()
    p = M.tg_file()
    with open(p, "w", encoding="utf-8") as f:
        f.write('{"token": "abc", "chats": [{"id"')
    bad = open(p, encoding="utf-8").read()
    buf, old = io.StringIO(), sys.stderr
    sys.stderr = buf
    try:
        M.tg_load()
        M.tg_save()
    finally:
        sys.stderr = old
    if open(p, encoding="utf-8").read() != bad:
        return "讀不到之後存檔把原檔蓋掉了（聊天室設定與證據都沒了）"
    if not any(n.startswith("telegram.json.bad-") for n in os.listdir(os.path.dirname(p))):
        return "壞檔沒有另存"
    if "telegram.json" not in (getattr(M, "CONF_ERR", None) or {}):
        return "錯誤區沒有記下 telegram.json 讀不到"


# ════ 第 8 條 r41：不合法的值「跳過」、其他照套、回報成功 ═══════════

@case("8-av", "交易設定：一個值轉型失敗，不能跳過它、其他照套、回報成功")
def _():
    ex, clock = fresh()
    M = main_mod()
    T.CFG["riskPct"] = 0.5
    code, body = M.trade_handle_safe("/api/trade/config", {"riskPct": 1.0, "leverage": "abc"})
    if T.CFG["riskPct"] != 0.5:
        return f"其中一個值不合法，其他照樣套上了（riskPct {T.CFG['riskPct']}）"
    if code != 400:
        return f"沒有明確回報不合法（HTTP {code}）"
    if "leverage" not in str(body.get("error")):
        return f"沒講是哪個欄位：{body}"


@case("8-aw", "交易設定：超出範圍不能悄悄夾到邊界；欄位名稱打錯不能回報成功")
def _():
    ex, clock = fresh()
    M = main_mod()
    T.CFG["riskPct"] = 0.5
    code1, _b1 = M.trade_handle_safe("/api/trade/config", {"riskPct": 50})
    code2, _b2 = M.trade_handle_safe("/api/trade/config", {"riskPctt": 1.0})
    if T.CFG["riskPct"] != 0.5:
        return f"超出範圍的值被夾到 {T.CFG['riskPct']} 套上了"
    if code1 != 400:
        return f"超出範圍回 HTTP {code1}（應整批不套用並回報）"
    if code2 != 400:
        return f"欄位名稱打錯回 HTTP {code2}（畫面會顯示成功，其實什麼都沒改）"


@case("8-ax", "Telegram 送出失敗（每個聊天室那一層）：要寫錯誤區——不能 mock 掉整個 notify_telegram 來測")
def _():
    ex, clock = fresh()
    M = main_mod()
    M._tg.update({"token": "t", "chats": [{"id": 111}]})
    calls = []

    def boom(method, params=None, timeout=30):
        calls.append(method)
        raise RuntimeError("注入：Telegram 連不上")
    M.tg_api = boom
    buf, old = io.StringIO(), sys.stderr
    sys.stderr = buf
    try:
        M.push_all("測試", "內容")
    finally:
        sys.stderr = old
    need(calls, "沒有真的呼叫到 Telegram（前提）")
    if "注入：Telegram 連不上" not in buf.getvalue():
        return "Telegram 送出失敗完全沒有訊息（每個聊天室各自 except: pass）"
    errs = (getattr(M, "NOTIFY_ERR", None) or {}).get("errors") or []
    if not any("注入：Telegram 連不上" in e.get("msg", "") for e in errs):
        return "錯誤區沒有這筆推播失敗"


@case("8-ay", "監控設定存檔失敗：照節奏推播（以前 except: pass）")
def _():
    ex, clock = fresh()
    M = main_mod()
    blocker = os.path.join(M.CACHE_DIR, "檔案不是資料夾")
    open(blocker, "w").write("x")
    M.CACHE_DIR = blocker                                        # 在檔案底下建資料夾 → 存檔一定失敗
    pushed = []
    M.push_all = lambda t, x: pushed.append(t)
    buf, old = io.StringIO(), sys.stderr
    sys.stderr = buf
    try:
        M.mon_save()
    finally:
        sys.stderr = old
    if not [t for t in pushed if "存檔失敗" in t]:
        return "設定存檔失敗沒有推播"


@case("8-az", "持倉紀錄沒載入時改交易設定：要講明這次設定只暫存（載入後會以狀態檔為準）")
def _():
    ex, clock = fresh(keep_save=True)
    T.load_state(corrupt_file())
    M = main_mod()
    code, body = M.trade_handle_safe("/api/trade/config", {"riskPct": 1.0})
    need(code == 200, f"合法的設定被拒（前提）：{body}")
    if "warning" not in body:
        return "沒講明持倉紀錄沒載入時設定只暫存"


# ════ 用法第 5 點 r40、r41：模擬交易所跟真的一樣 ════════════════

@case("u-1", "模擬交易所：全量部位表回所有交易過的幣、數量 0 的列、雙向兩側", infra=True)
def _():
    ex, clock = fresh("hedge")
    ex.pos[("AUSDT", "LONG")] = [0.0, 0.0]
    ex.pos[("BUSDT", "SHORT")] = [2.0, 50.0]
    st, rows = ex("GET", "/fapi/v2/positionRisk", {}, True, 5)
    got = {(r["symbol"], r["positionSide"]): r["positionAmt"] for r in rows}
    want = {("AUSDT", "LONG"): "0.0", ("AUSDT", "SHORT"): "0", ("BUSDT", "LONG"): "0", ("BUSDT", "SHORT"): "-2.0"}
    if got != want:
        return f"全量部位表 {got}，應為 {want}"


@case("u-2", "模擬交易所：市價單沒帶 RESULT 回 ACK（NEW、成交 0、均價 0），查單才是 FILLED", infra=True)
def _():
    ex, clock = fresh()
    st, d = ex("POST", "/fapi/v1/order", {"symbol": "XUSDT", "side": "BUY", "type": "MARKET", "quantity": "1"}, True, 5)
    if st != 200 or d.get("status") != "NEW" or d.get("executedQty") != "0":
        return f"沒帶 RESULT 的回應應為 ACK：{st} {d}"
    st2, d2 = ex("GET", "/fapi/v1/order", {"symbol": "XUSDT", "orderId": d.get("orderId")}, True, 5)
    if d2.get("status") != "FILLED" or d2.get("executedQty") != "1.0":
        return f"查單應為 FILLED、成交 1.0：{d2}"


@case("u-3", "模擬交易所 Ex42：never＝回 NEW、查單 EXPIRED、部位不變；stuck＝撤單後 CANCELED", infra=True)
def _():
    ex, clock = fresh()
    ex.market_new["open"] = "never"
    st, d = ex("POST", "/fapi/v1/order", {"symbol": "XUSDT", "side": "BUY", "type": "MARKET", "quantity": "1",
                                          "newOrderRespType": "RESULT"}, True, 5)
    st2, d2 = ex("GET", "/fapi/v1/order", {"symbol": "XUSDT", "orderId": d.get("orderId")}, True, 5)
    if d.get("status") != "NEW" or d2.get("status") != "EXPIRED" or ex.pos.get(("XUSDT", "LONG"), [0])[0] != 0:
        return f"never 模式不對：{d} {d2} {ex.pos}"
    ex.market_new["open"] = "stuck"
    st, d = ex("POST", "/fapi/v1/order", {"symbol": "XUSDT", "side": "BUY", "type": "MARKET", "quantity": "1"}, True, 5)
    st3, d3 = ex("DELETE", "/fapi/v1/order", {"symbol": "XUSDT", "orderId": d.get("orderId")}, True, 5)
    if st3 != 200 or d3.get("status") != "CANCELED":
        return f"stuck 撤單後應為 CANCELED：{st3} {d3}"


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✓' if not err else '✕'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
