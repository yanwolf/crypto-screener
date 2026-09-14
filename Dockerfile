# 這個專案只用 Python 標準函式庫，不需要編譯器或任何套件。
# 自訂 Dockerfile 是為了跳過建置器預設的 apt 安裝（gcc/build-essential）。
#
# 重要：這個檔案會被每次更新覆蓋，所以不能在這裡寫任何部署狀態。
# 啟動參數由環境變數 EXTRA_ARGS 供應（例如 EXTRA_ARGS=--live），
# 正式網與否完全由 Zeabur 的環境變數決定，資料夾內容與它無關。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CACHE_DIR=/app/cache \
    EXTRA_ARGS=""

WORKDIR /app
COPY . .
RUN mkdir -p /app/cache
EXPOSE 8080

CMD ["sh", "-c", "exec python main.py $EXTRA_ARGS"]
