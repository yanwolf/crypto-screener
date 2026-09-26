"""自動下單的狀態（AUTO）重啟後要讀得回來（2026-09-26：2Z 獲利平倉後重啟，被當成虧損套 120 分鐘冷卻）。

讀狀態檔只收「預設值裡有的鍵」，執行時才長出來的鍵重啟就丟了。兩層：
- 靜態：程式裡任何 AUTO["x"] = …／AUTO.setdefault("x", …) 的 x 都要在預設值裡。
- 行為：獲利平倉 → 存檔 → 重啟 → 冷卻用的是獲利後的短冷卻；未知筆數也讀得回來。

    python3 -m tests.test_auto_persist
"""
import ast
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, ROOT)
from tests import test_r42 as R                                 # noqa: E402
from tests import test_r75 as R75                               # noqa: E402
from tests.fake_exchange import need                            # noqa: E402
from tests.harness import make_case                             # noqa: E402

RESULTS = []
case = make_case(RESULTS)
fresh, open_long, restart = R.fresh, R.open_long, R75.restart


def T():
    return R.T


def runtime_auto_keys():
    """程式裡在執行時寫進 AUTO 的鍵。"""
    src = open(os.path.join(ROOT, "backend", "trader.py"), encoding="utf-8").read()
    keys = set()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Store) and isinstance(n.value, ast.Name) \
                and n.value.id == "AUTO" and isinstance(n.slice, ast.Constant):
            keys.add(n.slice.value)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "setdefault" \
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "AUTO" and n.args \
                and isinstance(n.args[0], ast.Constant):
            keys.add(n.args[0].value)
    return keys


@case("ap-1", "程式裡寫進 AUTO 的每個鍵都在預設值裡（不在的話讀狀態檔時會被丟掉）")
def _():
    ex, clock = fresh()
    keys = runtime_auto_keys()
    need(len(keys) >= 5, f"掃到的鍵太少（前提）：{keys}")
    missing = sorted(k for k in keys if k not in T().AUTO)
    if missing:
        return f"這些鍵執行時才長出來、不在預設值裡，重啟就丟了：{missing}"


@case("ap-2", "獲利平倉後重啟：冷卻照獲利後的短冷卻算，不是虧損後的長冷卻（2Z 實例）")
def _():
    ex, clock = fresh(keep_save=True)
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    T().STATE_FILE = path
    T().AUTO["on"] = True
    T().auto_roll_day()
    open_long(ex)
    ex.mark["XUSDT"] = 110.0
    r = T().close_position("XUSDT")
    need(r.get("ok") and T().STATE["trades"] and (T().STATE["trades"][-1].get("pnl") or 0) > 0, f"應獲利平倉（前提）：{r}")
    clock.sleep(20 * 60)                                        # 20 分鐘後：獲利冷卻 15 分鐘已過、虧損冷卻 120 分鐘還沒
    ok0, why0 = T().auto_can_trade("XUSDT")
    need(ok0 or "冷卻" not in str(why0), f"重啟前就被冷卻擋（前提）：{why0}")
    clock2 = restart(ex, path)
    clock2.now = clock.now                                      # 重啟不會讓時間倒流（模擬的新時鐘從起點開始，對齊回來）
    T().AUTO["on"] = True
    ok, why = T().auto_can_trade("XUSDT")
    if not ok and "冷卻" in str(why):
        return f"重啟後把獲利平倉當成虧損、套長冷卻：{why}"


@case("ap-3", "當日損益未知的筆數重啟後讀得回來（斷路器以每筆 -1R 計，歸零就少算）")
def _():
    ex, clock = fresh(keep_save=True)
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    T().STATE_FILE = path
    T().auto_roll_day()
    T().AUTO["unknownToday"] = 2
    T().save_state()
    restart(ex, path)
    if T().AUTO.get("unknownToday") != 2:
        return f"重啟後當日未知筆數變成 {T().AUTO.get('unknownToday')}（應為 2）"


@case("ap-4", "虧損平倉後重啟（對照）：照樣是虧損後的長冷卻，不會因為修正變成短冷卻")
def _():
    ex, clock = fresh(keep_save=True)
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    T().STATE_FILE = path
    T().AUTO["on"] = True
    T().auto_roll_day()
    open_long(ex)
    ex.mark["XUSDT"] = 97.0
    r = T().close_position("XUSDT")
    need(r.get("ok") and T().STATE["trades"] and (T().STATE["trades"][-1].get("pnl") or 0) < 0, f"應虧損平倉（前提）：{r}")
    clock.sleep(20 * 60)
    clock2 = restart(ex, path)
    clock2.now = clock.now
    T().AUTO["on"] = True
    ok, why = T().auto_can_trade("XUSDT")
    if ok or "虧損後長冷卻" not in str(why):
        return f"虧損平倉後 20 分鐘應還在長冷卻：{ok}、{why}"


@case("ap-5", "損益未知的平倉後重啟：照保守的長冷卻（未知不當成獲利），而且「未知」這個狀態本身讀得回來")
def _():
    ex, clock = fresh(keep_save=True)
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    T().STATE_FILE = path
    T().AUTO["on"] = True
    T().auto_roll_day()
    open_long(ex)
    ex.drop_avg = True
    ex.trades_fail = True
    r = T().close_position("XUSDT")
    need(r.get("ok") and T().STATE["trades"] and T().STATE["trades"][-1].get("pnl") is None, f"損益應記未知（前提）：{r}")
    clock.sleep(20 * 60)
    clock2 = restart(ex, path)
    clock2.now = clock.now
    if "XUSDT" not in (T().AUTO.get("lastCloseWin") or {}):
        return "重啟後「損益未知」這筆的紀錄不見了（分不出是未知還是從沒平過）"
    T().AUTO["on"] = True
    ok, why = T().auto_can_trade("XUSDT")
    if ok or "長冷卻" not in str(why):
        return f"損益未知應照保守的長冷卻：{ok}、{why}"


if __name__ == "__main__":
    fails = [r for r in RESULTS if r[2]]
    for tag, desc, err in RESULTS:
        print(f"{'✓' if not err else '✕'} [{tag}] {desc}" + (f"\n      → {err}" if err else ""))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通過")
    sys.exit(1 if fails else 0)
