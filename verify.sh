#!/bin/bash
# 打包前的完整驗證：前端建置 → 煙霧測試 → 交叉驗證 → 伺服器啟動 → 端點
set -e
cd "$(dirname "$0")"

echo "── 1. 前端建置 ──"
(cd frontend && npm run build 2>&1 | grep -E "built in|error" )

echo "── 2. 前端煙霧測試 ──"
(cd frontend && node scripts/smoke.js)

echo "── 3. Python 語法 ──"
for f in backend/main.py backend/engine.py backend/trader.py; do
  python3 -c "import ast;ast.parse(open('$f').read())" && echo "  $f 通過"
done

echo "── 4. 伺服器實際啟動 ──"
rm -rf /tmp/vfy && mkdir -p /tmp/vfy
(cd backend && CACHE_DIR=/tmp/vfy PORT=8899 timeout 14 python3 main.py > /tmp/vfy/srv.log 2>&1 &)
sleep 4
if grep -qiE "Traceback|NameError|SyntaxError" /tmp/vfy/srv.log; then
  echo "  ✕ 啟動時拋出例外："; sed -n '1,25p' /tmp/vfy/srv.log; exit 1
fi
for ep in / /api/health "/api/health?probe=1" /api/trade/status; do
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:8899$ep" || echo 000)
  echo "  $ep → HTTP $code"; [ "$code" = "200" ] || { echo "  ✕ 端點異常"; exit 1; }
done
curl -s http://127.0.0.1:8899/ | grep -q '<div id="root">' && echo "  首頁送出建置後的 index.html"
sleep 10 || true

echo "── 5. 區域變數順序／未定義名稱（BINANCE_LESSONS 第 14 條）──"
python3 scripts/check_locals.py
# 第 19 種：沒裝 pyflakes 時輸出是空的、「沒有未定義名稱」就空跑成立。一律要求裝好，並先掃一個已知有錯的檔。
if ! python3 -m pyflakes --version >/dev/null 2>&1; then
  echo "  ✕ 沒裝 pyflakes，無法檢查未定義名稱（pip install pyflakes）"; exit 1
fi
canary=$(mktemp --suffix=.py); printf 'def f():\n    return zz_undefined_canary\n' > "$canary"
if ! python3 -m pyflakes "$canary" 2>&1 | grep -q "undefined name 'zz_undefined_canary'"; then
  echo "  ✕ pyflakes 對已知有錯的檔沒有報錯，工具沒有正常運作"; rm -f "$canary"; exit 1
fi
rm -f "$canary"
nfiles=$(find backend tests scripts -name '*.py' | wc -l)
if [ "$nfiles" -lt 20 ]; then echo "  ✕ 只找到 $nfiles 個 .py 檔（路徑錯了？）"; exit 1; fi
pf=$(python3 -m pyflakes backend/ tests/ scripts/ 2>&1 || true)
# r28：`import *` 會讓 pyflakes 在那個檔上完全無法偵測未定義名稱（它自己會說 unable to detect）
if echo "$pf" | grep -q "unable to detect"; then echo "  ✕ pyflakes 無法偵測某些檔的未定義名稱（import *？）："; echo "$pf" | grep "unable to detect"; exit 1; fi
und=$(echo "$pf" | grep "undefined name" || true)
if [ -n "$und" ]; then echo "  ✕ 有未定義名稱，不能部署："; echo "$und"; exit 1; fi
echo "✓ pyflakes：掃了 $nfiles 個檔，沒有未定義名稱（已知有錯的檔有被抓到）"
python3 scripts/check_returns.py

echo "── 6. 欄位對應 ──"
python3 scripts/check_fields.py

echo "── 7. 前後端引擎交叉驗證 ──"
python3 scripts/xval.py

echo "── 8. 出場規則一致性（BINANCE_LESSONS 第 11 條）──"
python3 -m tests.test_parity

echo "── 9. 踩坑情境（BINANCE_LESSONS 第 2、7、8、13 條）──"
python3 -m tests.test_lessons

echo "── 10. r8→r11 逐段檢查項目 ──"
python3 -m tests.test_r11

echo "── 11. r12→r14 逐段檢查項目 ──"
python3 -m tests.test_r14

echo "── 12. r15→r17 逐段檢查項目 ──"
python3 -m tests.test_r17

echo "── 13. r18→r20 逐段檢查項目 ──"
python3 -m tests.test_r20

echo "── 14. r21→r23 逐段檢查項目 ──"
python3 -m tests.test_r23

echo "── 15. r24→r26 逐段檢查項目 ──"
python3 -m tests.test_r26

echo "── 16. r27→r29 逐段檢查項目 ──"
python3 -m tests.test_r29

echo "── 17. r30→r32 逐段檢查項目 ──"
python3 -m tests.test_r32

echo "── 18. r33→r35 逐段檢查項目 ──"
python3 -m tests.test_r35

echo "── 19. r36→r38 逐段檢查項目 ──"
python3 -m tests.test_r38

echo "── 20. r39→r42 逐段檢查項目（含第 15 條：市價單回 NEW）──"
python3 -m tests.test_r42

echo "── 20b. r43→r46 逐段檢查項目 ──"
python3 -m tests.test_r46

echo "── 20c. r47→r49 逐段檢查項目（兩條執行緒交錯、風控讀不到）──"
python3 -m tests.test_r49

echo "── 20d. r50→r52 逐段檢查項目（交錯測試的對照組、拿鎖前讀的部位）──"
python3 -m tests.test_r52

echo "── 20e. r53→r55 逐段檢查項目（交易所端重開、平倉送單前確認）──"
python3 -m tests.test_r55

echo "── 20f. r56→r58 逐段檢查項目（手動平倉講明沒送單、自檢均價比對）──"
python3 -m tests.test_r58

echo "── 20g. r58→r60 逐段檢查項目（回應依實際來源寫、每個「查不到」的直接測試）──"
python3 -m tests.test_r60

echo "── 20h. r61→r63 逐段檢查項目（停損剛觸發的回應、通知欄位）──"
python3 -m tests.test_r63

echo "── 20i. CoinGecko 429 讓路、行情榜不等上游 ──"
python3 -m tests.test_cg_cooldown

echo "── 21. 測試本身與工具的檢查（用法第 5 點、第 14 條）──"
python3 -m tests.check_indexing        # 先索引、沒先確認有東西（r35）
# 現在的測試跑 tests/legacy/ 下每一版舊程式：測試本身崩掉要是 0（r35、r36；r41 gold-scalper：自動跑每一版）
nleg=0
for d in tests/legacy/*/; do
  v=$(basename "$d"); nleg=$((nleg+1))
  python3 -m tests.run_on_legacy "$v"
done
if [ "$nleg" -lt 1 ]; then echo "  ✕ tests/legacy/ 下沒有舊版程式"; exit 1; fi
python3 scripts/patch.py              # apply 比對不到時一個檔都不寫（r26）
python3 -m tests.check_tests          # 每個案例從 fresh() 開始（第 14 種）、否定句要有前提（r19）
python3 -m tests.mutation_check       # 逐項突變比對：仍通過的必須在豁免清單（r18、r20）

echo
echo "全部通過"
