"""測試本身的靜態檢查（BINANCE_LESSONS 用法第 5 點）。

1. 測錯方式第 14 種：每個案例的第一個動作要是 fresh()——從乾淨的模組狀態開始，
   不讓前一個案例留在共用物件（trader 模組的 STATE、計數器）上的狀態帶進來。
2. 否定句斷言要有前提（r19）：案例描述是否定句（「不能」「沒有」「不送」「仍在」…）時，
   程式什麼都沒做也會成立，所以案例裡必須有 need(...) 前提斷言。

    python3 -m tests.check_tests
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ["test_r11.py", "test_r14.py", "test_r17.py", "test_r20.py"]
NEG = re.compile(r"不能|不可|沒有|沒送|不送|不撤|不掛|不補|不動|不算|不留|不發|仍在|不再|不是|不影響|照樣|照常")


def cases(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        for d in fn.decorator_list:
            if isinstance(d, ast.Call) and getattr(d.func, "id", "") == "case" and len(d.args) >= 2:
                yield d.args[0].value, d.args[1].value, fn


def first_call_is_fresh(fn):
    body = [s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(getattr(s, "value", None), ast.Constant))]
    if not body:
        return False
    s = body[0]
    call = s.value if isinstance(s, (ast.Assign, ast.Expr)) else None
    return isinstance(call, ast.Call) and getattr(call.func, "id", "") == "fresh"


def has_need(fn):
    return any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "need"
               for n in ast.walk(fn))


def main():
    bad = []
    total = neg = 0
    for f in FILES:
        for tag, desc, fn in cases(os.path.join(HERE, f)):
            total += 1
            if not first_call_is_fresh(fn):
                bad.append(f"{f} [{tag}] 第一個動作不是 fresh()（第 14 種：可能帶進前一個案例的狀態）")
            if NEG.search(desc):
                neg += 1
                if not has_need(fn):
                    bad.append(f"{f} [{tag}] 否定句斷言沒有前提 need(...)：{desc[:40]}")
    for b in bad:
        print("✕ " + b)
    print(f"{'✕' if bad else '✓'} 測試靜態檢查：{total} 個案例，其中否定句 {neg} 個；問題 {len(bad)} 項")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
