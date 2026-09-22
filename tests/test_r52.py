"""BINANCE_LESSONS r50 → r52 逐段差異的檢查項目（crypto-screener）。

交錯測試的對照組（用法第 5 點 r51、r52）：同一個情境跑兩次——真的引擎鎖、以及什麼都不擋的假鎖。
假鎖那組**必須出事**（兩次結帳、兩張進場單、忙碌時照樣送單），有鎖那組不能出事，兩組並列才證明是鎖擋下的。
停的位置選在「檢查」與「記帳」之間那段 I/O；對照組還要確認另一條執行緒真的走到了送單／記帳那一步。
新介面（_engine_lock、ENGINE_WAIT）先加存在性前提，舊版上是前提不成立、不是測試崩掉。

    python3 -m tests.test_r52
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
fresh, open_long, market_posts, main_mod, stops = R.fresh, R.open_long, R.market_posts, R.main_mod, R.stops_on_exchange


def T():
    return R.T


class NoLock:
    """對照組用：什麼都不擋的假鎖。"""

    def acquire(self, blocking=True, timeout=-1):
        return True

    def release(self):
        pass


def use_lock(real):
    need(hasattr(T(), "_engine_lock") and hasattr(T(), "engine_op"), "程式沒有引擎鎖（前提：r50 起才有）")
    if not real:
        T()._engine_lock = NoLock()


def pause_bg(ex, path):
    ex.pause = {"path": path, "thread": "bg", "arrived": threading.Event(), "go": threading.Event()}
    return ex.pause


def run(name, fn, out):
    def body():
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = e
    th = threading.Thread(target=body, name=name, daemon=True)
    th.start()
    return th


def queried(ex, since, path):
    return [c for c in ex.calls[since:] if c[1] == path]


# ── 情境一：手動平倉停在「成交後、記帳前」，背景對帳同時看到交易所上沒了 ──────────

def scene_double_book(ex, real):
    use_lock(real)
    open_long(ex)
    ex.drop_avg = True                   # 回應沒有均價 → 平倉後用單號查成交明細：「成交」到「記帳」之間的 I/O
    pz = pause_bg(ex, "/fapi/v1/userTrades")
    out = {}
    a = run("bg", lambda: T().close_position("XUSDT"), out)
    need(pz["arrived"].wait(5), "手動平倉沒有停在「成交後、記帳前」（前提）")
    need(ex.pos.get(("XUSDT", "LONG"), [0])[0] <= 1e-9, "停住時交易所上的部位應已平掉（前提：停的位置在成交之後）")
    n0 = len(ex.calls)
    b = run("sync", T().sync_positions, out)
    b.join(1.0)
    b_reached = bool(queried(ex, n0, "/fapi/v2/positionRisk"))
    pz["go"].set()
    a.join(10)
    b.join(10)
    need(not a.is_alive() and not b.is_alive(), "執行緒沒有結束（前提）")
    booked = [t for t in T().STATE["trades"] if t.get("symbol") == "XUSDT"]
    return len(booked), b_reached, out


@case("r52-1", "手動平倉停在成交後、記帳前時背景對帳：有鎖 → 只結一次帳")
def _():
    ex, clock = fresh()
    n, reached, out = scene_double_book(ex, True)
    need(isinstance(out.get("bg"), dict), f"手動平倉沒有正常回傳（前提）：{out.get('bg')!r}")
    if n != 1:
        return f"XUSDT 結帳 {n} 次（應為 1 次）"


@case("r52-1c", "對照組（假鎖）：同一個情境必須結兩次帳，而且對帳真的在停住期間查了交易所")
def _():
    ex, clock = fresh()
    n, reached, out = scene_double_book(ex, False)
    if not reached:
        return "對照組裡對帳沒有在停住期間走到查交易所——停的位置或模擬環境不對，證明不了鎖"
    if n != 2:
        return f"對照組只結了 {n} 次帳——停的位置不在「檢查」與「記帳」之間，這個情境證明不了鎖"


# ── 情境二：兩條同時開同一檔，第一條停在「同檔檢查後、寫 pending 前」 ──────────────

def scene_double_open(ex, real):
    use_lock(real)
    ex.mark["XUSDT"] = 100.4
    pz = pause_bg(ex, "/fapi/v2/account")
    out = {}
    n0 = len(ex.calls)
    a = run("bg", lambda: T().open_position("X", "LONG", 100.0, None, stop_pct=5), out)
    need(pz["arrived"].wait(5), "第一張開倉沒有停在查帳戶（前提）")
    b = run("web", lambda: T().open_position("X", "LONG", 100.0, None, stop_pct=5), out)
    b.join(1.0)
    pz["go"].set()
    a.join(10)
    b.join(10)
    need(isinstance(out.get("bg"), dict) and out["bg"].get("ok"), f"第一張開倉沒成功（前提）：{out.get('bg')!r}")
    return len(market_posts(ex, n0, close=False)), out


@case("r52-2", "兩條同時開同一檔：有鎖 → 只送一張進場單")
def _():
    ex, clock = fresh()
    n, out = scene_double_open(ex, True)
    if n != 1:
        return f"送了 {n} 張進場單"


@case("r52-2c", "對照組（假鎖）：同一個情境必須送出兩張進場單（第二條真的走到送單）")
def _():
    ex, clock = fresh()
    n, out = scene_double_open(ex, False)
    if n != 2:
        return f"對照組只送了 {n} 張——第二條沒走到送單（停的位置或模擬環境不對），證明不了鎖：{out.get('web')!r}"


# ── 情境三：網頁操作等引擎鎖的上限 ──────────────────────────────────────

def scene_busy(ex, real):
    use_lock(real)
    open_long(ex)
    M = main_mod()
    M.push_all = lambda *a, **k: None
    need(hasattr(M, "ENGINE_WAIT"), "main 沒有網頁等待上限（前提）")
    M.ENGINE_WAIT = 0.3
    pz = pause_bg(ex, "/fapi/v2/positionRisk")
    out = {}
    a = run("bg", T().sync_positions, out)
    need(pz["arrived"].wait(5), "背景對帳沒有停在查部位（前提）")
    n0 = len(ex.calls)
    b = run("web", lambda: M.trade_handle_safe("/api/trade/close", {"symbol": "XUSDT"}), out)
    b.join(3)
    pz["go"].set()
    a.join(10)
    b.join(10)
    code, body = out.get("web") if isinstance(out.get("web"), tuple) else (None, {})
    return code, body, len(market_posts(ex, n0, close=True))


@case("r52-3", "背景停住時網頁手動平倉：有鎖 → 0.3 秒後回 503「背景正在處理」、不送單")
def _():
    ex, clock = fresh()
    code, body, sent = scene_busy(ex, True)
    if code != 503:
        return f"應回 503，實際 {code} {body}"
    if sent:
        return "回了忙碌卻送了平倉單"


@case("r52-3c", "對照組（假鎖）：同一個情境網頁手動平倉必須真的送出平倉單")
def _():
    ex, clock = fresh()
    code, body, sent = scene_busy(ex, False)
    if not sent:
        return f"對照組沒有送出平倉單（{code} {body}）——模擬環境讓它提早返回，證明不了鎖"


# ── r51：呼叫端在拿鎖之前讀出的部位 ───────────────────────────────────

@case("r51-1", "反向訊號收緊：部位是拿鎖前讀的，中間被平掉、同一檔又開了新倉 → 舊的那筆不能拿去動新部位的停損")
def _():
    ex, clock = fresh()
    open_long(ex, mark=100.4)
    held = T().STATE["positions"]["XUSDT"]                      # main 的反向訊號流程：先讀出部位（沒拿鎖）
    ex.mark["XUSDT"] = 104.0
    r = T().close_position("XUSDT")                             # 這段期間網頁手動平倉……
    need(r.get("ok") and "XUSDT" not in T().STATE["positions"], f"手動平倉沒成功（前提）：{r}")
    open_long(ex, mark=110.0)                                   # ……接著同一檔又開了新倉
    new = T().STATE["positions"]["XUSDT"]
    need(new is not held and stops(ex), "新倉沒有自己的停損（前提）")
    before = {k: dict(v) for k, v in ex.algo.items()}
    n0 = len(ex.calls)
    T().move_to_breakeven(held, 110.0, force=True, reason="反向訊號")
    touched = [c for c in ex.calls[n0:] if c[0] in ("POST", "DELETE") and "algo" in c[1].lower()]
    if touched or {k: dict(v) for k, v in ex.algo.items()} != before:
        return f"拿舊的那筆（進場 {held.get('entry')}）動了新部位（進場 {new.get('entry')}）的停損：{len(touched)} 次送單／撤單"
    if new.get("stop") != T().STATE["positions"]["XUSDT"].get("stop") or new.get("beMoved"):
        return "新部位的帳被改了"


@case("r51-1b", "部位被平掉後（沒有新倉）再移損：不重掛停損（r20 的「移損前先確認部位」本來就擋住）")
def _():
    ex, clock = fresh()
    open_long(ex, mark=100.4)
    held = T().STATE["positions"]["XUSDT"]
    ex.mark["XUSDT"] = 104.0
    r = T().close_position("XUSDT")
    need(r.get("ok") and "XUSDT" not in T().STATE["positions"], f"手動平倉沒成功（前提）：{r}")
    need(not stops(ex), "平倉後停損應已撤掉（前提）")
    n0 = len(ex.calls)
    T().move_to_breakeven(held, 104.0, force=True, reason="反向訊號")
    placed = [c for c in ex.calls[n0:] if c[0] == "POST" and "algo" in c[1].lower()]
    if placed or stops(ex):
        return f"替已平掉的部位重掛了停損（{len(placed)} 張），交易所上留下孤兒單"


@case("r51-2", "移損：帳上那一筆照常移（對照組，確認新的檢查沒擋到正常情況）")
def _():
    ex, clock = fresh()
    open_long(ex, mark=100.4)
    held = T().STATE["positions"]["XUSDT"]
    ex.mark["XUSDT"] = 104.0
    ev = T().move_to_breakeven(held, 104.0, force=True, reason="反向訊號")
    if not (ev or {}).get("ok"):
        return f"帳上那一筆沒有移損：{ev}"


@case("r51-3", "每個會送單的入口都在拿鎖之後重新讀部位：手動平倉、重試平倉、守衛、移損、開倉都套了引擎鎖")
def _():
    ex, clock = fresh()
    names = ["open_position", "auto_open", "close_position", "retry_pending_closes", "guard_positions",
             "move_to_breakeven", "manage_positions", "sync_positions"]
    need(all(callable(getattr(T(), n, None)) for n in names), "要檢查的函式不存在（前提）")
    missing = [n for n in names if not getattr(getattr(T(), n), "engine_op", False)]
    if missing:
        return f"沒套引擎鎖：{missing}"


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✓' if not err else '✕'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
