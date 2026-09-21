# 加密貨幣量能異動雷達

小幣量能異動掃描 + 多空雷達 + 三刀流 + 幣安合約模擬／實盤自動下單 + Telegram 推播。

## 結構

```
backend/    Python 伺服器，純標準函式庫，零依賴
  main.py     HTTP 伺服器、CoinGecko/GeckoTerminal/GoPlus/Binance 代理、快取、監控、通知
  engine.py   評分引擎（與前端 src/engine.js、src/trade.js 逐條對齊，交叉驗證）
  trader.py   幣安合約下單、風控閘門、帳本
  preflight.py 交易所相容性自檢，每項對應 BINANCE_LESSONS.md 的一條
frontend/   Vite + React，建置輸出單一 index.html
  src/
    theme.js constants.js util.js api.js demo.js
    ta.js blades.js engine.js alerts.js chain.js trade.js
    components/  小元件與卡片
    pages/       各分頁（Radar / Watch / Chain / Trade / Signals / Alerts / DetailModal …）
    App.jsx      狀態擁有者，分頁透過狀態袋 s 取用
  scripts/   smoke.js（jsdom 掛載並走訪全部分頁）、xval.js（交叉驗證）
scripts/    check_fields.py、check_locals.py、xval.py、patch.py（找不到就報錯的取代）
tests/      test_parity.py（出場位階一致性，第 11 條）、test_lessons.py（第 2、7、8、13 條情境）、test_r11.py、test_r14.py、test_r17.py、test_r20.py（逐段檢查項目）、check_tests.py、mutation_check.py（測試本身的檢查）、fake_exchange.py（共用模擬交易所）
BINANCE_LESSONS.md  三個幣安專案共用的踩坑清單，版本日期必須一致
Dockerfile  多階段：node 建前端 → python:slim 只帶建好的 HTML
verify.sh   打包前完整驗證
GO_LIVE.md  接入正式網流程與並存部署
```

## 本機開發

```
cd frontend && npm install && npm run build      # 產出 dist/index.html
cd ../backend && python3 main.py                  # 會自動找 ../frontend/dist
```

前端改完重新 `npm run build` 即可；或 `npm run dev` 用 Vite 開發伺服器（會把 /api 代理到 8787）。

## 部署（Zeabur）

Git 連結後自動用根目錄的 Dockerfile 建置。部署狀態全部在環境變數，資料夾內不含任何狀態，
整包覆蓋更新永遠安全。環境變數清單見 GO_LIVE.md。

## 更新前

```
./verify.sh
```

建置、煙霧測試（走訪全部分頁）、伺服器啟動、端點、欄位對應、前後端引擎交叉驗證，
任何一關失敗就不要推。
