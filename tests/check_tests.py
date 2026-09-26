"""測試本身的靜態檢查（BINANCE_LESSONS 用法第 5 點）。

1. 測錯方式第 14 種：每個案例的第一個動作要是 fresh()（重建乾淨的模組狀態）。
2. 否定句斷言要有前提（r19）。**看斷言本身，不看描述**（第 15 種、r22）：
   本專案的斷言寫法是 `if 條件: return "失敗訊息"`——條件成立就失敗。
   「程式什麼都沒做時條件也不成立」的斷言就是否定句（例如 `if stops_sent(...)`：沒送才通過）。
   一個案例裡**所有**失敗分支都是否定句時，程式什麼都沒做整個案例就會通過 → 必須有 need(...) 前提。
   組合條件：`if A or B` 要求 A、B 都不成立，全部否定才算否定；`if A and B` 任一否定就算。
3. 第 26 種（r63 gold-scalper 的規則）：fresh() 把 save_state 換成不寫檔的假函式；案例若檢查狀態檔在磁碟上的結果
   （STATE_FILE、.unloaded、明確呼叫 save_state()），必須用 fresh(keep_save=True)，否則在檢查一個被 mock 掉的東西。

靜態分不出、只能在執行期抓的（r65、r66：寫明，不假裝有擋）：
- 「前提量到被測的結果」（第 13 種）、「前提只看有查沒看結果」（第 16 種）：need("真的送單了") 跟 need("結果是 X") 語法上一樣合法，
  差別在斷言的是不是被測那一步的產物 → 靠 mutation_check（結果在被測步驟之前就存在時，突變下照樣通過會報空跑）與 run_on_legacy。
- 「前提之後的動作讓測試本身崩掉」：靠 run_on_legacy 的「測試本身崩掉」計數（要是 0）與 mutation_check 的 CRASHES。
- 「測試把被測的那一段 mock 掉」（r43 notify_telegram）：靠人看；第 26 種只擋得住框架自己 mock 的 save_state。

    python3 -m tests.check_tests
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ["test_r11.py", "test_r14.py", "test_r17.py", "test_r20.py", "test_r23.py", "test_r26.py", "test_r29.py", "test_r32.py", "test_r35.py", "test_r38.py", "test_r42.py", "test_r46.py", "test_r49.py", "test_r52.py", "test_r55.py", "test_r58.py", "test_r60.py", "test_r63.py", "test_cg_cooldown.py", "test_r71.py", "test_r73.py", "test_r75.py", "test_r77.py"]
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


RESETS = {"fresh", "main_mod", "reload", "restart"}


def _attr_root(node):
    """`T().X`／`T.X`／`M.X` → (根, 屬性)；其他 → None。"""
    if not isinstance(node, ast.Attribute):
        return None
    cur = node.value
    root = cur.func.id if isinstance(cur, ast.Call) and isinstance(cur.func, ast.Name) else (cur.id if isinstance(cur, ast.Name) else None)
    return (root, node.attr) if root in ("T", "M", "T2") else None


def restore_order_problems(fn):
    """第 27 種（r81 gold-scalper）：先在模組上裝了東西、之後又重設（fresh／main_mod／reload／restart），
    重設後卻還在用那個屬性、中間沒有重新裝——重設把它悄悄拿掉了，測試照樣通過、不會有失敗訊號。
    回傳 [(裝的行號, 屬性, 重設的行號, 之後用到的行號)]。"""
    events = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                k = _attr_root(t)
                if k:
                    events.append((n.lineno, 0, "store", k))
        elif isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Load):
            k = _attr_root(n)
            if k:
                events.append((n.lineno, 2, "load", k))
        elif isinstance(n, ast.Call):
            f = n.func
            name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
            if name in RESETS:
                events.append((n.lineno, 1, "reset", name))
    events.sort()
    installed, wiped, out = {}, {}, []
    for line, _o, kind, k in events:
        if kind == "store":
            installed[k] = line
            wiped.pop(k, None)
        elif kind == "reset":
            for kk, sl in installed.items():
                wiped[kk] = (sl, line)
            installed = {}
        elif kind == "load" and k in wiped:
            sl, rl = wiped.pop(k)
            out.append((sl, f"{k[0]}.{k[1]}", rl, line))
    return out


SELFTEST_27 = [
    ("@case('t', 'd')\ndef _():\n    ex = fresh()\n    T.X = 1\n    fresh()\n    if T.X != 1:\n        return 'x'\n", 1),   # 裝了、重設、還在用
    ("@case('t', 'd')\ndef _():\n    ex = fresh()\n    T.X = 1\n    fresh()\n    T.X = 1\n    if T.X != 1:\n        return 'x'\n", 0),  # 重設後重新裝
    ("@case('t', 'd')\ndef _():\n    ex = fresh()\n    T.X = 1\n    fresh()\n    if T.Y != 1:\n        return 'x'\n", 0),   # 重設後用別的
    ("@case('t', 'd')\ndef _():\n    ex = fresh()\n    T.X = 1\n    if T.X != 1:\n        return 'x'\n", 0),              # 沒重設
]


DISK_MARKS = ("STATE_FILE", ".unloaded", "save_state()", "state_path(")


def needs_keep_save(fn, src):
    """第 26 種：案例檢查狀態檔在磁碟上的結果，卻沒有 keep_save=True（save_state 被框架 mock 掉）。"""
    seg = ast.get_source_segment(src, fn) or ""
    if not any(m in seg for m in DISK_MARKS):
        return False
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "fresh":
            if any(k.arg == "keep_save" and isinstance(k.value, ast.Constant) and k.value.value is True for k in node.keywords):
                return False
    return True


SELFTEST_26 = [
    ("@case('t', 'd')\ndef _():\n    ex = fresh()\n    T.save_state()\n    if not os.path.exists(T.STATE_FILE):\n        return 'x'\n", True),
    ("@case('t', 'd')\ndef _():\n    ex = fresh(keep_save=True)\n    T.save_state()\n    if not os.path.exists(T.STATE_FILE):\n        return 'x'\n", False),
    ("@case('t', 'd')\ndef _():\n    ex = fresh()\n    T.load_state(p)\n    if T.CFG['x'] != 1:\n        return 'x'\n", False),   # 只讀、不看磁碟
]


def selftest():
    wrong = []
    for src, want in SELFTEST_27:
        fn = next(c[2] for c in cases(src))
        if len(restore_order_problems(fn)) != want:
            wrong.append(f"第 27 種判錯：{src.splitlines()[3].strip()} …")
    for src, want in SELFTEST_26:
        fn = next(c[2] for c in cases(src))
        if needs_keep_save(fn, src) != want:
            wrong.append(f"第 26 種判錯：{src.splitlines()[2].strip()} / {src.splitlines()[3].strip()}")
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
    print(f"✓ 檢查器自我驗證：{len(SELFTEST)} 組否定句＋2 組 infra＋{len(SELFTEST_26)} 組 keep_save＋{len(SELFTEST_27)} 組重設順序固定人造資料全部判對")
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
            for sl, what, rl, ul in restore_order_problems(fn):
                bad.append(f"{f} [{tag}] 第 {sl} 行裝了 {what}，第 {rl} 行重設把它拿掉，第 {ul} 行還在用（第 27 種：重設要在裝之前）")
            if needs_keep_save(fn, src):
                bad.append(f"{f} [{tag}] 檢查狀態檔在磁碟上的結果，卻沒有 fresh(keep_save=True)：save_state 被框架換掉了（第 26 種）")
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
