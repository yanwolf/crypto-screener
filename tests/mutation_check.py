"""突變檢查（BINANCE_LESSONS 用法第 5 點 r18、r20）：讓逐幣部位查詢一律回空清單（基準查不到、確認不了部位），
逐項比對哪些測試在突變下仍通過。

- 仍通過的項目必須在 EXEMPT 裡，並寫明為什麼突變與它無關；不在清單裡的就是在空跑 → 失敗。
- 「本來就在測查不到」的項目不列豁免：它們都有對照組（同樣的呼叫、沒注入時確實會做那件事），
  突變會讓對照組失敗——前提寫得夠細時，這類項目在突變下也會失敗，這是好事（r20 gold-scalper）。

    python3 -m tests.mutation_check
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITES = ["test_r11", "test_r14", "test_r17", "test_r20"]
EXEMPT = {
    ("test_r11", "7-a"): "只測持倉模式偵測與重送，不查部位",
    ("test_r11", "7-b"): "同上",
    ("test_r11", "7-c"): "自檢的持倉模式項目，不查部位",
    ("test_r11", "7-d"): "只測快取寫入時機",
    ("test_r11", "7-e"): "同上",
    ("test_r11", "7-f"): "同上",
    ("test_r11", "1-a"): "只測 Algo 端點退回條件",
    ("test_r14", "1-b"): "只測條件單端點退回期限",
    ("test_r14", "1-c"): "只測條件單端點 -4120 改回",
    ("test_r14", "3-b"): "只測 pending 佔位（不查逐幣）",
    ("test_r14", "3-c"): "認領走全量表的正向證據，不經過逐幣查詢",
    ("test_r14", "3-e"): "同上",
    ("test_r17", "3-i"): "同上",
}


def run(suite, mutate):
    env = dict(os.environ)
    if mutate:
        env["MUTATE_SYMBOL_EMPTY"] = "1"
    else:
        env.pop("MUTATE_SYMBOL_EMPTY", None)
    out = subprocess.run([sys.executable, "-m", f"tests.{suite}"], cwd=ROOT, env=env,
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    return {m.group(2): m.group(1) == "✓" for m in re.finditer(r"^([✓✕]) \[([^\]]+)\]", out, re.M)}


def main():
    bad, exempt_seen = [], set()
    for s in SUITES:
        normal, mutated = run(s, False), run(s, True)
        for tag, ok in mutated.items():
            if not normal.get(tag):
                bad.append(f"{s} [{tag}] 沒有突變時就沒通過，無法判斷")
            elif ok:
                if (s, tag) in EXEMPT:
                    exempt_seen.add((s, tag))
                else:
                    bad.append(f"{s} [{tag}] 突變下仍通過、又不在豁免清單 → 可能在空跑")
        failed = sum(1 for ok in mutated.values() if not ok)
        print(f"  {s}：突變下 {failed} 項明確失敗、{len(mutated) - failed} 項通過（皆為豁免）"
              if not any(b.startswith(s) for b in bad) else f"  {s}：有問題")
    for k in EXEMPT:
        if k not in exempt_seen:
            print(f"  （豁免的 {k[0]} [{k[1]}] 在突變下失敗了——前提夠細，不需要豁免，可以從清單拿掉）")
    for b in bad:
        print("✕ " + b)
    print(("✓" if not bad else "✕") + f" 突變檢查：豁免 {len(EXEMPT)} 項，問題 {len(bad)} 項")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
