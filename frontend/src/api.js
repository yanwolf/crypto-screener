import { derivAnalyze } from "./trade.js";
import { sleep, store } from "./util.js";

/* ════ 請求層 ════════════════════════════════════════════ */
const gate = { last: 0, chain: Promise.resolve() };
const lastStale = { age: null };      // 伺服器回舊資料時的年齡（秒）

function buildUrl(path, { key, pro, proxy, local }) {
  if (local) return "/api/v3" + path;
  const base = pro ? "https://pro-api.coingecko.com/api/v3" : "https://api.coingecko.com/api/v3";
  let url = base + path;
  if (key) url += (url.includes("?") ? "&" : "?") + (pro ? "x_cg_pro_api_key=" : "x_cg_demo_api_key=") + encodeURIComponent(key.trim());
  return proxy ? proxy.replace(/\/$/, "") + "/" + url : url;
}
function enqueue(fn, minGap) {
  const run = gate.chain.then(async () => {
    const wait = Math.max(0, gate.last + minGap - Date.now());
    if (wait) await sleep(wait);
    gate.last = Date.now();
    return fn();
  });
  gate.chain = run.catch(() => {});
  return run;
}
async function cgFetch(path, cfg) {
  const minGap = cfg.local ? 250 : cfg.key ? 900 : 2400;
  return enqueue(async () => {
    for (let attempt = 0; attempt < 3; attempt++) {
      let res;
      const ac = typeof AbortController !== "undefined" ? new AbortController() : null;
      const tid = ac ? setTimeout(() => ac.abort(), 25000) : null;   // 單次請求上限 25 秒
      try {
        res = await fetch(buildUrl(path, cfg), {
          method: "GET", mode: "cors", cache: "no-store", signal: ac ? ac.signal : undefined,
        });
      } catch (e) {
        if (tid) clearTimeout(tid);
        throw new Error(e && e.name === "AbortError" ? "TIMEOUT" : "BLOCKED");
      }
      if (tid) clearTimeout(tid);
      if (res.status === 429) {
        if (attempt < 2) { await sleep(6000 * (attempt + 1)); continue; }
        throw new Error("RATE");
      }
      if (res.status === 401 || res.status === 403) throw new Error("AUTH");
      if (res.status === 404 && cfg.local && !res.headers.get("X-Cache")) throw new Error("NOPROXY");
      if (res.status >= 500) {
        if (attempt < 2) { await sleep(2500); continue; }
        throw new Error("SERVER");
      }
      if (!res.ok) throw new Error("HTTP " + res.status);
      const sa = res.headers.get("X-Stale-Age");
      lastStale.age = sa ? parseInt(sa, 10) : null;   // 伺服器標示這份是舊快取
      return res.json();
    }
  }, minGap);
}
const ERR_MSG = {
  BLOCKED: "瀏覽器沒有把請求送出去。這個網路擋掉了 api.coingecko.com，但你目前的網址還開得起來——到「連線設定」勾選「強制走伺服器」，改由伺服器代抓資料就能繞過去。",
  RATE: "CoinGecko 回 429，額度已滿。無金鑰時每分鐘只有 5–15 次；等一分鐘再試，或填入免費 Demo API Key 提高到每分鐘 30 次。",
  QUOTA: "Demo Key 的每月 10,000 次總額度已用盡（error_code 10006）。等一分鐘沒有用，要等到下個月重置。請調高監控間隔、關閉自動更新，或升級 CoinGecko 方案。",
  AUTH: "API Key 被拒絕。確認金鑰沒打錯，且 Pro 金鑰才需勾選 Pro。",
  SERVER: "CoinGecko 伺服器暫時異常，稍後重試。",
  TIMEOUT: "請求超過 25 秒沒有回應。若走伺服器代理，通常是伺服器正在等額度或重新部署；按「執行連線診斷」可以看出卡在哪一步。",
  NOPROXY: "同源路徑 /api/v3 沒有回應，代理可能重新部署中或已停止。程式會自動改回直連，稍後可按「重新偵測」。",
};
const errText = (e) => ERR_MSG[e.message] || `請求失敗（${e.message}）。`;


/* ════ 通知發送 ══════════════════════════════════════════ */
async function sendNotify(ch, msg) {
  const out = [];
  if (ch.browser) {
    try {
      if (typeof Notification !== "undefined" && Notification.permission === "granted") {
        new Notification(msg.title, { body: msg.text.slice(0, 300) });
        out.push(["瀏覽器通知", true, ""]);
      } else out.push(["瀏覽器通知", false, "尚未授權"]);
    } catch (e) { out.push(["瀏覽器通知", false, e.message]); }
  }
  if (ch.useServer) {
    try {
      const r = await fetch("/api/notify", {
        method: "POST", headers: { "Content-Type": "application/json" },
        // 帶上訊號與掃描資料，伺服器才能跑同一套自動下單判斷，
        // 否則網頁送出的通知會少掉「有沒有下單、為什麼」那一段。
        body: JSON.stringify({ title: msg.title, text: msg.text,
                               event: msg.event || null, row: msg.row || null }),
      });
      const j = await r.json().catch(() => ({}));
      out.push(["伺服器轉發", r.ok, r.ok ? (j.sent || []).join("、") : "HTTP " + r.status]);
    } catch (e) { out.push(["伺服器轉發", false, "連不到 screener_server.py"]); }
    return out;
  }
  if (ch.tgToken && ch.tgChat) {
    try {
      // 用 GET 帶查詢參數，屬於 simple request，不會觸發 CORS 預檢
      const u = `https://api.telegram.org/bot${encodeURIComponent(ch.tgToken)}/sendMessage` +
        `?chat_id=${encodeURIComponent(ch.tgChat)}&text=${encodeURIComponent(msg.title + "\n" + msg.text)}`;
      const r = await fetch(u);
      out.push(["Telegram", r.ok, r.ok ? "" : "HTTP " + r.status]);
    } catch (e) { out.push(["Telegram", false, "被瀏覽器攔截"]); }
  }
  if (ch.discord) {
    try {
      const r = await fetch(ch.discord, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: `**${msg.title}**\n${msg.text}` }),
      });
      out.push(["Discord", r.ok || r.status === 204, r.ok ? "" : "HTTP " + r.status]);
    } catch (e) { out.push(["Discord", false, "跨域被擋，改用伺服器轉發"]); }
  }
  if (ch.email && !ch.useServer) out.push(["電子郵件", false, "瀏覽器無法直接寄信，需開啟伺服器轉發"]);
  return out;
}



/* ── Binance 衍生品資料抓取 ────────────────────────────────
   公開端點免金鑰。瀏覽器直連時 Binance 有 CORS 允許，
   但部分地區的 IP 會被擋，所以走伺服器代理較穩。 */

const bnGate = { last: 0, chain: Promise.resolve() };

async function bnFetch(path, cfg) {
  const url = cfg.local ? "/api/bn" + path : "https://fapi.binance.com" + path;
  const run = bnGate.chain.then(async () => {
    const wait = Math.max(0, bnGate.last + 260 - Date.now());
    if (wait) await sleep(wait);
    bnGate.last = Date.now();
    const ac = typeof AbortController !== "undefined" ? new AbortController() : null;
    const tid = ac ? setTimeout(() => ac.abort(), 20000) : null;
    try {
      const r = await fetch(url, { cache: "no-store", signal: ac ? ac.signal : undefined });
      if (tid) clearTimeout(tid);
      if (!r.ok) throw new Error("BN_" + r.status);
      return r.json();
    } catch (e) {
      if (tid) clearTimeout(tid);
      throw e;
    }
  });
  bnGate.chain = run.catch(() => {});
  return run;
}

/* 可用合約清單：CoinGecko 的 symbol 對到 Binance 的 USDT 永續 */
let bnSymbolCache = null;
async function bnSymbols(cfg) {
  if (bnSymbolCache) return bnSymbolCache;
  try {
    const j = await bnFetch("/fapi/v1/exchangeInfo", cfg);
    const set = new Set();
    (j.symbols || []).forEach((x) => {
      if (x.contractType === "PERPETUAL" && x.quoteAsset === "USDT" && x.status === "TRADING")
        set.add(x.baseAsset.toUpperCase());
    });
    bnSymbolCache = set;
    return set;
  } catch (e) {
    bnSymbolCache = new Set();
    return bnSymbolCache;
  }
}

/* 抓單一幣種的衍生品資料。找不到對應合約時回傳 null，不是錯誤 —
   多數小市值幣本來就沒有永續合約。 */
async function fetchDeriv(coin, cfg) {
  const base = String(coin.symbol || "").toUpperCase();
  const avail = await bnSymbols(cfg);
  if (!avail.has(base)) return null;
  const sym = base + "USDT";
  const q = `symbol=${sym}&period=1h&limit=30`;
  try {
    const [oiH, lsR, tkR, prem] = await Promise.all([
      bnFetch(`/futures/data/openInterestHist?${q}`, cfg).catch(() => null),
      bnFetch(`/futures/data/globalLongShortAccountRatio?${q}`, cfg).catch(() => null),
      bnFetch(`/futures/data/takerlongshortRatio?${q}`, cfg).catch(() => null),
      bnFetch(`/fapi/v1/premiumIndex?symbol=${sym}`, cfg).catch(() => null),
    ]);
    if (!oiH && !prem) return null;
    const d = {
      oi: (oiH || []).map((x) => parseFloat(x.sumOpenInterestValue)),
      ls: (lsR || []).map((x) => parseFloat(x.longShortRatio)),
      taker: (tkR || []).map((x) => parseFloat(x.buySellRatio)),
      funding: prem ? parseFloat(prem.lastFundingRate) : null,
      fundingHist: [],
      chg24: coin.price_change_percentage_24h ?? null,
    };
    const a = derivAnalyze(d);
    a.symbol = sym;
    a.updated = Date.now();
    return a;
  } catch (e) {
    return null;
  }
}

export { ERR_MSG, bnFetch, bnGate, bnSymbolCache, bnSymbols, buildUrl, cgFetch, enqueue, errText, fetchDeriv, gate, lastStale, sendNotify };
