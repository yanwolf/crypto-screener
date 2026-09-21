"""回傳原因的語法樹檢查（BINANCE_LESSONS 第 2 條 r25、r26）。

守衛、移損、掛停損、平倉這些函式的每個出口都要回傳原因，測試才分得出「查不到」「沒了」「還在」（第 16 種）。
  1. 每個 return 都帶值（巢狀函式裡的不算）；
  2. 函式最後不會掉出去——最後一句不是 return 時，Python 一樣回 None（r26）。
     「結束」＝ return／raise，或 if 與 else 兩邊都結束。
第 19 種：檢查本身也要有前提——指定的函式真的都找到、真的數到 return；並用固定人造函式自我驗證。

    python3 scripts/check_returns.py
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGETS = {"backend/trader.py": ["_guard_one", "move_to_breakeven", "place_stop", "_market_close",
                                 "_own_live", "_live_row", "_exit_price", "_close_fills",
                                 "_close_position_impl", "cancel_orphan", "close_position"],
           "backend/main.py": ["trade_handle_safe"]}


def own_returns(fn):
    """fn 自己的 return（不含巢狀函式、lambda 裡的）。"""
    out, stack = [], list(fn.body)
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(n, ast.Return):
            out.append(n)
        stack.extend(ast.iter_child_nodes(n))
    return out


def terminates(block):
    if not block:
        return False
    last = block[-1]
    if isinstance(last, (ast.Return, ast.Raise)):
        return True
    if isinstance(last, ast.If):
        return terminates(last.body) and terminates(last.orelse)
    if isinstance(last, ast.Try):
        return terminates(last.body) and all(terminates(h.body) for h in last.handlers) or terminates(last.finalbody)
    return False


def check_fn(fn):
    probs = [f"第 {r.lineno} 行 return 沒帶值" for r in own_returns(fn) if r.value is None
             or (isinstance(r.value, ast.Constant) and r.value.value is None)]
    if not terminates(fn.body):
        probs.append(f"函式最後（第 {fn.end_lineno} 行）會掉出去，回 None")
    return probs


SELFTEST = [
    ("def f(x):\n    if x:\n        return 'a'\n    return", True),          # 裸 return
    ("def f(x):\n    if x:\n        return 'a'\n    x += 1", True),          # 掉出去
    ("def f(x):\n    if x:\n        return 'a'\n    else:\n        return 'b'", False),
    ("def f(x):\n    def g():\n        return\n    return g", False),       # 巢狀函式的裸 return 不算
    ("def f(x):\n    return None", True),                                    # 明寫 None 也算沒帶原因
    ("def f(x):\n    if x:\n        return 'a'", True),                      # 只有 if 沒 else
]


def selftest():
    wrong = []
    for src, want in SELFTEST:
        fn = ast.parse(src).body[0]
        if bool(check_fn(fn)) != want:
            wrong.append(src.replace("\n", "⏎")[:60])
    return wrong


def main():
    wrong = selftest()
    if wrong:
        print("✕ 回傳檢查自我驗證失敗：" + "；".join(wrong))
        return 1
    bad, found, nret = [], 0, 0
    for rel, names in TARGETS.items():
        tree = ast.parse(open(os.path.join(ROOT, rel), encoding="utf-8").read())
        fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        for name in names:
            fn = fns.get(name)
            if fn is None:
                bad.append(f"{rel} 找不到函式 {name}（前提：要檢查的函式真的存在）")
                continue
            found += 1
            k = len(own_returns(fn))
            nret += k
            if k < 2:
                # 第 19 種（r29）：每個函式都要真的解析到多個 return，只看總數會被別的函式撐過去
                bad.append(f"{name} 只解析到 {k} 個 return（前提：這種函式都有多個出口）")
            bad += [f"{name}：{p}" for p in check_fn(fn)]
    want = sum(len(v) for v in TARGETS.values())
    if found != want or nret < found:
        bad.append(f"前提不成立：只找到 {found}/{want} 個函式、{nret} 個 return")
    for b in bad:
        print("✕ " + b)
    print(("✓" if not bad else "✕") + f" 回傳檢查：{len(SELFTEST)} 組人造函式自我驗證通過；{found} 個函式、{nret} 個 return"
          f"（每個函式至少 2 個 return），問題 {len(bad)} 項")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
