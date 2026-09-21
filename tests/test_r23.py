"""BINANCE_LESSONS r21 → r23 逐段差異的檢查項目（crypto-screener 從 r21 回來，含 r22、r23）。

前提斷言檢查「結果」而不只是「有去做」（測錯方式第 16 種）；前提寫出要走的路徑（第 17 種）。

    python3 -m tests.test_r23
"""
import io
import os
import sys
import importlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, ROOT)
import trader as T                                              # noqa: E402
from tests.fake_exchange import Ex17, Clock, PROGRAM_ERRORS, Pre, need, entry_sent  # noqa: E402
import tests.fake_exchange as FX           # noqa: E402

RESULTS = []


def case(tag, desc, allow=()):
    def deco(fn):
        FX.CURRENT[0] = tag                                   # 突變命中紀錄用
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


def open_long(ex, sym="X", mark=100.4):
    ex.mark[sym + "USDT"] = mark
    n0 = len(ex.calls)
    r = T.open_position(sym, "LONG", 100.0, None, stop_pct=5)
    need(entry_sent(ex, n0), f"進場單沒有送出：{r.get('error')}")
    need(sym + "USDT" in T.STATE["positions"], "進場後帳上沒有部位")
    return r


def stops_sent(ex, since):
    return [c[2] for c in ex.calls[since:]
            if c[1] in ("/fapi/v1/algoOrder", "/fapi/v1/order") and c[0] == "POST" and c[2].get("type") == "STOP_MARKET"]


# ════ 第 2 條 r22、r23：開倉成交、認領後掛停損用手上的數量，不重查 ════

@case("2-j", "開倉成交後掛停損：成交確認查到一次之後交易所就「還沒反映」（逐幣回空），仍用成交數量掛上")
def _():
    ex, clock = fresh()
    ex.mark["XUSDT"] = 100.4
    orig = ex.__call__
    state = {"entry": False, "answered": 0}

    def lag(m, p, params, s, t):
        params = params or {}
        if p == "/fapi/v1/order" and params.get("type") == "MARKET" and not params.get("reduceOnly"):
            state["entry"] = True
        st, d = orig(m, p, params, s, t)
        if state["entry"] and p == "/fapi/v2/positionRisk" and params.get("symbol") and isinstance(d, list) and d:
            state["answered"] += 1
            ex.symbol_empty = 99                              # 成交確認拿到答案之後，再查就回空清單
        return st, d
    T._request_raw = lag
    n0 = len(ex.calls)
    r = T.open_position("X", "LONG", 100.0, None, stop_pct=5)
    need(entry_sent(ex, n0), f"進場單沒有送出：{r.get('error')}")
    need(state["answered"] >= 1, "成交確認沒有查到部位（前提：要走的是「確認過、之後才回空」那條路）")
    if not stops_sent(ex, n0):
        return "成交後掛停損又重查了一次、查到空就沒掛"


@case("2-j2", "認領後掛停損：用交易所那一列的數量，不重查（重查回空也照樣掛）")
def _():
    ex, clock = fresh()
    T.STATE["pending"]["XUSDT"] = {"side": "LONG", "qty": 1.0, "stopPct": 5, "note": "", "base": 0.0,
                                   "params": {k: T.CFG.get(k) for k in T.PARAM_KEYS},
                                   "ts": int(clock.now * 1000)}
    ex.pos[("XUSDT", "LONG")] = [0.7, 101.3]
    ex.symbol_empty = 99                                      # 逐幣查詢都回空；認領用的是全量表那一列
    clock.now += 40
    n0 = len(ex.calls)
    T.sync_positions()
    need("XUSDT" in T.STATE["positions"], "沒有認領（前提）")
    placed = stops_sent(ex, n0)
    if not placed:
        return "認領後又重查部位、查到空就沒掛停損"


# ════ 第 8 條 r22：except 不能把已做完的動作當成沒做 ════════

@case("8-o", "結帳時收尾通知出錯：部位只結帳一次、不會被放回帳上重結", allow=("XUSDT",))
def _():
    ex, clock = fresh()
    open_long(ex)
    p = T.STATE["positions"]["XUSDT"]
    p["wantStop"], p["beFails"] = "壞掉的資料", 1              # 收尾通知格式化時會丟例外
    ex.pos[("XUSDT", "LONG")] = [0, 0]
    for _ in range(3):
        try:
            T.sync_positions()
        except Exception:
            pass
    booked = [t for t in T.STATE["trades"] if t.get("symbol") == "XUSDT"]
    need(any(c[0] == "DELETE" for c in ex.calls), "沒有撤條件單（前提：要測的是撤了之後出錯）")
    if len(booked) != 1:
        return f"應只結帳 1 次，實際 {len(booked)} 次（通知出錯被當成沒結帳）"
    if "XUSDT" in T.STATE["positions"]:
        return "已結帳、停損已撤，部位卻還留在帳上"


@case("8-o2", "結帳時算損益的資料缺欄位：照樣結帳（損益記為未知），不會卡住每輪重結", allow=("XUSDT",))
def _():
    ex, clock = fresh()
    open_long(ex)
    T.STATE["positions"]["XUSDT"]["exits"] = {}               # 缺 R，算 R 倍數會丟 KeyError
    ex.pos[("XUSDT", "LONG")] = [0, 0]
    for _ in range(3):
        try:
            T.sync_positions()
        except Exception:
            pass
    booked = [t for t in T.STATE["trades"] if t.get("symbol") == "XUSDT"]
    if len(booked) != 1 or "XUSDT" in T.STATE["positions"]:
        return f"缺欄位就結不了帳：結帳 {len(booked)} 次、帳上仍有 {'XUSDT' in T.STATE['positions']}"


@case("8-p", "殘留單重撤：壞掉的那筆（缺 symbol）留在清單並告警，其他筆照常", allow=("None#", "KeyError"))
def _():
    ex, clock = fresh()
    ex.algo[7001] = {"symbol": "XUSDT", "type": "STOP_MARKET"}
    T.STATE["leftovers"] = [{"id": 1, "via": "algo", "type": "STOP_MARKET", "attempts": 1},     # 缺 symbol
                            {"symbol": "XUSDT", "id": 7001, "via": "algo", "type": "STOP_MARKET", "attempts": 1}]
    T.retry_leftovers()
    need(any(c[0] == "DELETE" for c in ex.calls), "好的那筆沒有重撤（前提）")
    if not any(l.get("id") == 1 for l in T.STATE["leftovers"]):
        return "壞掉的那筆被丟掉了"
    if any(l.get("id") == 7001 for l in T.STATE["leftovers"]):
        return "好的那筆撤掉了卻還在清單"
    if not any("殘留單" in a["title"] and "出錯" in a["title"] for a in T.drain_alerts()):
        return "壞掉的那筆沒有推播"


# ════ 第 8 條 r23：結帳之前出錯不能把部位弄丟；背景執行緒的例外要有人接 ════

@case("8-q", "手動平倉在結帳前出錯（確認查詢丟例外）：部位留在帳上、記待平倉、推播", allow=("XUSDT", "ValueError"))
def _():
    ex, clock = fresh()
    open_long(ex)
    orig = ex.__call__

    def broken(m, p, params, s, t):
        if p == "/fapi/v2/positionRisk" and (params or {}).get("symbol"):
            return 200, [{"symbol": "XUSDT", "positionSide": "BOTH", "positionAmt": "壞", "entryPrice": "1"}]
        return orig(m, p, params, s, t)
    T._request_raw = broken
    T.drain_alerts()
    raised = None
    try:
        r = T.close_position("XUSDT")
    except Exception as e:
        raised, r = e, {}
    if raised:
        return f"例外往外傳（{type(raised).__name__}），呼叫端（網頁）只看到連線中斷"
    need(not r.get("ok"), "平倉沒有失敗（前提：要測的是結帳前出錯）")
    p = T.STATE["positions"].get("XUSDT")
    if not p:
        return "結帳前出錯，部位卻從帳上消失了"
    if not p.get("pendingClose"):
        return "結帳前出錯沒有記待平倉（使用者要平倉的意圖丟了）"
    if not any("XUSDT" in a["text"] for a in T.drain_alerts()):
        return "沒有推播"


@case("8-q2", "手動平倉在結帳後出錯（收尾出錯）：回報已平倉，不能再記待平倉", allow=("XUSDT",))
def _():
    ex, clock = fresh()
    open_long(ex)
    T.STATE["positions"]["XUSDT"]["wantStop"] = "壞掉的資料"
    T.STATE["positions"]["XUSDT"]["beFails"] = 1
    n0 = len(ex.calls)
    try:
        r = T.close_position("XUSDT")
    except Exception as e:
        return f"結帳後出錯，例外往外傳（{type(e).__name__}）"
    need(any(c[2].get("type") == "MARKET" and c[2].get("reduceOnly") for c in ex.calls[n0:]), "沒有送平倉單（前提）")
    need(any(t.get("symbol") == "XUSDT" for t in T.STATE["trades"]), "沒有結帳（前提）")
    if "XUSDT" in T.STATE["positions"]:
        return "已結帳卻被放回帳上"
    if not r.get("ok"):
        return f"已平倉卻回報失敗：{r.get('error')}"


def _main():
    import main as M
    importlib.reload(M)
    M.trader = T
    pushed = []
    M.push_all = lambda title, text: pushed.append((title, text))
    return M, pushed


@case("8-r", "監控執行緒（會自動下單）出錯：照節奏推播，不能只寫錯誤區")
def _():
    ex, clock = fresh()
    M, pushed = _main()
    M.MON["on"] = True
    M.mon_run_once = lambda: (_ for _ in ()).throw(RuntimeError("評分壞了"))
    rnd = getattr(M, "monitor_round", None)
    need(rnd is not None, "沒有可單獨執行一輪的 monitor_round（進入點寫死在無窮迴圈裡）")
    rnd()
    if not any("評分壞了" in x for _, x in pushed):
        return f"監控執行緒出錯沒有推播：{pushed}"


@case("8-r2", "網頁交易操作（手動開平倉）丟例外：回錯誤給網頁並推播，不是連線中斷")
def _():
    ex, clock = fresh()
    M, pushed = _main()
    M.trade_handle = lambda path, payload: (_ for _ in ()).throw(RuntimeError("平倉路由壞了"))
    safe = getattr(M, "trade_handle_safe", None)
    need(safe is not None, "網頁交易操作沒有包裝（例外直接穿出請求處理）")
    code, body = safe("/api/trade/close", {"symbol": "XUSDT"})
    if code != 500 or "平倉路由壞了" not in str(body):
        return f"沒有回錯誤給網頁：{code} {body}"
    if not any("平倉路由壞了" in x for _, x in pushed):
        return "沒有推播"


# ════ 測錯方式第 16 種：前提要檢查確認的「結果」 ═════════════

@case("16-a", "移損遇部位已沒了／查不到：回傳略過的原因，讓測試分得出兩者")
def _():
    ex, clock = fresh()
    open_long(ex)
    p = T.STATE["positions"]["XUSDT"]
    ex.pos[("XUSDT", "LONG")] = [0, 0]
    ev = T.move_to_breakeven(p, 106.0)
    if not (ev and ev.get("skipped") == "gone"):
        return f"部位沒了時沒有回報 gone：{ev}"
    ex2, _c = fresh()
    open_long(ex2)
    p2 = T.STATE["positions"]["XUSDT"]
    ex2.symbol_empty = 1
    ev2 = T.move_to_breakeven(p2, 106.0)
    if not (ev2 and ev2.get("skipped") == "unknown"):
        return f"查不到時沒有回報 unknown：{ev2}"


@case("16-b", "守衛補掛遇部位已沒了／查不到：事件裡帶略過的原因")
def _():
    ex, clock = fresh()
    open_long(ex)
    for o in list(ex.algo):
        if ex.algo[o].get("type") == "STOP_MARKET":
            ex.algo.pop(o)
    ex.pos[("XUSDT", "LONG")] = [0, 0]
    evs = []
    for _ in range(3):
        evs += T.guard_positions()
    if not any(e.get("action") == "skip" and e.get("why") == "gone" for e in evs):
        return f"守衛沒有回報 gone：{evs}"


# ════ 測錯方式第 17 種：前提寫出要走的路徑（第幾張、數量多少）══════

@case("17-a", "雙向帳上數量比交易所多（出一半還沒偵測到）：平倉一開始就送交易所自己的數量，只送一張")
def _():
    ex, clock = fresh("hedge")
    T._mode.update({"hedge": True, "ts": clock.now})
    open_long(ex)
    p = T.STATE["positions"]["XUSDT"]
    real = round(p["qty"] / 2, 1)
    ex.pos[("XUSDT", "LONG")][0] = real                       # 交易所只剩一半，帳上還是全部
    n0 = len(ex.calls)
    r = T.close_position("XUSDT")
    sent = [c[2] for c in ex.calls[n0:] if c[1] == "/fapi/v1/order" and c[2].get("type") == "MARKET"]
    need(len(sent) == 1, f"應只送 1 張平倉單（不是先超量被拒再重送），實際 {len(sent)} 張")
    need(abs(float(sent[0]["quantity"]) - real) < 1e-9, f"平倉數量 {sent[0]['quantity']}，應為交易所上的 {real}")
    if not r.get("ok") or "XUSDT" in T.STATE["positions"]:
        return f"平倉沒有完成：{r}"


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✕' if err else '✓'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
