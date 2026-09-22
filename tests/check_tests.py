"""測試本身的靜態檢查（BINANCE_LESSONS 用法第 5 點）。

1. 測錯方式第 14 種：每個案例的第一個動作要是 fresh()（重建乾淨的模組狀態）。
2. 否定句斷言要有前提（r19）。**看斷言本身，不看描述**（第 15 種、r22）：
   本專案的斷言寫法是 `if 條件: return "失敗訊息"`——條件成立就失敗。
   「程式什麼都沒做時條件也不成立」的斷言就是否定句（例如 `if stops_sent(...)`：沒送才通過）。
   一個案例裡**所有**失敗分支都是否定句時，程式什麼都沒做整個案例就會通過 → 必須有 need(...) 前提。
   組合條件：`if A or B` 要求 A、B 都不成立，全部否定才算否定；`if A and B` 任一否定就算。

    python3 -m tests.check_tests
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ["test_r11.py", "test_r14.py", "test_r17.py", "test_r20.py", "test_r23.py", "test_r26.py", "test_r29.py", "test_r32.py", "test_r35.py", "test_r38.py", "test_r42.py", "test_r46.py", "test_r49.py", "test_r52.py", "test_r55.py", "test_r58.py"]
STATEY = ("STATE", "positions", "pending", "leftovers", ".pos", "ex.algo")


def cases(src):
    tree = ast.parse(src)
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        for d in fn.decorator_list:
            if isinstance(d, ast.Call) and getattr(d.func, "id", "") == "case" and len(d.args) >= 2:
                yield d.args[0].value, d.args[1].value, fn


def negative(cond, src):
    """失敗條件 cond：程式什麼都沒做時是否「不成立」（＝這條斷言會空跑通過）。"""
    if isinstance(cond, ast.BoolOp):
        parts = [negative(v, src) for v in cond.values]
        return all(parts) if isinstance(cond.op, ast.Or) else any(parts)
    if isinstance(cond, ast.UnaryOp) and isinstance(cond.op, ast.Not):
        # 第 25 種（r44 pump-dump-hunter）：if not all(…)——清單是空的時 all() 成立，這條永遠不失敗 → 藏起來的否定句
        o = cond.operand
        if isinstance(o, ast.Call) and isinstance(o.func, ast.Name) and o.func.id == "all":
            return True
        return False                                  # if not X：要求 X 發生 → 正向
    if isinstance(cond, (ast.Call, ast.Name, ast.Attribute, ast.Subscript)):
        return True                                   # if X：要求 X 沒發生 → 否定
    if isinstance(cond, ast.Compare) and len(cond.ops) == 1:
        op, left, right = cond.ops[0], cond.left, cond.comparators[0]
        rs = ast.get_source_segment(src, right) or ""
        ls = ast.get_source_segment(src, left) or ""
        if isinstance(op, ast.NotIn):
            return any(k in rs for k in STATEY)       # 「還在帳上」之類：什麼都沒做也成立
        if isinstance(op, ast.In):
            return False
        if isinstance(op, ast.IsNot):
            return True
        if isinstance(op, ast.Is):
            return False
        baseline = any(n in rs or n in ls for n in ("n0", "m0", "c0", "before", "stops_before"))
        if isinstance(op, ast.NotEq) and baseline:
            return True                               # if 數量 != 之前：要求沒變 → 否定
        if isinstance(op, (ast.Gt, ast.GtE)) and isinstance(right, ast.Constant) and right.value in (0, 0.0) \
                and not ls.startswith("abs("):
            return True                               # if 找到的數量 > 0：要求沒找到 → 否定
        return False                                  # 其他數值比對：要求特定值 → 正向
    return False


def fail_branches(fn):
    """案例裡所有「if 條件: return 字串」的條件（含巢狀）。"""
    out = []
    for n in ast.walk(fn):
        if isinstance(n, ast.If) and n.body and isinstance(n.body[0], ast.Return) and n.body[0].value is not None:
            out.append(n.test)
    return out


def infra_touches_program(fn):
    """infra 項在 fresh() 之後不能碰程式：不能讀寫 T.（trader）、M.（main）的任何東西（r28：防止拿標記繞過檢查）。"""
    body = [s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(getattr(s, "value", None), ast.Constant))][1:]
    return sorted({f"{n.value.id}.{n.attr}" for s in body for n in ast.walk(s)
                   if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in ("T", "M")})


def is_infra(fn):
    return any(isinstance(d, ast.Call) and getattr(d.func, "id", "") == "case"
               and any(k.arg == "infra" and getattr(k.value, "value", False) is True for k in d.keywords)
               for d in fn.decorator_list)


def has_need(fn):
    return any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "need" for n in ast.walk(fn))


SELFTEST = [
    # (人造的案例內容, 應不應該被報出) —— 固定資料，不拿當下的測試檔來驗（r23）
    ("if stops_sent(ex, n0):\n        return 'x'", True),             # 否定句、沒前提
    ("if not stops_sent(ex, n0):\n        return 'x'", False),        # 正向
    ("if q > 0:\n        return 'x'", True),                          # 第 15 種：看似正向描述的否定句
    ("if abs(q - 1) > 0:\n        return 'x'", False),                # 數值比對：正向
    ("if a() or not b():\n        return 'x'", False),                # or 裡有正向 → 不空跑
    ("if a() and not b():\n        return 'x'", True),                # and 裡有否定 → 可能空跑
    ("if 'X' not in T.STATE['positions']:\n        return 'x'", True),
    ("if 'busy' not in text:\n        return 'x'", False),
    ("need(True, 'p')\n    if stops_sent(ex, n0):\n        return 'x'", False),  # 有前提
    ("if len(market_calls(ex)) != n0:\n        return 'x'", True),
    ("if len(booked) != 1:\n        return 'x'", False),
    ("if not all(p['r'] for p in posts(ex)):\n        return 'x'", True),     # 第 25 種：空清單時 all() 成立
    ("need(posts(ex), 'p')\n    if not all(p['r'] for p in posts(ex)):\n        return 'x'", False),  # 先確認非空
]


def selftest():
    wrong = []
    for body, want in SELFTEST:
        src = f"@case('t', 'd')\ndef _():\n    ex = fresh()\n    {body}\n"
        fn = next(c[2] for c in cases(src))
        br = fail_branches(fn)
        got = bool(br) and all(negative(x, src) for x in br) and not has_need(fn)
        if got != want:
            wrong.append(f"{body.splitlines()[-2 if 'need' in body else 0].strip()}：預期{'報' if want else '不報'}、實際{'報' if got else '不報'}")
    return wrong


def main():
    wrong = selftest()
    if wrong:
        for w in wrong:
            print("✕ 檢查器自我驗證失敗：" + w)
        return 1
    # infra 規則的自我驗證：標成 infra 卻碰了程式 → 要抓到；只碰模擬交易所 → 不報
    probe = {"@case('t', 'd', infra=True)\ndef _():\n    ex = fresh()\n    T.guard_positions()\n": True,
             "@case('t', 'd', infra=True)\ndef _():\n    ex = fresh()\n    ex('GET', '/x', {}, True, 5)\n": False}
    for src, want in probe.items():
        fn = next(c[2] for c in cases(src))
        if bool(infra_touches_program(fn)) != want:
            print("✕ 檢查器自我驗證失敗：infra 規則判錯")
            return 1
    print(f"✓ 檢查器自我驗證：{len(SELFTEST)} 組否定句＋2 組 infra 固定人造資料全部判對")
    bad, total, neg = [], 0, 0
    for f in FILES:
        src = open(os.path.join(HERE, f), encoding="utf-8").read()
        found = list(cases(src))
        if len(found) < 5:
            bad.append(f"{f} 只掃到 {len(found)} 個案例（前提：掃描真的找到了案例，第 19 種）")
        for tag, desc, fn in found:
            total += 1
            body = [s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(getattr(s, "value", None), ast.Constant))]
            first = body[0] if body else None
            call = first.value if isinstance(first, (ast.Assign, ast.Expr)) else None
            if not (isinstance(call, ast.Call) and getattr(call.func, "id", "") == "fresh"):
                bad.append(f"{f} [{tag}] 第一個動作不是 fresh()（第 14 種）")
            if is_infra(fn):
                touched = infra_touches_program(fn)
                if touched:
                    bad.append(f"{f} [{tag}] 標成 infra 卻碰了程式：{'、'.join(touched)}（不能拿這個標記繞過突變檢查）")
            br = fail_branches(fn)
            if br and all(negative(c, src) for c in br):
                neg += 1
                if not has_need(fn):
                    bad.append(f"{f} [{tag}] 所有斷言都是否定句、沒有前提 need(...)：{desc[:36]}")
    for b in bad:
        print("✕ " + b)
    print(f"{'✕' if bad else '✓'} 測試靜態檢查：{total} 個案例，全否定句 {neg} 個；問題 {len(bad)} 項")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
