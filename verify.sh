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

echo "── 5. 區域變數順序 ──"
python3 scripts/check_locals.py

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

echo
echo "全部通過"
