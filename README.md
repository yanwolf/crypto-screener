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
| `PREFETCH` | `300` | 背景預抓前 300 檔 |
| `MONITOR_EVERY` | `5` | 背景訊號監控的檢查間隔（分鐘） |
| `PREFETCH_TTL` | `12` | 資料保鮮時數 |
| `ADMIN_KEY` | 自訂字串 | 選填。設了之後，只有知道這組金鑰的人能更換機器人 |

`PORT` 不用設，Zeabur 會自動注入。設完按 **Redeploy**。

Telegram 不需要環境變數，改在網頁上設定（見下方）。

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

## 七、設定 Telegram 推播

全部在網頁的「提醒設定 → 通知管道」完成：

1. **建立機器人**：Telegram 搜尋 `@BotFather` → 送出 `/newbot` → 取名字 →
   複製它給的 Token → 貼進網頁按「驗證並儲存」。Token 存在伺服器，不會留在瀏覽器。
2. **配對聊天室**：按「開始配對」→ 網頁給你一組六碼 →
   點「在 Telegram 開啟」，Telegram 會自動帶入代碼，按 START 即可。
   幾秒後網頁會顯示配對成功，機器人也會回一則確認訊息。
3. 想收到通知的人各自做一次步驟二，可以配對多個聊天室（也支援群組：
   把機器人加進群組後在群裡送 `/start 代碼`）。

配對結果存在伺服器的快取目錄，重新部署後若有掛 Volume 就不會消失。

## 八、讓網頁關掉也收得到推播

前端的「訊號監控」只在網頁開著時運作。要真正背景推播，用**伺服器背景監控**：

1. 先完成上一節的 Telegram 配對
2. 雷達頁把想監控的幣加星號，進觀察清單
3. 提醒設定 → 調好多空門檻
4. 「伺服器背景監控」→ 按**同步並啟動背景監控**

之後伺服器每 5 分鐘自己算一次，觸發就推 Telegram，網頁與手機都可以關掉。
設定存在磁碟，服務重啟後會自動接續。

改了門檻或增減觀察清單，記得回來按一次「重新同步設定」。

伺服器用的是 `engine.py`，它是前端評分邏輯的 Python 移植版，
公式逐條對齊過（交叉驗證的數值差異為 0），所以推播的分數與畫面上一致。
若要修改任何門檻或係數，兩邊都要改。

## 注意

網址是公開的，任何知道網址的人都能透過它呼叫 CoinGecko，等於共用你的額度。
Demo Key 免費、損失有限，但不要把 Pro Key 放上去。
