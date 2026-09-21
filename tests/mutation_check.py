"""突變檢查（BINANCE_LESSONS 用法第 5 點 r18、r20、r22、r23）。

突變：讓逐幣部位查詢一律回空清單（基準查不到、確認不了部位）。逐項比對：
  - 突變下明確失敗 → 好。
  - 突變下仍通過，而且這一項裡突變**命中 0 次** → 「無關」，自動判定，不用寫理由。
  - 突變下仍通過、命中 > 0 → 必須在 EXEMPT 裡，類別是「前提」或「對照組」，並引用另一項；
    檢查器確認被引用的那一項在突變下**真的失敗**，否則豁免不成立。
  - EXEMPT 裡的項目如果在突變下已經會失敗 → 過期，報錯要求刪掉（過期的豁免會蓋住將來的空跑）。
檢查器先用**固定的人造資料**自我驗證（r23：不能拿當下的豁免清單來弄壞）。

    python3 -m tests.mutation_check
"""
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITES = ["test_r11", "test_r14", "test_r17", "test_r20", "test_r23", "test_r26", "test_r29"]
# (測試檔, 項目) → (類別, 引用的項目, 理由)。目前沒有需要人工豁免的項目：
# 突變下仍通過的全部是命中 0 次（自動判定無關）。
EXEMPT = {}


def evaluate(normal, mutated, hits, exempt):
    """純函式：回傳 (問題清單, 自動判定無關的項目)。normal/mutated：{(檔, 項): 是否通過}；hits：{(檔, 項): 次數}。"""
    bad, auto = [], []
    for key, ok in sorted(mutated.items()):
        if not normal.get(key):
            bad.append(f"{key} 沒有突變時就沒通過，無法判斷")
            continue
        if not ok:
            if key in exempt:
                bad.append(f"{key} 在豁免清單，但突變下已經會失敗 → 過期，請刪掉")
            continue
        if hits.get(key, 0) == 0:
            auto.append(key)
            if key in exempt:
                bad.append(f"{key} 突變命中 0 次、已自動判定無關，豁免清單不需要它 → 請刪掉")
            continue
        ex = exempt.get(key)
        if not ex:
            bad.append(f"{key} 突變命中 {hits[key]} 次仍通過、又沒有豁免 → 可能在空跑")
            continue
        kind, ref, _why = ex
        if kind not in ("前提", "對照組"):
            bad.append(f"{key} 豁免類別「{kind}」不對（只能是「前提」或「對照組」）")
        elif ref not in mutated:
            bad.append(f"{key} 引用的 {ref} 不存在")
        elif mutated[ref]:
            bad.append(f"{key} 引用的 {ref} 在突變下也通過 → 豁免不成立")
    for key in exempt:
        if key not in mutated:
            bad.append(f"{key} 在豁免清單，但這一項已經不存在 → 請刪掉")
    return bad, auto


# 固定的人造資料：每一種錯都要被抓到（r23：不拿當下的豁免清單來弄壞）
SELFTEST = [
    ("命中 > 0 仍通過、沒有豁免", {("f", "a"): True}, {("f", "a"): True}, {("f", "a"): 2}, {}, True),
    ("命中 0 次仍通過 → 自動無關", {("f", "a"): True}, {("f", "a"): True}, {}, {}, False),
    ("豁免引用的項目在突變下也通過", {("f", "a"): True, ("f", "b"): True}, {("f", "a"): True, ("f", "b"): True},
     {("f", "a"): 1, ("f", "b"): 1}, {("f", "a"): ("對照組", ("f", "b"), "x")}, True),
    ("豁免引用的項目在突變下失敗 → 成立", {("f", "a"): True, ("f", "b"): True}, {("f", "a"): True, ("f", "b"): False},
     {("f", "a"): 1, ("f", "b"): 1}, {("f", "a"): ("對照組", ("f", "b"), "x")}, False),
    ("過期豁免（突變下已經失敗）", {("f", "a"): True}, {("f", "a"): False}, {("f", "a"): 1},
     {("f", "a"): ("前提", ("f", "a"), "x")}, True),
    ("豁免引用不存在的項目", {("f", "a"): True}, {("f", "a"): True}, {("f", "a"): 1},
     {("f", "a"): ("前提", ("f", "z"), "x")}, True),
    ("豁免類別寫錯", {("f", "a"): True, ("f", "b"): True}, {("f", "a"): True, ("f", "b"): False},
     {("f", "a"): 1}, {("f", "a"): ("無關", ("f", "b"), "x")}, True),
    ("命中 0 次卻還列在豁免", {("f", "a"): True}, {("f", "a"): True}, {}, {("f", "a"): ("前提", ("f", "a"), "x")}, True),
]


def selftest():
    wrong = []
    for name, normal, mutated, hits, exempt, want_bad in SELFTEST:
        bad, _ = evaluate(normal, mutated, hits, exempt)
        if bool(bad) != want_bad:
            wrong.append(f"{name}：預期{'報錯' if want_bad else '不報'}、實際{'報錯' if bad else '不報'}")
    return wrong


def run(suite, mutate):
    env = dict(os.environ)
    log = None
    env.pop("MUTATE_SYMBOL_EMPTY", None)
    env.pop("MUTATION_LOG", None)
    if mutate:
        env["MUTATE_SYMBOL_EMPTY"] = "1"
        log = tempfile.mktemp(suffix=".json")
        env["MUTATION_LOG"] = log
    out = subprocess.run([sys.executable, "-m", f"tests.{suite}"], cwd=ROOT, env=env,
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    res = {(suite, m.group(2)): m.group(1) == "✓" for m in re.finditer(r"^([✓✕]) \[([^\]]+)\]", out, re.M)}
    hits = {}
    if log and os.path.exists(log):
        hits = {(suite, k): v for k, v in json.load(open(log, encoding="utf-8")).items()}
        os.unlink(log)
    return res, hits


def main():
    wrong = selftest()
    if wrong:
        for w in wrong:
            print("✕ 突變檢查器自我驗證失敗：" + w)
        return 1
    print(f"✓ 突變檢查器自我驗證：{len(SELFTEST)} 組固定人造資料全部判對")
    normal, mutated, hits = {}, {}, {}
    import ast
    for s in SUITES:
        n, _ = run(s, False)
        m, h = run(s, True)
        tree = ast.parse(open(os.path.join(ROOT, "tests", f"{s}.py"), encoding="utf-8").read())
        want = sum(1 for fn in tree.body if isinstance(fn, ast.FunctionDef)
                   and any(isinstance(d, ast.Call) and getattr(d.func, "id", "") == "case" for d in fn.decorator_list))
        if not (len(n) == len(m) == want > 0):
            print(f"✕ 前提不成立：{s} 有 {want} 個案例，解析到正常 {len(n)}、突變 {len(m)} 項（第 19 種）")
            return 1
        normal.update(n)
        mutated.update(m)
        hits.update(h)
        failed = sum(1 for k, ok in m.items() if not ok)
        print(f"  {s}：突變下 {failed} 項明確失敗、{len(m) - failed} 項通過")
    bad, auto = evaluate(normal, mutated, hits, EXEMPT)
    print(f"  自動判定無關（突變命中 0 次）：{len(auto)} 項 " + "、".join(f"{k[0][5:]}[{k[1]}]" for k in auto))
    for b in bad:
        print("✕ " + b)
    print(("✓" if not bad else "✕") + f" 突變檢查：人工豁免 {len(EXEMPT)} 項，問題 {len(bad)} 項")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
