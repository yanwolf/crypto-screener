"""BINANCE_LESSONS r47 → r49 逐段差異的檢查項目（crypto-screener）。

r48 兩條執行緒交錯：用模擬交易所的 pause 讓背景那條停在「查交易所」，另一條執行緒做網頁操作。
新介面（ENGINE_OPS、ENGINE_WAIT）一律用 getattr 取，舊版上是斷言失敗、不是測試崩掉。

    python3 -m tests.test_r49
"""
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, ROOT)
from tests import test_r42 as R                                 # noqa: E402
from tests.fake_exchange import need, entry_sent                # noqa: E402
from tests.harness import make_case                             # noqa: E402

RESULTS = []
case = make_case(RESULTS)
fresh, open_long, market_posts, main_mod = R.fresh, R.open_long, R.market_posts, R.main_mod


def T():
    return R.T


def _const(v):
    """全大寫的常數表（標籤、分數對照）：值都是字串或數字。"""
    vals = v.values() if isinstance(v, dict) else v
    return all(isinstance(x, (str, int, float, tuple)) for x in vals)


def pause_bg(ex, path):
    ex.pause = {"path": path, "thread": "bg", "arrived": threading.Event(), "go": threading.Event()}
    return ex.pause


def run(name, fn, out):
    def body():
        try:
            out[name] = fn()
        except Exception as e:                                  # 讓斷言看到，不讓執行緒默默結束
            out[name] = e
    th = threading.Thread(target=body, name=name, daemon=True)
    th.start()
    return th


# ════ 第 8 條 r48：兩條執行緒交錯 ═══════════════════════════════

@case("8-be", "背景對帳停在查交易所時，手動平倉不能同時送單；背景做完才輪到，只結一次帳")
def _():
    ex, clock = fresh()
    open_long(ex)
    pz = pause_bg(ex, "/fapi/v2/positionRisk")
    out = {}
    bg = run("bg", T().sync_positions, out)
    need(pz["arrived"].wait(5), "背景對帳沒有停在查部位（前提）")
    n0 = len(ex.calls)
    web = run("web", lambda: T().close_position("XUSDT"), out)
    web.join(0.5)
    sent_during = len(market_posts(ex, n0, close=True))
    pz["go"].set()
    bg.join(10)
    web.join(10)
    need(not bg.is_alive() and not web.is_alive(), "執行緒沒有結束（前提）")
    need(isinstance(out.get("web"), dict), f"手動平倉沒有正常回傳（前提）：{out.get('web')!r}")
    if sent_during:
        return f"背景對帳停在查交易所時，手動平倉照樣送出了 {sent_during} 張平倉單（兩條交錯）"
    booked = [t for t in T().STATE["trades"] if t.get("symbol") == "XUSDT"]
    if len(booked) != 1:
        return f"XUSDT 結帳 {len(booked)} 次（應為 1 次）"


@case("8-bf", "網頁操作等引擎鎖有上限：背景停住時手動平倉要回「背景正在處理」，不能一直掛著、也不能動到部位")
def _():
    ex, clock = fresh()
    open_long(ex)
    M = main_mod()
    M.push_all = lambda *a, **k: None
    M.ENGINE_WAIT = 0.3
    pz = pause_bg(ex, "/fapi/v2/positionRisk")
    out = {}
    bg = run("bg", T().sync_positions, out)
    need(pz["arrived"].wait(5), "背景對帳沒有停在查部位（前提）")
    n0 = len(ex.calls)
    web = run("web", lambda: M.trade_handle_safe("/api/trade/close", {"symbol": "XUSDT"}), out)
    web.join(3)
    alive = web.is_alive()
    pz["go"].set()
    bg.join(10)
    web.join(10)
    if alive:
        return "網頁手動平倉等了 3 秒還掛著（沒有等待上限）"
    code, body = out.get("web") if isinstance(out.get("web"), tuple) else (None, {})
    if code != 503 or "背景正在處理" not in str((body or {}).get("error")):
        return f"應回 503「背景正在處理」，實際 {code} {body}"
    if market_posts(ex, n0, close=True):
        return "回了忙碌，卻還是送了平倉單"
    if "XUSDT" not in T().STATE["positions"]:
        return "回了忙碌，部位卻被動了"


@case("8-bg", "動帳本的函式本身都套了引擎鎖（不是只鎖呼叫端）")
def _():
    ex, clock = fresh(keep_save=True)
    want = ["open_position", "auto_open", "close_position", "sync_positions", "adopt_pending", "manage_positions",
            "guard_positions", "move_to_breakeven", "retry_pending_closes", "retry_leftovers", "record_close",
            "cancel_orphan", "set_excluded", "configure", "save_state", "load_state", "retry_load_state"]
    need(all(callable(getattr(T(), n, None)) for n in want), "要檢查的函式不存在（前提）")
    missing = [n for n in want if not getattr(getattr(T(), n, None), "engine_op", False)]
    if missing:
        return f"沒套引擎鎖：{missing}"


@case("8-bh", "自動開倉停在查帳戶時，同一檔的手動開倉不能也送出（兩張進場單）")
def _():
    ex, clock = fresh()
    ex.mark["XUSDT"] = 100.4
    pz = pause_bg(ex, "/fapi/v2/account")
    out = {}
    n0 = len(ex.calls)
    bg = run("bg", lambda: T().open_position("X", "LONG", 100.0, None, stop_pct=5), out)
    need(pz["arrived"].wait(5), "第一張開倉沒有停在查帳戶（前提）")
    web = run("web", lambda: T().open_position("X", "LONG", 100.0, None, stop_pct=5), out)
    web.join(0.5)
    pz["go"].set()
    bg.join(10)
    web.join(10)
    need(isinstance(out.get("bg"), dict) and out["bg"].get("ok"), f"第一張開倉沒成功（前提）：{out.get('bg')!r}")
    entries = market_posts(ex, n0, close=False)
    if len(entries) != 1:
        return f"同一檔送了 {len(entries)} 張進場單"
    if (out.get("web") or {}).get("ok"):
        return f"第二張回報成功：{out.get('web')}"


@case("8-bi", "手動開倉：同一檔已有部位時不能再送（以前記帳時直接蓋掉原本那筆）")
def _():
    ex, clock = fresh()
    open_long(ex)
    first = dict(T().STATE["positions"]["XUSDT"])
    n0 = len(ex.calls)
    r = T().open_position("X", "LONG", 100.0, None, stop_pct=5)
    if entry_sent(ex, n0):
        return "已有部位還送了進場單"
    if r.get("ok"):
        return f"回報成功：{r}"
    if (T().STATE["positions"].get("XUSDT") or {}).get("opened") != first.get("opened"):
        return "原本那筆被蓋掉了"


# ════ 第 8 條 r48：空表單＝清空全部 ═══════════════════════════════

@case("8-bj", "監控設定送空的 cfg、或缺 bull／bear：回錯誤，原本的設定不能被清掉（空 cfg 會讓背景監控靜靜停掉）")
def _():
    ex, clock = fresh()
    M = main_mod()
    M.mon_save = lambda: True
    good = {"bull": {"minScore": 60}, "bear": {"minScore": 60}}
    M.MON["cfg"] = dict(good)
    codes = []
    for bad in ({}, {"bull": {"minScore": 60}}, {"bull": {}, "bear": {}}):
        try:
            code, _b = M.mon_handle("/api/monitor/config", {"cfg": bad})
        except Exception as e:
            code = f"例外 {type(e).__name__}"
        codes.append(code)
    code_ok, _body_ok = M.mon_handle("/api/monitor/config", {"cfg": good})
    need(code_ok == 200, "合法的 cfg 被拒（前提）")
    if codes != [400, 400, 400]:
        return f"空的或缺一組的 cfg 回 {codes}（應都是 400）"


@case("8-bk", "解除 Telegram 聊天室沒帶 id：回錯誤（以前回成功、什麼都沒改）")
def _():
    ex, clock = fresh()
    M = main_mod()
    M.tg_save = lambda: True
    M._tg["chats"] = [{"id": "111"}]
    code, body = M.tg_handle("/api/tg/unpair", {})
    if code == 200:
        return f"沒帶 id 回成功：{body}"
    if M._tg["chats"] != [{"id": "111"}]:
        return "聊天室被動了"


# ════ 第 8 條 r49：風控讀不到，被當成「沒有虧損」 ═════════════════

@case("8-bl", "每日虧損斷路器：損益未知（出場成交價讀不到）不能當成沒虧——以每筆 -1R 算進上限")
def _():
    ex, clock = fresh()
    T().AUTO["on"] = True
    T().auto_roll_day()
    T().AUTO.update({"closedR": 0.0, "unknownToday": 3, "dailyLossR": -2.0, "blocked": None})
    ok, why = T().auto_can_trade("XUSDT")
    need(isinstance(why, (str, type(None))), "回傳格式不對（前提）")
    if ok:
        return "今天 3 筆損益讀不到，斷路器照樣放行（成交明細查詢壞掉時永遠不會觸發）"
    if "未知" not in str(why):
        return f"擋下了但沒講明原因含未知的筆數：{why}"


@case("8-bm", "每日虧損斷路器：沒有未知時照原本的規則（對照組）")
def _():
    ex, clock = fresh()
    T().AUTO["on"] = True
    T().auto_roll_day()
    T().AUTO.update({"closedR": -1.0, "unknownToday": 0, "dailyLossR": -2.0, "blocked": None})
    ok, why = T().auto_can_trade("XUSDT")
    if not ok:
        return f"虧 -1R、上限 -2R，不該擋：{why}"


# ════ 用法 r49：框架重設涵蓋的模組改成自動列舉 ═══════════════════

@case("u-5", "backend/ 底下每個模組自動列舉：fresh() 不重新載入的模組不能有模組層級的可變狀態")
def _():
    ex, clock = fresh()
    import importlib
    reloaded = {"trader", "main"}                               # fresh()／main_mod() 每個情境重新載入
    mods = sorted(f[:-3] for f in os.listdir(os.path.join(ROOT, "backend")) if f.endswith(".py"))
    need(len(mods) >= 4, f"backend/ 只列到 {mods}（前提）")
    exempt = {("preflight", "LAST"): "自檢結果，每次 check() 開頭整個換掉",
              ("preflight", "EXTRA"): "main 重新載入時先移除舊的登記再加（test_r42 8-at 驗證）"}
    bad = []
    for name in mods:
        if name in reloaded:
            continue
        m = importlib.import_module(name)
        for k, v in vars(m).items():
            if k.startswith("__") or not isinstance(v, (dict, list, set)):
                continue
            if k.startswith("_") or (k.isupper() and (name, k) not in exempt and v and not _const(v)):
                bad.append(f"{name}.{k}")
    if bad:
        return f"不會被重設的模組層級狀態：{bad}（加進 fresh() 或寫理由豁免）"


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✓' if not err else '✕'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
