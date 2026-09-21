"""檢查函式內是否有「在賦值之前就使用」的區域變數。

這類錯誤語法完全合法，只有執行到那一行才會爆（UnboundLocalError），
而下單路徑很少在測試中被完整走過，所以特別危險。
"""
import ast, sys, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
bad = []

class Fn(ast.NodeVisitor):
    def __init__(self, fname, path):
        self.fname, self.path = fname, path
        self.assigned = {}       # name -> 最早賦值行號
        self.used = []           # (name, lineno)

    def visit_Name(self, n):
        if isinstance(n.ctx, ast.Store):
            self.assigned.setdefault(n.id, n.lineno)
        elif isinstance(n.ctx, ast.Load):
            self.used.append((n.id, n.lineno))
        self.generic_visit(n)

    def visit_FunctionDef(self, n):
        pass                     # 巢狀函式另外處理

def check_fn(fn, path, module_names):
    v = Fn(fn.name, path)
    for st in fn.body:
        v.visit(st)
    args = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
    if fn.args.vararg: args.add(fn.args.vararg.arg)
    if fn.args.kwarg: args.add(fn.args.kwarg.arg)
    globals_ = set()
    for n in ast.walk(fn):
        if isinstance(n, (ast.Global, ast.Nonlocal)):
            globals_.update(n.names)
    # 只看「函式層直線流程」的明顯錯序，排除三種語法上先用後賦值但實際正確的情況：
    # 迴圈與 try（後續迭代已賦值）、生成式（目標變數在運算式之後才出現）。
    skip_lines = set()
    for n in ast.walk(fn):
        if isinstance(n, (ast.For, ast.While, ast.Try,
                          ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for sub in ast.walk(n):
                if hasattr(sub, "lineno"):
                    skip_lines.add(sub.lineno)
    comp_targets = set()
    for n in ast.walk(fn):
        if isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for g in n.generators:
                for t in ast.walk(g.target):
                    if isinstance(t, ast.Name):
                        comp_targets.add(t.id)
    for name, line in v.used:
        if (name in args or name in globals_ or name in module_names
                or name in comp_targets or name in dir(__builtins__)):
            continue
        first = v.assigned.get(name)
        if first is not None and line < first and line not in skip_lines:
            bad.append(f"{os.path.basename(path)}:{line} 函式 {fn.name}() 使用 {name}，但它到第 {first} 行才被賦值")

# 第 19 種：檢查本身也要有前提——先對已知有錯的人造函式跑一次，必須抓到（否則「沒報錯」可能只是沒在檢查）
_canary = ast.parse("def canary():\n    print(zz_used_first)\n    zz_used_first = 1\n").body[0]
check_fn(_canary, "canary.py", set())
if not any("zz_used_first" in b for b in bad):
    print("✕ 區域變數檢查的自我驗證失敗：已知有錯的人造函式沒被抓到")
    sys.exit(1)
bad.clear()
checked = [0]

for f in ("backend/main.py", "backend/trader.py", "backend/engine.py"):
    path = os.path.join(ROOT, f)
    tree = ast.parse(open(path).read())
    module_names = set()
    for n in tree.body:
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            for t in ast.walk(n):
                if isinstance(t, ast.Name) and isinstance(t.ctx, ast.Store):
                    module_names.add(t.id)
        elif isinstance(n, (ast.FunctionDef, ast.ClassDef)):
            module_names.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                module_names.add((a.asname or a.name).split(".")[0])
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef):
            check_fn(n, path, module_names)
            checked[0] += 1

if bad:
    print("✕ 區域變數使用早於賦值：")
    for b in bad:
        print("   " + b)
    sys.exit(1)
if checked[0] < 100:
    print(f"✕ 前提不成立：只掃到 {checked[0]} 個函式（路徑錯了？）")
    sys.exit(1)
print(f"✓ 區域變數順序檢查通過（掃了 {checked[0]} 個函式，自我驗證的已知錯誤有抓到）")
