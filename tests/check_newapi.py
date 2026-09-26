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
          "test_r63.py", "test_cg_cooldown.py", "test_r71.py", "test_r73.py", "test_r75.py", "test_r77.py", "test_auto_persist.py"]


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


NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _walk_no_nested(node):
    """走訪但不進巢狀的函式／lambda／類別（定義不等於執行，r81）。"""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        for c in ast.iter_child_nodes(n):
            if not isinstance(c, NESTED):
                stack.append(c)


def _root_of(expr):
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
        return expr.func.id
    if isinstance(expr, ast.Name):
        return expr.id
    return None


def _guard_keys(node):
    """這段運算式／敘述裡（不含巢狀定義）出現的守護：{(根, 屬性)}。"""
    out = set()
    if isinstance(node, NESTED):
        return out                                  # 定義一個函式不等於執行它
    for n in _walk_no_nested(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("hasattr", "getattr") and len(n.args) >= 2:
            root = _root_of(n.args[0])
            if root in MODULE_BASES and isinstance(n.args[1], ast.Constant) and isinstance(n.args[1].value, str):
                out.add((root, n.args[1].value))
    return out


def _is_guard(n, key):
    return (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("hasattr", "getattr")
            and len(n.args) >= 2 and _root_of(n.args[0]) == key[0]
            and isinstance(n.args[1], ast.Constant) and n.args[1].value == key[1])


def _implies(test, key, truth):
    """條件的值是 truth 時，能不能確定 key 存在（r83：看正負，不是看有沒有出現過）。
    hasattr／getattr 為真 ⇒ 在；not 反向；and 為真 ⇒ 任一；and 為假 ⇒ 推不出；or 為真 ⇒ 全部；or 為假 ⇒ 任一為假就推得出。"""
    if _is_guard(test, key):
        return truth
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return _implies(test.operand, key, not truth)
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        return truth and any(_implies(v, key, True) for v in test.values)
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        return all(_implies(v, key, True) for v in test.values) if truth else any(_implies(v, key, False) for v in test.values)
    if isinstance(test, ast.Call) and isinstance(test.func, ast.Name) and test.func.id == "callable" and test.args:
        return truth and _implies(test.args[0], key, True)
    return False


EXITS = (ast.Return, ast.Raise, ast.Continue, ast.Break)


def _exits(block):
    """這個區塊一定離開（最後一句是 return／raise／continue／break）。"""
    return bool(block) and isinstance(block[-1], EXITS)


def _stops(stmt, key, helpers):
    """這一句執行完之後，key 一定存在——只認擋得住的寫法（r83：只是出現過守護不算，前提失敗不會停下來就擋不住）：
    need(守護)（本專案的 need 失敗會丟例外）、assert 守護、`if 反向守護: 離開`、`if 守護: … else: 離開`、
    with／try（沒有 except）區塊裡的這些、先呼叫的同檔輔助函式最上層有這些。"""
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        c = stmt.value
        if isinstance(c.func, ast.Name) and c.func.id == "need" and c.args and _implies(c.args[0], key, True):
            return True
    if isinstance(stmt, ast.Assert) and _implies(stmt.test, key, True):
        return True
    if isinstance(stmt, ast.If):
        if _exits(stmt.body) and _implies(stmt.test, key, False):
            return True
        if stmt.orelse and _exits(stmt.orelse) and _implies(stmt.test, key, True):
            return True
    if isinstance(stmt, ast.With):
        return any(_stops(s, key, helpers) for s in stmt.body)
    if isinstance(stmt, ast.Try) and not stmt.handlers:
        return any(_stops(s, key, helpers) for s in stmt.body)
    for n in _walk_no_nested(stmt):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and key in helpers.get(n.func.id, set()):
            return True
    return False


def _protected(use, key, scope, parents, helpers):
    """照執行順序判斷：往上走到自己的範圍為止（不越過函式邊界，r81：前一個情境的守護不算）。"""
    child, node = use, parents.get(use)
    while node is not None:
        if isinstance(node, ast.IfExp):
            if child is node.body and _implies(node.test, key, True):
                return True
            if child is node.orelse and _implies(node.test, key, False):
                return True
        if isinstance(node, ast.If):
            if child in node.body and _implies(node.test, key, True):
                return True
            if child in node.orelse and _implies(node.test, key, False):
                return True
        if isinstance(node, ast.BoolOp) and child in node.values:
            before = node.values[:node.values.index(child)]
            if isinstance(node.op, ast.And) and any(_implies(v, key, True) for v in before):
                return True
            if isinstance(node.op, ast.Or) and any(_implies(v, key, False) for v in before):
                return True
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if isinstance(block, list) and child in block:
                if any(_stops(prev, key, helpers) for prev in block[:block.index(child)]):
                    return True
        if node is scope:
            return False
        child, node = node, parents.get(node)
    return False


def _helper_stops(fn):
    """輔助函式最上層擋得住的守護：{(根, 屬性)}（呼叫它之後就一定在）。"""
    keys = set()
    for s in fn.body:
        for k in _guard_keys(s):
            if _stops(s, k, {}):
                keys.add(k)
    return keys


def check_file(path, legacy):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    helpers = {n.name: _helper_stops(n) for n in tree.body if isinstance(n, ast.FunctionDef)}
    parents = {}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            parents[c] = n
    bad, seen = [], set()
    top_level = [n for n in tree.body if not isinstance(n, NESTED)]
    scopes = [(n, n) for n in tree.body if isinstance(n, ast.FunctionDef)]
    scopes += [(tree, s) for s in top_level]                   # 模組最上層也是一個範圍（r81）
    for scope, root_node in scopes:
        local = set()
        if isinstance(root_node, ast.FunctionDef):
            local = {a.arg for a in root_node.args.args}
            for n in _walk_no_nested(root_node):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                    local.add(n.id)                            # 跟別名同名的區域變數不是那個模組（r81）
        for n in _walk_no_nested(root_node):
            if not isinstance(n, ast.Attribute) or isinstance(n.ctx, ast.Store):
                continue                          # 指定屬性（M.push_all = …）是在換掉，不是在用
            b = base_of(n)
            if not b:
                continue
            root, attr = b
            if root in local:
                continue
            mod = MODULE_BASES[root]
            if mod not in legacy or attr in legacy[mod] or attr.startswith("__"):
                continue                          # 舊介面，或不是被測程式的模組（測試模組自己的名稱不管）
            if (n.lineno, root, attr) in seen:
                continue                          # 同一鏈的每一層都會走到，只報一次
            seen.add((n.lineno, root, attr))
            if not _protected(n, (root, attr), scope, parents, helpers):
                bad.append(f"{os.path.basename(path)}:{n.lineno} 用到新介面 {root}.{attr}（最舊舊版沒有），"
                           f"執行到這一處之前沒有守住同一個屬性的 hasattr／getattr")
    return bad


SELFTEST = [
    # (原始碼, 應報幾處)
    ("def _():\n    x = T().newfn()\n", 1),                                              # 沒守
    ("def _():\n    need(hasattr(T(), 'newfn'), 'p')\n    x = T().newfn()\n", 0),        # 守了同一個、在之前
    ("def _():\n    x = T().newfn()\n    need(hasattr(T(), 'newfn'), 'p')\n", 1),        # 守在之後
    ("def _():\n    need(hasattr(T(), 'other'), 'p')\n    x = T().newfn()\n", 1),        # 守了別的屬性
    ("def helper():\n    need(hasattr(T(), 'newfn'), 'p')\n\ndef _():\n    helper()\n    x = T().newfn()\n", 0),   # 先呼叫的輔助函式守的
    ("def helper():\n    x = T().newfn()\n", 1),                                          # 輔助函式自己用到也要守
    ("def _():\n    x = M.newobj.run()\n", 1),                                            # 兩層：M.newobj 是新的
    ("def _():\n    need(hasattr(M, 'newobj'), 'p')\n    x = M.newobj.run()\n", 0),
    ("def _():\n    x = T().oldfn()\n", 0),                                               # 舊介面不用守
    ("def _():\n    x = getattr(T(), 'newfn', None)\n    if x:\n        x()\n", 0),        # getattr 本身就是守護
    # r80、r81：照執行順序
    ("def _():\n    x = (T().newfn\n         if hasattr(T(), 'newfn') else None)\n", 0),   # 條件運算式、跨行寫
    ("def _():\n    x = None if hasattr(T(), 'newfn') else T().newfn\n", 1),             # 用法在 else 那一邊
    ("def _():\n    if hasattr(T(), 'newfn'):\n        pass\n    else:\n        x = T().newfn\n", 1),   # if 的 else 區塊
    ("def _():\n    x = hasattr(T(), 'newfn') or T().newfn\n", 1),                        # or 的後段
    ("def _():\n    x = hasattr(T(), 'newfn') and T().newfn\n", 0),                       # and 的後段
    ("def _():\n    if not hasattr(T(), 'newfn'):\n        x = T().newfn\n", 1),         # not 底下
    ("def _():\n    if not hasattr(T(), 'newfn'):\n        return 'x'\n    y = T().newfn\n", 0),  # 先排除再用
    ("def _():\n    need(hasattr(T(), 'newfn'), 'p')\n\ndef _():\n    x = T().newfn\n", 1),   # 前一個情境守過不算
    ("def helper():\n    need(hasattr(T(), 'newfn'), 'p')\n\ndef _():\n    x = T().newfn\n", 1),  # 定義了沒呼叫不算
    ("def _():\n    def inner():\n        need(hasattr(T(), 'newfn'), 'p')\n    x = T().newfn\n", 1),  # 巢狀定義裡的守護不算
    ("def _():\n    for M in []:\n        x = M.newobj\n", 0),                               # 跟別名同名的區域變數
    ("x = T().newfn\n", 1),                                                                  # 模組最上層也檢查
    # r83：寫在前面的敘述只認擋得住的；條件看正負
    ("def _():\n    hasattr(T(), 'newfn')\n    x = T().newfn\n", 1),                      # 光寫、沒停
    ("def _():\n    ok = hasattr(T(), 'newfn')\n    x = T().newfn\n", 1),                 # 存進變數沒用
    ("def _():\n    if not hasattr(T(), 'newfn'):\n        print('x')\n    x = T().newfn\n", 1),   # 沒離開
    ("def _():\n    check('p', hasattr(T(), 'newfn'))\n    x = T().newfn\n", 1),         # 不會停的前提
    ("def _():\n    assert hasattr(T(), 'newfn')\n    x = T().newfn\n", 0),
    ("def _():\n    if hasattr(T(), 'newfn'):\n        pass\n    else:\n        return 'x'\n    x = T().newfn\n", 0),
    ("def _():\n    if not hasattr(T(), 'newfn'):\n        pass\n    else:\n        x = T().newfn\n", 0),   # else 那邊、條件是反向
    ("def _():\n    x = None if not hasattr(T(), 'newfn') else T().newfn\n", 0),
    ("def _():\n    x = not hasattr(T(), 'newfn') or T().newfn\n", 0),
    ("def _():\n    x = T().newfn if not hasattr(T(), 'newfn') else None\n", 1),
    ("def _():\n    x = not hasattr(T(), 'newfn') and T().newfn\n", 1),
    ("def _():\n    with open('x') as f:\n        need(hasattr(T(), 'newfn'), 'p')\n    x = T().newfn\n", 0),
    ("def h():\n    ok = hasattr(T(), 'newfn')\n\ndef _():\n    h()\n    x = T().newfn\n", 1),   # 輔助函式裡只是出現過
    ("def _():\n    need(hasattr(T(), 'a') and hasattr(T(), 'newfn'), 'p')\n    x = T().newfn\n", 0),
    ("def _():\n    need(hasattr(T(), 'a') or hasattr(T(), 'newfn'), 'p')\n    x = T().newfn\n", 1),   # or 為真推不出
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
