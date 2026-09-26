"""測試呼叫新介面前要先確認存在（用法第 5 點 r38、r78、r79）——靜態檢查。

新介面＝最舊那版舊程式（tests/legacy/ 裡版本最小的）的 backend/*.py 模組層級沒有的名稱。
測試裡任何一處 `T().X`、`T.X`、`M.X`、`FX.X`、`T().a.X`（鏈的第一個屬性）用到新介面 X，
**那一處**之前（同一個函式裡，或它先呼叫的同檔輔助函式裡）要有守住**同一個屬性**的 hasattr／getattr：
不是整個檔案有一個就好（r78 pump-dump-hunter），也不是守了別的屬性就好（r79 gold-scalper）。
輔助函式與案例一樣檢查。守護寫在用法之後不算。

    python3 -m tests.check_newapi
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_BASES = {"T": "trader", "M": "main", "FX": "fake_exchange", "R": "test_r42", "R55": "test_r55", "R71": "test_r71",
                "R73": "test_r73", "R75": "test_r75"}
SUITES = ["test_r42.py", "test_r46.py", "test_r49.py", "test_r52.py", "test_r55.py", "test_r58.py", "test_r60.py",
          "test_r63.py", "test_cg_cooldown.py", "test_r71.py", "test_r73.py", "test_r75.py", "test_r77.py"]


def legacy_names():
    """最舊那版舊程式每個模組的模組層級名稱。"""
    leg = os.path.join(ROOT, "tests", "legacy")
    vers = sorted(d for d in os.listdir(leg) if os.path.isdir(os.path.join(leg, d)))
    if not vers:
        raise SystemExit("✕ tests/legacy/ 下沒有舊版程式")
    oldest = vers[0]
    out = {}
    for f in os.listdir(os.path.join(leg, oldest, "backend")):
        if not f.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(leg, oldest, "backend", f), encoding="utf-8").read())
        names = set()
        body = []
        for n in tree.body:                     # 模組層級的 try／if 裡面的定義也算（main 的 import trader 在 try 裡）
            body.append(n)
            if isinstance(n, (ast.Try, ast.If)):
                body += [x for x in ast.walk(n) if isinstance(x, (ast.FunctionDef, ast.ClassDef, ast.Assign, ast.Import, ast.ImportFrom))]
        for n in body:
            if isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                names.add(n.name)
            elif isinstance(n, ast.Assign):
                for t in n.targets:
                    for x in ast.walk(t):
                        if isinstance(x, ast.Name):
                            names.add(x.id)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    names.add((a.asname or a.name).split(".")[0])
        out[f[:-3]] = names
    return oldest, out


def base_of(node):
    """屬性鏈的根：`T()` → "T"、`T` → "T"、`T().a.b` → ("T", 第一個屬性)。回傳 (根名稱, 第一個屬性名稱) 或 None。"""
    chain = []
    cur = node
    while isinstance(cur, ast.Attribute):
        chain.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Call) and isinstance(cur.func, ast.Name):
        root = cur.func.id
    elif isinstance(cur, ast.Name):
        root = cur.id
    else:
        return None
    if root not in MODULE_BASES or not chain:
        return None
    return root, chain[-1]


def guards_in(fn):
    """函式裡的守護：{(根, 屬性): 行號}（hasattr／getattr 的第一個參數是模組根、第二個是字串）。"""
    g = {}
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("hasattr", "getattr") and len(n.args) >= 2:
            b = base_of(ast.Attribute(value=n.args[0], attr="_", ctx=ast.Load())) if not isinstance(n.args[0], (ast.Name, ast.Call)) else None
            root = None
            a0 = n.args[0]
            if isinstance(a0, ast.Call) and isinstance(a0.func, ast.Name):
                root = a0.func.id
            elif isinstance(a0, ast.Name):
                root = a0.id
            elif b:
                root = b[0]
            if root in MODULE_BASES and isinstance(n.args[1], ast.Constant) and isinstance(n.args[1].value, str):
                key = (root, n.args[1].value)
                g[key] = min(g.get(key, n.lineno), n.lineno)
    return g


def check_file(path, legacy):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    guards = {name: guards_in(fn) for name, fn in funcs.items()}
    # 同名的案例函式（都叫 _）：逐一處理，不用名稱查
    all_fns = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    bad = []
    for fn in all_fns:
        own = guards_in(fn)
        # 先呼叫的輔助函式帶進來的守護（一層）：以呼叫那一行當守護的行號
        inherited = {}
        seen = set()
        for n in ast.walk(fn):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in guards and n.func.id != fn.name:
                for key in guards[n.func.id]:
                    inherited[key] = min(inherited.get(key, n.lineno), n.lineno)
        for n in ast.walk(fn):
            if not isinstance(n, ast.Attribute) or isinstance(n.ctx, ast.Store):
                continue                          # 指定屬性（M.push_all = …）是在換掉，不是在用
            b = base_of(n)
            if not b:
                continue
            root, attr = b
            mod = MODULE_BASES[root]
            if mod not in legacy or attr in legacy[mod] or attr.startswith("__"):
                continue                          # 舊介面，或不是被測程式的模組（測試模組自己的名稱不管）
            line = n.lineno
            if (line, root, attr) in seen:
                continue                          # 同一鏈的每一層都會走到，只報一次
            seen.add((line, root, attr))
            g_line = own.get((root, attr))
            if g_line is None or g_line > line:
                g_line = inherited.get((root, attr))
            if g_line is None or g_line > line:
                bad.append(f"{os.path.basename(path)}:{line} 用到新介面 {root}.{attr}（最舊舊版沒有），這一處之前沒有守住同一個屬性的 hasattr／getattr")
    return bad


SELFTEST = [
    # (原始碼, 應報幾處)
    ("def _():\n    x = T().newfn()\n", 1),                                              # 沒守
    ("def _():\n    need(hasattr(T(), 'newfn'), 'p')\n    x = T().newfn()\n", 0),        # 守了同一個、在之前
    ("def _():\n    x = T().newfn()\n    need(hasattr(T(), 'newfn'), 'p')\n", 1),        # 守在之後
    ("def _():\n    need(hasattr(T(), 'other'), 'p')\n    x = T().newfn()\n", 1),        # 守了別的屬性
    ("def helper():\n    need(hasattr(T(), 'newfn'), 'p')\n\ndef _():\n    helper()\n    x = T().newfn()\n", 0),   # 輔助函式守的
    ("def helper():\n    x = T().newfn()\n", 1),                                          # 輔助函式自己用到也要守
    ("def _():\n    x = M.newobj.run()\n", 1),                                            # 兩層：M.newobj 是新的
    ("def _():\n    need(hasattr(M, 'newobj'), 'p')\n    x = M.newobj.run()\n", 0),
    ("def _():\n    x = T().oldfn()\n", 0),                                               # 舊介面不用守
    ("def _():\n    x = getattr(T(), 'newfn', None)\n    if x:\n        x()\n", 0),        # getattr 本身就是守護
]


def selftest():
    legacy = {"trader": {"oldfn"}, "main": {"oldfn"}}
    wrong = []
    for src, want in SELFTEST:
        p = os.path.join(ROOT, "tests", "_newapi_selftest.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write(src)
        try:
            got = len(check_file(p, legacy))
        finally:
            os.remove(p)
        if got != want:
            wrong.append(f"{src.strip().splitlines()[-1]} → 報 {got} 處（應 {want}）")
    return wrong


def main():
    wrong = selftest()
    if wrong:
        print("✕ 新介面檢查器自我驗證失敗：\n  " + "\n  ".join(wrong))
        return 1
    print(f"✓ 新介面檢查器自我驗證：{len(SELFTEST)} 組固定人造資料全部判對")
    oldest, legacy = legacy_names()
    bad = []
    for s in SUITES:
        bad += check_file(os.path.join(ROOT, "tests", s), legacy)
    if bad:
        print("✕ 新介面檢查（對照最舊舊版 " + oldest + "）：\n  " + "\n  ".join(bad))
        return 1
    print(f"✓ 新介面檢查：{len(SUITES)} 支測試，對照最舊舊版 {oldest}，每一處用到新介面都在之前守住同一個屬性")
    return 0


if __name__ == "__main__":
    sys.exit(main())
