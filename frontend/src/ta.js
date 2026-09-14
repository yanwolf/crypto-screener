import { mean } from "./util.js";

/* ════ 技術分析工具 ══════════════════════════════════════ */
/* 用逐時價格重建日線 OHLC，才有辦法做樞紐點、實體強弱與 ATR */
function toDaily(chart) {
  const days = new Map();
  (chart.prices || []).forEach(([t, p]) => {
    if (!isFinite(p)) return;
    const d = Math.floor(t / 86400000);
    const o = days.get(d);
    if (!o) days.set(d, { d, o: p, h: p, l: p, c: p, v: 0 });
    else { o.h = Math.max(o.h, p); o.l = Math.min(o.l, p); o.c = p; }
  });
  (chart.total_volumes || []).forEach(([t, v]) => {
    const o = days.get(Math.floor(t / 86400000));
    if (o && isFinite(v)) o.v = v;            // 當日最後一筆 24 小時滾動量
  });
  return [...days.values()].sort((a, b) => a.d - b.d);
}

function emaLast(arr, n) {
  if (!arr.length) return null;
  const k = 2 / (n + 1);
  let e = arr[0];
  for (let i = 1; i < arr.length; i++) e = arr[i] * k + e * (1 - k);
  return e;
}
function rsiLast(c, n = 14) {
  if (c.length < n + 2) return 50;
  let g = 0, l = 0;
  for (let i = 1; i <= n; i++) { const d = c[i] - c[i - 1]; d >= 0 ? (g += d) : (l -= d); }
  g /= n; l /= n;
  for (let i = n + 1; i < c.length; i++) {
    const d = c[i] - c[i - 1];
    g = (g * (n - 1) + Math.max(d, 0)) / n;
    l = (l * (n - 1) + Math.max(-d, 0)) / n;
  }
  return l === 0 ? 100 : 100 - 100 / (1 + g / l);
}
function atrLast(bars, n = 14) {
  if (bars.length < n + 1) return null;
  const tr = [];
  for (let i = 1; i < bars.length; i++) {
    const b = bars[i], p = bars[i - 1].c;
    tr.push(Math.max(b.h - b.l, Math.abs(b.h - p), Math.abs(b.l - p)));
  }
  return mean(tr.slice(-n));
}
/* 樞紐高低點：左右各 k 根都不超過自己 */
function pivots(bars, k = 3) {
  const hi = [], lo = [];
  for (let i = k; i < bars.length - k; i++) {
    let isH = true, isL = true;
    for (let j = i - k; j <= i + k; j++) {
      if (bars[j].h > bars[i].h) isH = false;
      if (bars[j].l < bars[i].l) isL = false;
    }
    if (isH) hi.push(i);
    if (isL) lo.push(i);
  }
  return { hi, lo };
}

export { atrLast, emaLast, pivots, rsiLast, toDaily };
