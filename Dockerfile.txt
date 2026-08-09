# 使用輕量級的 Python 3 基礎映像檔
FROM python:3.10-slim

# 設定容器內的工作目錄
WORKDIR /app

# 將網頁檔與伺服器程式複製到容器內
COPY crypto-screener.html .
COPY screener_server.py .

# 宣告執行時使用的通訊埠 (Zeabur 會自動分配 PORT 環境變數)
EXPOSE $PORT

# 啟動伺服器，這裡預設開啟了背景預抓功能 (--prefetch 300)
# 若有設定 API Key 等環境變數，程式碼會自動讀取
CMD ["python3", "screener_server.py", "--prefetch", "300"]
