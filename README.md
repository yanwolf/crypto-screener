# 部署到 Zeabur

## 檔案清單

```
main.py                    伺服器（Zeabur 以 python main.py 啟動）
requirements.txt           讓 Zeabur 判定這是 Python 專案（本專案不需外部套件）
zbpack.json                指定 Python 3.12
index.html                 前端主程式
manifest.webmanifest sw.js icon-*.png apple-touch-icon.png    PWA 相關
```

十個檔案要放在**同一層**，不要包資料夾。

## 一、推上 GitHub

```
git init
git add .
git commit -m "crypto screener"
git branch -M main
git remote add origin https://github.com/你的帳號/crypto-screener.git
git push -u origin main
```

## 二、在 Zeabur 建立服務

1. 進專案 → **Add Service** → **Git** → 授權後選這個 repo
2. Zeabur 看到 requirements.txt 會自動判定 Python，不必手動選
3. 等建置完成，Logs 出現「雲端模式：監聽 0.0.0.0:xxxx」就成功了

## 三、設定環境變數

服務頁 → **Variables**，逐項新增：

| 變數 | 值 | 說明 |
|---|---|---|
| `CG_API_KEY` | 你的 Demo Key | 額度從 5–15 拉到 30 次/分 |
| `PREFETCH` | `300` | 背景預抓前 300 檔，這項最有感 |
| `PREFETCH_TTL` | `12` | 資料保鮮時數 |
| `TG_TOKEN` | Bot Token | Telegram 推播（選填） |
| `TG_CHAT` | Chat ID | Telegram 推播（選填） |

`PORT` 不用設，Zeabur 會自動注入。設完按 **Redeploy**。

## 四、綁定網域

Networking → Domains → 輸入想要的子網域 → Generate。
拿到 https 網址後，手機用 Safari／Chrome 開，「加入主畫面」即可安裝成 App。

## 五、開啟後第一件事

頁面右上「連線設定」→ 勾選 **透過本機代理**。
名字叫本機，實際意思是走同源的 `/api` 路徑。勾了之後：
金鑰留在伺服器、共用伺服器快取、不再受瀏覽器跨域限制。

## 六、讓快取在重新部署後存活（建議）

Volumes → Add Volume → Mount Path 填 `/app/cache`，
再新增環境變數 `CACHE_DIR` = `/app/cache`。
沒掛 Volume 的話，每次重新部署快取會清空，預抓得重跑一輪。

## 注意

網址是公開的，任何知道網址的人都能透過它呼叫 CoinGecko，等於共用你的額度。
Demo Key 免費、損失有限，但不要把 Pro Key 放上去。
