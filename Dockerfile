# ── 第一階段：建前端 ────────────────────────────────────────
# 只在這一階段需要 node；產物是單一 index.html，之後的映像不含 node。
FROM node:20-alpine AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ── 第二階段：Python 伺服器 ─────────────────────────────────
# 純標準函式庫，不裝任何套件，不裝編譯器。
#
# 重要：這個檔案會被每次更新覆蓋，不能寫任何部署狀態。
# 正式網與否由環境變數 EXTRA_ARGS / ALLOW_LIVE 決定（見 GO_LIVE.md）。
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 CACHE_DIR=/app/cache EXTRA_ARGS=""
WORKDIR /app
COPY backend/ ./
COPY --from=web /web/dist/ ./static/
RUN mkdir -p /app/cache
EXPOSE 8080
CMD ["sh", "-c", "exec python main.py $EXTRA_ARGS"]
