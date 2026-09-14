import React from "react";
import ReactDOM from "react-dom/client";
import "./index.css";
import CryptoScreener from "./App.jsx";

ReactDOM.createRoot(document.getElementById("root")).render(<CryptoScreener />);

// 註冊 Service Worker：讓 App 可安裝、離線也能開啟外殼
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
}
