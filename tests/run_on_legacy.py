"""拿現在的測試跑舊版程式，失敗分成四類（BINANCE_LESSONS 用法第 5 點 r35、r36）。

    python3 -m tests.run_on_legacy r77

舊版程式存在 tests/legacy/<版本>/backend/（只放 .py）。每輪對照清單前，把這輪修改前的程式存一份進來。
重點看「測試本身崩掉」要是 0——不是 0 就代表測試在舊程式上走不到斷言，前後比對失真。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITES = ["test_r11", "test_r14", "test_r17", "test_r20", "test_r23", "test_r26", "test_r29", "test_r32", "test_r35", "test_r38", "test_r42", "test_r46", "test_r49", "test_r52", "test_r55", "test_r58", "test_r60", "test_r63", "test_cg_cooldown", "test_r71", "test_r73", "test_r75", "test_r77"]


def main(ver):
    src = os.path.join(ROOT, "tests", "legacy", ver, "backend")
    if not os.path.isdir(src):
        print(f"✕ 找不到舊版程式 {src}")
        return 1
    work = tempfile.mkdtemp()
    shutil.copytree(os.path.join(ROOT, "tests"), os.path.join(work, "tests"))
    shutil.copytree(os.path.join(ROOT, "scripts"), os.path.join(work, "scripts"))
    shutil.copytree(src, os.path.join(work, "backend"))
    kinds = {"前提不成立": 0, "被測程式拋錯": 0, "測試本身崩掉": 0, "斷言失敗": 0}
    for s in SUITES:
        out = subprocess.run([sys.executable, "-m", f"tests.{s}"], cwd=work, capture_output=True,
                             text=True, encoding="utf-8", errors="replace").stdout
        summ = re.findall(r"^(\d+)/(\d+) 通過$", out, re.M)
        print(f"  {s}（{ver} 版程式）：" + (f"{summ[-1][0]}/{summ[-1][1]} 通過" if summ else "沒有結果（測試檔本身壞了）"))
        for m in re.finditer(r"^✕ \[([^\]]+)\][^\n]*\n\s+→ ([^\n]*)", out, re.M):
            why = m.group(2)
            k = next((k for k in ("前提不成立", "被測程式拋錯", "測試本身崩掉") if why.startswith(k)), "斷言失敗")
            kinds[k] += 1
            if k == "測試本身崩掉":
                print(f"    ✕ [{m.group(1)}] {why[:100]}")
    print("  分類：" + "、".join(f"{k} {v}" for k, v in kinds.items()))
    return 1 if kinds["測試本身崩掉"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "r77"))
