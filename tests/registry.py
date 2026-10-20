"""測試檔登記表（2026-10-20，r101 對照 pump-dump-hunter）：所有「逐檔掃」的工具都從這裡拿清單。

- 全部 tests/test_*.py 自動列入——不靠人記得加（r97 的 test_r97 漏登記兩輪、索引檢查只掃 test_r*.py
  讓名字不是 test_rNN 的 5 個檔從沒被掃過，都是同一類問題）。
- 某個工具不適用某個檔案，要寫在 SKIP 裡、附理由；理由會印出來，也審得到。SKIP 裡寫了不存在的檔名會報。
- 工具裡不准再寫死測試檔清單：check_tests 會掃工具原始碼，看到兩個以上 test_… 字串排成的清單就報。

    python3 -m tests.registry        # 列出每個工具掃哪些檔、跳過哪些、為什麼
"""
import glob
import os

HERE = os.path.dirname(os.path.abspath(__file__))

_OLD_STYLE = "舊式測試（最上層一路往下跑、斷言是 check(…)），沒有 @case 案例函式——這個工具以案例為單位"
SKIP = {
    "mutation": {"test_lessons.py": _OLD_STYLE, "test_parity.py": _OLD_STYLE},
    "legacy": {"test_lessons.py": _OLD_STYLE, "test_parity.py": _OLD_STYLE},
    "newapi": {"test_lessons.py": _OLD_STYLE, "test_parity.py": _OLD_STYLE},
    "indexing": {},
    "negative": {},           # 否定句檢查：@case 與舊式各有一套分析，全部都掃
}
TOOLS = tuple(SKIP)


def all_files():
    return sorted(os.path.basename(p) for p in glob.glob(os.path.join(HERE, "test_*.py")))


def style(fname):
    """case（@case 框架）／script（舊式：最上層 check(…)）／unknown。"""
    src = open(os.path.join(HERE, fname), encoding="utf-8").read()
    if "@case(" in src:
        return "case"
    if any(line.startswith("check(") for line in src.splitlines()) and "fresh(" in src:
        return "script"
    return "unknown"


def files_for(tool):
    """這個工具要掃的測試檔（檔名，含 .py）。"""
    if tool not in SKIP:
        raise KeyError(f"登記表沒有這個工具：{tool}")
    return [f for f in all_files() if f not in SKIP[tool]]


def modules_for(tool):
    return [f[:-3] for f in files_for(tool)]


def problems():
    """登記表本身的問題：SKIP 寫了不存在的檔、認不得寫法的測試檔。"""
    out, have = [], set(all_files())
    for tool, skips in SKIP.items():
        for f in skips:
            if f not in have:
                out.append(f"登記表：{tool} 跳過的 {f} 不存在（被刪掉或改名，請把這行拿掉）")
    for f in sorted(have):
        if style(f) == "unknown":
            out.append(f"登記表：{f} 認不得的寫法（不是 @case 框架、也不是最上層 check(…)），否定句等檢查都掃不到")
    return out


if __name__ == "__main__":
    for t in TOOLS:
        print(f"{t}：掃 {len(files_for(t))} 支" + "".join(f"\n  跳過 {f}：{why}" for f, why in SKIP[t].items()))
    for p in problems():
        print("✕ " + p)
