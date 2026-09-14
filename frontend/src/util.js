const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));


/* ════ 格式化 ════════════════════════════════════════════ */
const fmtUsd = (v) => {
  if (v == null || !isFinite(v)) return "—";
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
  return `$${v.toFixed(0)}`;
};
const fmtPrice = (v) => {
  if (v == null) return "—";
  if (v >= 1000) return "$" + v.toLocaleString("en-US", { maximumFractionDigits: 0 });
  if (v >= 1) return "$" + v.toFixed(2);
  if (v >= 0.01) return "$" + v.toFixed(4);
  return "$" + v.toPrecision(3);
};
const fmtPct = (v, d = 1) => (v == null || !isFinite(v) ? "—" : (v > 0 ? "+" : "") + v.toFixed(d) + "%");
const fmtX = (v) => (v == null ? "—" : v.toFixed(2) + "x");

function pctRank(values) {
  const pairs = [];
  values.forEach((v, i) => { if (v != null && isFinite(v)) pairs.push([v, i]); });
  pairs.sort((a, b) => a[0] - b[0]);
  const out = new Array(values.length).fill(null);
  const n = pairs.length;
  pairs.forEach(([, i], r) => { out[i] = n <= 1 ? 50 : (r / (n - 1)) * 100; });
  return out;
}
const mean = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : null);
const stdev = (a) => {
  if (a.length < 2) return 0;
  const m = mean(a);
  return Math.sqrt(a.reduce((s, v) => s + (v - m) ** 2, 0) / (a.length - 1));
};



/* ════ 本機儲存（僅在下載後的本機頁面可持久化） ═════════ */
const store = {
  ok: (() => { try { localStorage.setItem("cgs:t", "1"); localStorage.removeItem("cgs:t"); return true; } catch (e) { return false; } })(),
  get(k, d) { try { const v = localStorage.getItem("cgs:" + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("cgs:" + k, JSON.stringify(v)); return "ok"; } catch (e) { return "fail"; } },
  /* 掃描快取可能很大。寫不進去時先丟掉最舊的三成再試一次，
     總比整包存不進去、重整後全沒了要好。 */
  setScans(v) {
    if (!this.ok) return "nostore";
    if (this.set("scans", v) === "ok") return "ok";
    const ent = Object.entries(v).filter(([, x]) => x && x.ts).sort((a, b) => b[1].ts - a[1].ts);
    const keep = Object.fromEntries(ent.slice(0, Math.max(30, Math.floor(ent.length * 0.7))));
    return this.set("scans", keep) === "ok" ? "trimmed" : "quota";
  },
};


const r1 = (v) => (v == null || !Number.isFinite(v) ? v : Math.round(v * 10) / 10);

const pctRankOf = (arr, v) => {          // 單一數值在序列中的百分位（與上方 pctRank 不同，勿混用）
  const a = (arr || []).filter((x) => x != null && Number.isFinite(x));
  if (a.length < 5 || v == null || !Number.isFinite(v)) return null;
  return (100 * a.filter((x) => x < v).length) / a.length;
};

export { clamp, fmtPct, fmtPrice, fmtUsd, fmtX, mean, pctRank, pctRankOf, r1, sleep, stdev, store };
