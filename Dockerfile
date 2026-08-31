# 這個專案只用 Python 標準函式庫，不需要編譯器或任何套件。
# 自訂 Dockerfile 是為了跳過建置器預設的 apt 安裝（gcc/build-essential），
# 那一步會花好幾分鐘，而我們完全用不到。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CACHE_DIR=/app/cache

WORKDIR /app

# 只複製執行需要的檔案；.dockerignore 已排除快取與狀態檔
COPY . .

RUN mkdir -p /app/cache

EXPOSE 8080

CMD ["python", "main.py"]
