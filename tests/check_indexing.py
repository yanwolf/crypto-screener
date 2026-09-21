"""測試裡「先索引、沒先確認有東西」的寫法（BINANCE_LESSONS 用法第 5 點 r35）。

對清單取 [0]／[-1] 之前，同一個案例裡要先有 need(...)／if not …／len(…) 提到那個清單；
否則突變或舊版程式讓那個清單是空的時候，測試本身崩掉（IndexError），那一項後面的斷言全部沒檢查。
排除不會空的寫法：迴圈變數的 tuple 取值（c[1]）、帶非空預設值（.get(k, [0])[0]、x or [None]）、
常數清單、同一行短路保護（len(x) != 1 or x[0]、x and x[0]、not x or x[0]）。

    python3 -m tests.check_indexing
"""
import ast
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOOPVARS = {"c", "x", "o", "a", "r", "f", "p_", "t", "l", "m"}


def scan(src, fname="<src>"):
    tree = ast.parse(src)
    lines = src.split("\n")
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    out = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Load)):
            continue
        sl = n.slice
        v = sl.value if isinstance(sl, ast.Constant) else (
            -sl.operand.value if isinstance(sl, ast.UnaryOp) and isinstance(sl.operand, ast.Constant) else None)
        if v not in (0, -1):
            continue
        base = ast.get_source_segment(src, n.value) or ""
        if isinstance(n.value, ast.Name) and n.value.id in LOOPVARS:
            continue
        if isinstance(n.value, (ast.List, ast.Tuple)):
            continue
        if base in ("T._algo_supported",) or base.startswith("ex.pos[") or base.startswith("ex2.pos["):
            continue                                  # 固定一個元素的旗標、模擬交易所固定 [數量, 均價] 的部位紀錄
        fn0 = next((f for f in fns if f.lineno <= n.lineno <= f.end_lineno), None)
        if isinstance(n.value, ast.Name) and fn0 and any(
                isinstance(s, ast.Assign) and any(getattr(t, "id", "") == n.value.id for t in s.targets)
                and isinstance(s.value, ast.List) for s in ast.walk(fn0)):
            continue                                  # 函式裡寫死的常數清單
        if isinstance(n.value, ast.BoolOp) and isinstance(n.value.op, ast.Or):
            continue                                  # x or [None]
        if isinstance(n.value, ast.Call) and getattr(n.value.func, "attr", "") == "get" and len(n.value.args) >= 2:
            continue                                  # .get(k, [0])
        line = lines[n.lineno - 1]
        key = base if len(base) < 40 else base[:40]
        if any(g in line for g in (f"{key} and ", f"not {key} or")) or \
                (f"len({key})" in line and line.index(f"len({key})") < line.index(key + ("[" if not key.endswith("]") else ""))):
            continue                                  # 同一行先確認過（x and x[0]、len(x) >= 2 and x[-1]…）
        fn = next((f for f in fns if f.lineno <= n.lineno <= f.end_lineno), None)
        pre = lines[(fn.lineno - 1 if fn else 0):n.lineno - 1]
        k = key.split("[")[0].split("(")[0].strip()
        if any(("need(" in l or "if not" in l or "len(" in l or "check(" in l) and k in l for l in pre):
            continue
        out.append((fname, n.lineno, base[:60]))
    return out


SELFTEST = [
    ("def f(T):\n    return T.STATE['trades'][-1]\n", 1),
    ("def f(T):\n    need(T.STATE['trades'], 'x')\n    return T.STATE['trades'][-1]\n", 0),
    ("def f(ex):\n    return ex.pos.get(('X', 'L'), [0])[0]\n", 0),
    ("def f(h):\n    if len(h) != 1 or 'a' not in h[0]['text']:\n        return 1\n", 0),
    ("def f(y):\n    return (y or [None])[-1]\n", 0),
    ("def f(ex):\n    need(len(ex.fills) >= 2 and ex.fills[-1]['a'], 'x')\n", 0),
]


def main():
    for src, want in SELFTEST:
        if len(scan(src)) != want:
            print(f"✕ 索引檢查的自我驗證失敗：{src.splitlines()[1].strip()} 預期 {want} 處")
            return 1
    files = sorted(glob.glob(os.path.join(HERE, "test_r*.py"))) + [os.path.join(HERE, "test_lessons.py")]
    if len(files) < 8:
        print(f"✕ 前提不成立：只找到 {len(files)} 支測試檔")
        return 1
    bad = []
    for f in files:
        bad += scan(open(f, encoding="utf-8").read(), os.path.basename(f))
    for b in bad:
        print(f"✕ {b[0]}:{b[1]} 沒先確認就取 {b[2]}")
    print(("✓" if not bad else "✕") + f" 索引檢查：{len(SELFTEST)} 組人造資料自我驗證通過；掃了 {len(files)} 支測試，問題 {len(bad)} 處")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
