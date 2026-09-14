"""檢查 main.py 讀取的 row/event 欄位是否真的由 engine.py 產生。

欄位名稱寫錯不會拋錯，只會安靜地拿到 None，
先前「分數不足 · 沒有評分資料」就是這樣來的。
"""
import re, sys

main = open('backend/main.py').read()
eng = open('backend/engine.py').read()

fn = main[main.index('def auto_try_trade'):main.index('def fetch_deriv_server')]
row_fields = set(re.findall(r'row\.get\("([^"]+)"\)', fn)) | set(re.findall(r'row\["([^"]+)"\]', fn))
ev_fields = set(re.findall(r'ev\.get\("([^"]+)"\)', fn))

# row 的欄位有兩個來源：engine 計算的，以及 main.py 從行情端點併入的
produced = set(re.findall(r'o\["([^"]+)"\]\s*=', eng))
produced |= set(re.findall(r'"([a-zA-Z0-9_]+)":', eng[eng.index('def extract_scan'):]))
# main.py 組 row 時直接帶入的行情欄位
mon = main[main.index('def mon_run_once'):]
produced |= set(re.findall(r'"([a-zA-Z0-9_]+)":\s*c\.get', mon))
produced |= set(re.findall(r'"([a-zA-Z0-9_]+)":\s*c\[', mon))
# 事件欄位
ev_block = eng[eng.index('events.append({'):eng.index('return events, nxt')]
ev_produced = set(re.findall(r'"([a-zA-Z0-9_]+)":', ev_block))

bad = []
for f in sorted(row_fields):
    if f not in produced:
        bad.append(f'row["{f}"] 不在 engine 產生的欄位中')
for f in sorted(ev_fields):
    if f not in ev_produced:
        bad.append(f'ev["{f}"] 不在訊號事件欄位中')

if bad:
    print("✕ 欄位對不上：")
    for b in bad:
        print("   " + b)
    sys.exit(1)
print(f"✓ 欄位檢查通過（row {len(row_fields)} 個、event {len(ev_fields)} 個都對得上）")
