"""測試案例的共用框架（取代原本每支測試檔各自複製的 case()）。

以前 5 支測試檔各有一份 case()，而且版本不一：test_r11 的那份完全沒有攔截錯誤輸出，
「沒有程式錯誤被吞掉」這條檢查在它的 23 項上從來沒執行過（測錯方式第 19 種）。

每個案例：
  - 前提不成立（need 失敗）→ 回報「前提不成立」
  - 攔截錯誤輸出，出現程式錯誤（NameError 之類）而案例本身又通過 → 判失敗（第 14 條）
  - 記錄目前案例給突變命中紀錄用
selftest() 用固定的人造案例確認攔截真的生效（第 19 種：攔截沒生效時紀錄是空的，斷言照樣成立）。
"""
import io
import sys

import tests.fake_exchange as FX
from tests.fake_exchange import Pre, PROGRAM_ERRORS


def make_case(results):
    def case(tag, desc, allow=(), infra=False):
        """allow：這一項刻意注入、預期會出現在錯誤輸出的字串（其他程式錯誤照樣攔下）。
        infra：這一項只測測試環境本身（模擬交易所、框架、檢查器），不測程式（用法第 5 點 r28）。
               執行後清掉它的突變命中，讓突變檢查自動歸為無關；check_tests 會確認它在 fresh() 之後不碰程式。"""
        def deco(fn):
            FX.CURRENT[0] = tag
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
            if infra:
                FX.HITS.pop(tag, None)
            results.append((tag, desc, err))
            return fn
        return deco
    return case


def selftest_modules():
    """錯誤掃描要攔得到每個模組寫的錯誤（第 19 種 r29：gold-scalper 只攔了一個模組的 logger）。
    crypto-screener 不用 logging，各模組直接寫 sys.stderr——在框架的攔截底下，讓 trader 與 main
    各自走一次真的出錯路徑，回傳攔到了哪些模組。"""
    import importlib
    import trader as T
    import main as M
    importlib.reload(M)
    M.trader = T
    M.push_all = lambda *a, **k: None
    res = []
    case = make_case(res)
    seen = set()

    @case("m", "兩個模組各寫一次錯誤")
    def _():
        T._pos_step_error("自我驗證", "XUSDT", NameError("name 'zz' is not defined"))
        M._step("自我驗證", lambda: (_ for _ in ()).throw(NameError("name 'yy' is not defined")))

    err = res[0][2] if res else ""
    if "程式錯誤被吞掉" in (err or ""):
        seen.add("trader" if "zz" in err else "main")
        # 第一條被攔到的是 trader 寫的；再單獨確認 main 的
        res2 = []
        c2 = make_case(res2)

        @c2("m2", "只有 main 寫錯誤")
        def _():
            M._step("自我驗證", lambda: (_ for _ in ()).throw(NameError("name 'yy' is not defined")))
        if res2 and "程式錯誤被吞掉" in (res2[0][2] or ""):
            seen.add("main")
    return seen


def selftest():
    """固定人造案例：攔截要真的生效。"""
    res = []
    case = make_case(res)

    @case("s1", "印出程式錯誤但沒拋 → 要判失敗")
    def _():
        sys.stderr.write("  ! 某步驟出錯：NameError: name 'x' is not defined\n")

    @case("s2", "印出允許的注入錯誤 → 通過", allow=("注入",))
    def _():
        sys.stderr.write("  ! 注入的 KeyError\n")

    @case("s3", "前提不成立 → 判失敗")
    def _():
        raise Pre("沒走到")

    @case("s4", "什麼都沒印、通過")
    def _():
        return None

    got = {t: bool(e) for t, _, e in res}
    want = {"s1": True, "s2": False, "s3": True, "s4": False}
    return (None if got == want else f"預期 {want}，實際 {got}"), len(res)
