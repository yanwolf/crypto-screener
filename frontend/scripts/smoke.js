/* 建置後的煙霧測試：在 jsdom 裡實際掛載，模擬完全沒有網路。
   畫面空白或有執行期錯誤就讓建置失敗。 */
import { JSDOM } from "jsdom";
import fs from "node:fs";

const html = fs.readFileSync("dist/index.html", "utf8");
const scripts = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]).filter((s) => s.length > 1000);
if (!scripts.length) { console.error("✕ 找不到內嵌的應用程式碼"); process.exit(1); }
const css = [...html.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join("\n");

const dom = new JSDOM(`<!DOCTYPE html><html><head><style>${css}</style></head><body><div id="root"></div></body></html>`,
  { runScripts: "outside-only", url: "https://example.zeabur.app/", pretendToBeVisual: true });
const w = dom.window;
const errors = [];
w.fetch = () => Promise.reject(new Error("offline"));
w.Notification = undefined;
Object.defineProperty(w.navigator, "serviceWorker", { value: { register: () => Promise.reject(new Error("offline")) } });
["error", "unhandledrejection"].forEach((ev) => w.addEventListener(ev, (e) => errors.push(e.message || String(e.reason))));

try {
  for (const s of scripts) w.eval(s);
} catch (e) {
  console.error("✕ 掛載失敗:", e.constructor.name + ":", e.message);
  process.exit(1);
}
const fail = (msg, extra) => { console.error("✕ " + msg, extra || ""); process.exit(1); };
const hardErrors = () => errors.filter((e) => !/offline/i.test(String(e)));
const clickText = (t) => {
  const b = [...w.document.querySelectorAll("button")].find((x) => x.textContent.trim().startsWith(t));
  if (!b) return false;
  b.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  return true;
};

setTimeout(async () => {
  const root = w.document.getElementById("root");
  if (root.innerHTML.length < 500) fail("畫面空白", hardErrors());
  if (hardErrors().length) fail("執行期錯誤:", hardErrors().slice(0, 3));

  // 把每個分頁都點過一遍：拆頁後任何漏掉的引用只會在該頁渲染時爆
  const visited = [];
  for (const t of ["連線設定", "觀察清單", "鏈上雷達", "訊號紀錄", "模擬單", "提醒設定", "雷達", "空頭雷達"]) {
    if (clickText(t)) visited.push(t);
    await new Promise((r) => setTimeout(r, 250));
    const h = hardErrors();
    if (h.length) fail(`切到「${t}」後出現執行期錯誤:`, h.slice(0, 3));
  }
  console.log(`✓ 煙霧測試通過（渲染 ${(root.innerHTML.length / 1024).toFixed(0)}KB，走訪 ${visited.length} 個分頁，無執行期錯誤）`);
  process.exit(0);
}, 1400);
