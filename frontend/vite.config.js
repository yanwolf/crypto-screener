import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";

// 輸出單一 index.html：所有 JS/CSS 內嵌，Python 伺服器直接送出即可，
// 不需要處理 assets 目錄，PWA 快取也只有一個檔案要管。
export default defineConfig({
  plugins: [react({ jsxRuntime: "classic" }), viteSingleFile()],
  build: { outDir: "dist", emptyOutDir: true, cssCodeSplit: false, assetsInlineLimit: 100000000 },
  server: { proxy: { "/api": "http://127.0.0.1:8787" } },
});
