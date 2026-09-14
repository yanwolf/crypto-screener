import { C } from "./theme.js";

/* ════ 均線三刀流（60 分 K：20／60／240MA） ══════════════
   240MA 小橘 劉備 — 決定方向，多空分水嶺
   60MA  小綠 關羽 — 負責進出，站上做多、跌破做空
   20MA  小藍 張飛 — 負責收尾，斜率翻負多單下車、翻正空單回補
   ════════════════════════════════════════════════════════ */
const BLADE = {
  attackLong:  { label: "多方攻擊", color: C.red,    short: "三刀多攻",
                 say: "站上小綠也站上小橘，小藍還是正斜率，三把刀同方向，多單續抱。" },
  takeLong:    { label: "多單停利", color: C.gold,   short: "多單停利",
                 say: "還在小綠之上，但小藍斜率翻負——張飛在收尾了，多單該分批下車。" },
  correction:  { label: "修正",     color: C.violet, short: "修正中",
                 say: "跌破小綠但沒破小橘，方向還是多方，這只是修正。等站回小綠再找多單，不要反手做空。" },
  rebound:     { label: "反彈",     color: C.teal,   short: "只是反彈",
                 say: "站上小綠但沒站上小橘，大方向仍偏空，這只是反彈。不要當成多頭起漲，空單可以先退但別急著翻多。" },
  attackShort: { label: "空方攻擊", color: C.green,  short: "三刀空攻",
                 say: "跌破小綠也跌破小橘，小藍負斜率，三把刀同方向往下，空單續抱。" },
  coverShort:  { label: "空單回補", color: C.gold,   short: "空單回補",
                 say: "還在小綠之下，但小藍斜率翻正——反彈要來了，空單先回補。" },
  none:        { label: "未掃描",   color: C.muted,  short: "—", say: "尚未取得 60 分 K 資料。" },
};

/* 逐時價格 → 60 分 K 收盤序列 */
function toHourly(chart) {
  const m = new Map();
  (chart.prices || []).forEach(([t, p]) => { if (p != null) m.set(Math.floor(t / 3600000), p); });
  return [...m.entries()].sort((a, b) => a[0] - b[0]).map((x) => x[1]);
}
function smaAt(a, n, back = 0) {
  const end = a.length - back;
  if (end < n) return null;
  let s = 0;
  for (let i = end - n; i < end; i++) s += a[i];
  return s / n;
}

function blades(chart) {
  const H = toHourly(chart);
  if (H.length < 250) return {};                       // 240MA 至少要 240 根 60 分 K
  const price = H[H.length - 1];
  const ma20 = smaAt(H, 20), ma60 = smaAt(H, 60), ma240 = smaAt(H, 240);
  const ma20b = smaAt(H, 20, 3), ma60b = smaAt(H, 60, 6), ma240b = smaAt(H, 240, 12);
  const slope20 = ma20b ? ((ma20 - ma20b) / ma20b) * 100 : 0;      // 近 3 根的斜率
  const slope60 = ma60b ? ((ma60 - ma60b) / ma60b) * 100 : 0;
  const slope240 = ma240b ? ((ma240 - ma240b) / ma240b) * 100 : 0;

  const aboveOrange = price > ma240, aboveGreen = price > ma60;
  const state = aboveOrange && aboveGreen ? (slope20 >= 0 ? "attackLong" : "takeLong")
    : aboveOrange && !aboveGreen ? "correction"
    : !aboveOrange && aboveGreen ? "rebound"
    : (slope20 <= 0 ? "attackShort" : "coverShort");

  return {
    ma20, ma60, ma240, slope20, slope60, slope240, bladeState: state,
    dGreen: ((price / ma60) - 1) * 100,
    dOrange: ((price / ma240) - 1) * 100,
    dBlue: ((price / ma20) - 1) * 100,
    // 小綠與小橘距離太近代表均線糾結，這種盤三刀流勝率會下降
    tangle: Math.abs(ma60 - ma240) / price * 100 < 0.6,
  };
}

/* 趨勢結構：EMA、樞紐、下跌波段、回撤位置、分段量能 */

export { BLADE, blades, smaAt, toHourly };
