import { lastStale } from "./api.js";
import { C } from "./theme.js";
import { clamp, sleep, store } from "./util.js";

/* ════════════════════════════════════════════════════════
   鏈上異動雷達
   熱度資料：GeckoTerminal（新池、熱門池、成交、買賣人數、流動性）
   安全資料：GoPlus Token Security（蜜罐、稅率、權限、LP 鎖倉、持幣集中度）
   兩者都是免費公開 API，且支援跨域。
   ════════════════════════════════════════════════════════ */
const GT_BASE = "https://api.geckoterminal.com/api/v2";
const GP_BASE = "https://api.gopluslabs.io/api/v1";

const NETWORKS = [
  { id: "eth", name: "Ethereum", gp: "1" },
  { id: "bsc", name: "BNB Chain", gp: "56" },
  { id: "base", name: "Base", gp: "8453" },
  { id: "arbitrum", name: "Arbitrum", gp: "42161" },
  { id: "polygon_pos", name: "Polygon", gp: "137" },
  { id: "solana", name: "Solana", gp: "solana" },
];

const chainGate = { last: 0, chain: Promise.resolve() };
function enqueueOn(g, fn, minGap) {
  const run = g.chain.then(async () => {
    const wait = Math.max(0, g.last + minGap - Date.now());
    if (wait) await sleep(wait);
    g.last = Date.now();
    return fn();
  });
  g.chain = run.catch(() => {});
  return run;
}
async function chainFetch(kind, path, cfg) {
  const url = cfg.local
    ? (kind === "gt" ? "/api/gt" : "/api/gp") + path
    : (kind === "gt" ? GT_BASE : GP_BASE) + path;
  return enqueueOn(chainGate, async () => {
    for (let a = 0; a < 3; a++) {
      let res;
      try { res = await fetch(url, { method: "GET", mode: "cors", cache: "no-store" }); }
      catch (e) { throw new Error("BLOCKED"); }
      if (res.status === 429) { if (a < 2) { await sleep(5000 * (a + 1)); continue; } throw new Error("RATE"); }
      if (!res.ok) throw new Error("HTTP " + res.status);
      const sa = res.headers.get("X-Stale-Age");
      lastStale.age = sa ? parseInt(sa, 10) : null;   // 伺服器標示這份是舊快取
      return res.json();
    }
  }, 2200);
}

/* ── 合約風險評分 ──────────────────────────────────────── */
const pctNum = (v) => (v == null || v === "" ? null : parseFloat(v) * 100);
const isOn = (v) => v === "1" || v === 1 || v === true;

function securityScore(g, isSol) {
  if (!g) return { score: null, flags: [], checked: false };
  const f = [];
  let sc = 100;
  const cut = (n, level, k, t) => { sc -= n; f.push({ level, k, t }); };

  if (isSol) {
    if (isOn(g.mintable?.status)) cut(18, "high", "可增發", "發行方還能繼續鑄造新代幣，你手上的份額隨時可能被稀釋。");
    if (isOn(g.freezable?.status)) cut(35, "block", "可凍結帳戶", "發行方能凍結任何人的代幣，等同於可以讓你賣不掉。");
    if (isOn(g.closable?.status)) cut(20, "high", "可關閉帳戶", "發行方能關閉持有帳戶，屬於高度異常的權限。");
    if (isOn(g.balance_mutable_authority?.status)) cut(30, "block", "可改餘額", "發行方能直接修改任何人的餘額。");
    if (isOn(g.metadata_mutable?.status)) cut(6, "low", "名稱可改", "代幣名稱與圖示可被更改，常見於冒名頂替。");
    const tf = parseFloat(g.transfer_fee?.current_fee_rate ?? 0) * 100;
    if (tf > 5) cut(tf > 15 ? 40 : 20, tf > 15 ? "block" : "high", `轉帳稅 ${tf.toFixed(1)}%`, `每次轉帳被抽 ${tf.toFixed(1)}%，來回一趟就先虧掉這個比例。`);
  } else {
    if (isOn(g.is_honeypot)) cut(100, "block", "疑似蜜罐", "偵測到買得進、賣不出的特徵，這是最典型的詐騙結構。");
    if (isOn(g.cannot_sell_all)) cut(60, "block", "無法全部賣出", "合約限制你不能一次賣光，出場會被卡住。");
    if (isOn(g.cannot_buy)) cut(30, "high", "無法買入", "目前買不進去，可能是尚未開盤或被限制。");
    if (isOn(g.transfer_pausable)) cut(30, "block", "可暫停交易", "合約方能隨時凍結所有轉帳，你會在最想賣的時候賣不掉。");
    if (isOn(g.is_blacklisted)) cut(25, "high", "可列黑名單", "合約方能把特定地址列入黑名單，讓該地址無法交易。");
    if (isOn(g.selfdestruct)) cut(30, "block", "可自毀", "合約含自毀函式，觸發後代幣直接歸零。");
    if (isOn(g.hidden_owner)) cut(28, "block", "隱藏擁有者", "表面上已放棄權限，實際上仍有隱藏的控制者。");
    if (isOn(g.can_take_back_ownership)) cut(22, "high", "可取回擁有權", "就算現在放棄了，合約方仍能把控制權拿回去。");
    if (isOn(g.owner_change_balance)) cut(35, "block", "擁有者可改餘額", "合約方能直接修改任何人的持幣數量。");
    if (isOn(g.slippage_modifiable) || isOn(g.personal_slippage_modifiable)) cut(20, "high", "稅率可調", "交易稅可以隨時被改高，現在低不代表賣的時候還低。");
    if (isOn(g.is_mintable)) cut(14, "mid", "可增發", "發行方還能鑄造新幣，供給隨時被稀釋。");
    if (isOn(g.trading_cooldown)) cut(10, "mid", "交易冷卻", "兩次交易之間有強制間隔，急著出場時會卡住。");
    if (!isOn(g.is_open_source)) cut(22, "high", "原始碼未驗證", "合約沒有公開原始碼，裡面寫了什麼沒人知道。");
    if (isOn(g.is_proxy)) cut(8, "low", "可升級代理", "合約邏輯可被替換。正規協議常見，純交易型代幣則沒必要。");

    const bt = pctNum(g.buy_tax), st = pctNum(g.sell_tax);
    const mx = Math.max(bt ?? 0, st ?? 0);
    if (mx >= 25) cut(45, "block", `交易稅 ${mx.toFixed(0)}%`, `買或賣要被抽 ${mx.toFixed(0)}%，這個成本幾乎不可能靠價差賺回來。`);
    else if (mx >= 10) cut(25, "high", `交易稅 ${mx.toFixed(0)}%`, `單邊稅率 ${mx.toFixed(0)}%，來回交易成本很重。`);
    else if (mx >= 5) cut(10, "mid", `交易稅 ${mx.toFixed(0)}%`, `單邊稅率 ${mx.toFixed(0)}%，短線進出會被侵蝕。`);

    /* LP 鎖倉：撤池風險的核心 */
    const lps = g.lp_holders || [];
    const lockedPct = lps.reduce((a, h) => a + (isOn(h.is_locked) || /burn|dead|0x000000000000000000000000000000000000dead/i.test(h.address || "") ? parseFloat(h.percent || 0) : 0), 0) * 100;
    if (lps.length) {
      if (lockedPct < 50) cut(lockedPct < 20 ? 30 : 18, lockedPct < 20 ? "block" : "high",
        `LP 僅鎖 ${lockedPct.toFixed(0)}%`, `流動性池只有 ${lockedPct.toFixed(0)}% 被鎖定或銷毀，其餘隨時可以被抽走，這就是撤池跑路的風險。`);
      else f.push({ level: "ok", k: `LP 已鎖 ${lockedPct.toFixed(0)}%`, t: "大部分流動性已鎖定或銷毀，短期撤池的風險較低。" });
    } else cut(15, "mid", "查無 LP 鎖倉資訊", "找不到流動性池的鎖倉資料，無法判斷會不會被抽走。");
    if (g._lockedPct === undefined) g._lockedPct = lps.length ? lockedPct : null;
  }

  /* 持幣集中度 */
  const hs = (g.holders || []).filter((h) => !isOn(h.is_locked));
  const top10 = hs.slice(0, 10).reduce((a, h) => a + parseFloat(h.percent || 0), 0) * 100;
  const top1 = hs.length ? parseFloat(hs[0].percent || 0) * 100 : null;
  if (hs.length) {
    if (top10 >= 70) cut(32, "block", `前十持有 ${top10.toFixed(0)}%`, `前十個地址就握有 ${top10.toFixed(0)}% 的流通量，其中任何一個賣出都會把價格砸下來。`);
    else if (top10 >= 50) cut(18, "high", `前十持有 ${top10.toFixed(0)}%`, `籌碼偏集中，前十地址佔 ${top10.toFixed(0)}%。`);
    else if (top10 >= 30) cut(6, "mid", `前十持有 ${top10.toFixed(0)}%`, `集中度中等，尚在常見範圍。`);
  }
  const holders = parseInt(g.holder_count || 0, 10);
  if (holders && holders < 200) cut(holders < 50 ? 20 : 10, holders < 50 ? "high" : "mid",
    `持幣地址僅 ${holders}`, `只有 ${holders} 個地址持有，參與者太少，價格很容易被單一錢包左右。`);

  return { score: clamp(sc, 0, 100), flags: f, checked: true, top10, top1, holders, lockedPct: g._lockedPct };
}

/* ── 鏈上熱度評分 ──────────────────────────────────────── */
function heatScore(p) {
  const liq = p.liq || 0, vol = p.vol24 || 0;
  const turn = liq ? vol / liq : 0;
  const sLiq = clamp(((Math.log10(Math.max(liq, 1)) - 3.5) / 2.5) * 100, 0, 100);
  const sTurn = clamp((Math.log10(Math.max(turn, 0.05)) / Math.log10(20)) * 100, 0, 100);
  const sBuyers = clamp((Math.log10(Math.max(p.buyers || 0, 1)) / Math.log10(500)) * 100, 0, 100);
  const sTx = clamp((Math.log10(Math.max((p.buys || 0) + (p.sells || 0), 1)) / Math.log10(2000)) * 100, 0, 100);
  const ratio = p.sells ? p.buys / p.sells : p.buys ? 2 : 1;
  const sRatio = clamp(((ratio - 0.6) / 1.4) * 100, 0, 100);
  return { heat: clamp(0.22 * sLiq + 0.2 * sTurn + 0.24 * sBuyers + 0.16 * sTx + 0.18 * sRatio, 0, 100), turn, ratio, sLiq, sTurn, sBuyers, sTx, sRatio };
}

function parsePool(d, tokens) {
  const a = d.attributes || {};
  const bt = d.relationships?.base_token?.data?.id || "";
  const tok = tokens[bt] || {};
  const tx = a.transactions?.h24 || {};
  const age = a.pool_created_at ? (Date.now() - new Date(a.pool_created_at).getTime()) / 3600000 : null;
  const p = {
    pool: a.address, name: a.name, sym: (tok.symbol || a.name || "?").split("/")[0].trim().toUpperCase(),
    tokenName: tok.name || "", addr: (tok.address || bt.split("_").slice(1).join("_") || "").toLowerCase(),
    price: parseFloat(a.base_token_price_usd || 0),
    liq: parseFloat(a.reserve_in_usd || 0),
    vol24: parseFloat(a.volume_usd?.h24 || 0),
    fdv: parseFloat(a.fdv_usd || 0),
    buys: tx.buys || 0, sells: tx.sells || 0, buyers: tx.buyers || 0, sellers: tx.sellers || 0,
    chg24: parseFloat(a.price_change_percentage?.h24 || 0),
    chg1h: parseFloat(a.price_change_percentage?.h1 || 0),
    ageH: age,
  };
  return { ...p, ...heatScore(p) };
}

/* 高熱度但低安全 → 一律進高風險觀察區，不進機會榜 */
function bucketOf(t, minSafe) {
  if (t.sec == null) return "unverified";
  if (t.sec < minSafe) return "risk";
  return t.heat >= 55 ? "opportunity" : "watchlist";
}
const BUCKETS = {
  opportunity: { label: "鏈上機會榜", color: C.teal, say: "安全檢查過關，而且鏈上活躍度夠高。" },
  watchlist:   { label: "一般清單",   color: C.muted, say: "安全檢查過關，但熱度還不明顯。" },
  risk:        { label: "高風險觀察區", color: C.red, say: "鏈上很熱鬧，但合約檢查沒過。熱度越高越危險，這一區只供觀察。" },
  unverified:  { label: "無法驗證",   color: C.gold, say: "取不到合約安全資料，在確認之前一律視同高風險。" },
};


/* 鏈上示範資料：連不到 API 時仍可操作介面（數字為模擬值） */
function makeChainDemo(net) {
  let seed = 913;
  const rnd = () => (seed = (seed * 1664525 + 1013904223) % 4294967296) / 4294967296;
  const names = [["TURBO","Turbo Cat"],["PEPE2","Pepe Two"],["GROK","Grok AI"],["MOON","MoonBase"],
    ["SAFU","Safu Token"],["DEGEN","Degen Play"],["ALPHA","Alpha Signal"],["RUGME","Free Mint"],
    ["ZKAI","ZK Intelligence"],["BONKY","Bonky Dog"],["VAULT","Vault Finance"],["SNIPE","Sniper Bot"]];
  return names.map(([sym, nm], i) => {
    const liq = [8e3, 4.2e4, 3.1e5, 1.8e4, 9.5e5, 6.2e4, 2.4e5, 5e3, 1.4e5, 3.3e4, 7.8e5, 1.1e4][i];
    const turn = 0.4 + rnd() * 6;
    const buys = Math.round(30 + rnd() * 900), sells = Math.round(buys * (0.4 + rnd() * 1.1));
    const p = { pool: "demo" + i, name: sym + " / WETH", sym, tokenName: nm,
      addr: "0x" + (i + 1).toString(16).padStart(40, "d"),
      price: 0.0001 * (1 + rnd() * 40), liq, vol24: liq * turn, fdv: liq * (8 + rnd() * 40),
      buys, sells, buyers: Math.round(buys * (0.35 + rnd() * 0.5)), sellers: Math.round(sells * 0.5),
      chg24: (rnd() - 0.35) * 180, chg1h: (rnd() - 0.5) * 40,
      ageH: [3, 19, 260, 7, 900, 44, 130, 1, 72, 11, 1400, 2][i] };
    const bad = [1, 0, 0, 1, 0, 0, 0, 1, 0, 1, 0, 1][i];
    const g = {
      is_honeypot: i === 7 ? "1" : "0", cannot_sell_all: i === 11 ? "1" : "0",
      buy_tax: bad ? "0." + (10 + Math.round(rnd() * 20)) : "0.0" + Math.round(rnd() * 3),
      sell_tax: bad ? "0." + (15 + Math.round(rnd() * 25)) : "0.0" + Math.round(rnd() * 4),
      is_open_source: bad && rnd() > 0.5 ? "0" : "1", is_proxy: rnd() > 0.75 ? "1" : "0",
      is_mintable: bad ? "1" : "0", hidden_owner: i === 3 ? "1" : "0",
      transfer_pausable: i === 9 ? "1" : "0", can_take_back_ownership: bad && rnd() > 0.6 ? "1" : "0",
      slippage_modifiable: bad ? "1" : "0", selfdestruct: "0", is_blacklisted: bad ? "1" : "0",
      holder_count: String(Math.round(bad ? 20 + rnd() * 150 : 400 + rnd() * 9000)),
      holders: Array.from({ length: 10 }, () => ({ percent: String((bad ? 0.06 : 0.018) * (0.5 + rnd())), is_locked: 0 })),
      lp_holders: [{ percent: bad ? "0.15" : "0.92", is_locked: 1, address: "0x1" }],
    };
    const s = securityScore(g, net === "solana");
    return { ...p, ...heatScore(p), sec: s.score, flags: s.flags, top10: s.top10, top1: s.top1,
      holders: s.holders, lockedPct: s.lockedPct, holderGrowth: null, histLen: 0, histFrom: null, raw: g };
  });
}

export { BUCKETS, GP_BASE, GT_BASE, NETWORKS, bucketOf, chainFetch, chainGate, enqueueOn, heatScore, isOn, makeChainDemo, parsePool, pctNum, securityScore };
