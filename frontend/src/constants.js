import { C } from "./theme.js";

/* ════ 常數 ══════════════════════════════════════════════ */
const STABLE = new Set(["usdt","usdc","dai","busd","tusd","usde","fdusd","usdd","pyusd","frax","lusd","gusd","usdp","usds","eurc","eurt","eur","usd1","xaut","paxg","susds","usdf","usdy","rlusd","usdg","buidl","usdtb"]);
const WRAPPED = new Set(["wbtc","weth","steth","wsteth","cbeth","reth","weeth","ezeth","rseth","jitosol","msol","bnsol","jupsol","lbtc","cbbtc","wbeth","solvbtc","bsc-usd","wbnb","stbtc","meth","ethx","sweth","frxeth","sfrxeth","clbtc","wsol","wpol"]);

const STAGES = {
  quiet:    { label: "沉寂整理", color: C.muted,   score: 25, say: "量能與價格都平淡，還沒有資金進場的跡象。" },
  ignite:   { label: "剛啟動",   color: C.teal,    score: 100, say: "量先放大、價格才剛從低檔轉強，是資金進場的初期。" },
  accel:    { label: "加速上漲", color: C.green,   score: 85, say: "帶量往上推，價格已接近區間高點，趨勢正在延伸。" },
  hot:      { label: "高位過熱", color: C.gold,    score: 45, say: "漲幅與換手同時衝到極端，情緒過熱，波動會很大。" },
  dump:     { label: "放量下跌", color: C.red,     score: 12, say: "量放大但價格在跌，通常是有人趁流動性出場。" },
  fade:     { label: "退潮低迷", color: C.violet,  score: 10, say: "價格走弱、量能萎縮，資金已經離開。" },
  active:   { label: "溫和活躍", color: C.bone,    score: 55, say: "有交易但不算異常，屬於正常波動範圍。" },
  unknown:  { label: "未掃描",   color: C.muted,   score: null, say: "尚未取得歷史量能，先執行雷達掃描。" },
};

const BEAR_STAGES = {
  drop:    { label: "加速下跌", color: C.red,   say: "價格已經跌破 EMA20，高點與低點都一路往下，而且下跌時成交量放大，賣壓還在延續。" },
  rebound: { label: "反彈等待", color: C.gold,  say: "跌深後反彈到壓力帶附近，但反彈的成交量比下跌時小，買盤力道不足，有機會再往下。" },
  none:    { label: "空頭不成立", color: C.muted, say: "目前結構沒有轉空，或跌勢還沒有明確成形，不適合當作做空對象。" },
  unknown: { label: "未掃描",   color: C.muted, say: "尚未取得歷史資料，先執行雷達掃描。" },
};

const METRICS = [
  { key: "rvol7", label: "量能倍數", hint: "現在的成交量是近 7 日平均的幾倍" },
  { key: "turn",  label: "成交額市值比", hint: "24 小時成交額 ÷ 流通市值" },
  { key: "m24",   label: "24 小時動量", hint: "近一日漲跌幅" },
  { key: "m7",    label: "7 日動量",   hint: "近一週漲跌幅" },
  { key: "m30",   label: "30 日動量",  hint: "近一月漲跌幅" },
  { key: "rs",    label: "相對強弱 RS", hint: "30 日報酬減去 BTC 30 日報酬" },
  { key: "vol",   label: "成交量",     hint: "24 小時美元成交額" },
  { key: "mcap",  label: "市值",       hint: "流通市值" },
  { key: "d90",   label: "貼近 90 日高", hint: "現價距 90 日最高價" },
  { key: "liq",   label: "流動性質量",  hint: "成交規模、量能穩定度與市值的綜合評估" },
];
const PRESETS = {
  "量能優先": { rvol7: 10, turn: 6, m24: 4, m7: 3, m30: 0, rs: 3, vol: 3, mcap: 0, d90: 3, liq: 5 },
  "動量突破": { rvol7: 5, turn: 4, m24: 3, m7: 6, m30: 8, rs: 9, vol: 3, mcap: 0, d90: 8, liq: 3 },
  "高流動性藍籌": { rvol7: 3, turn: 3, m24: 0, m7: 3, m30: 4, rs: 4, vol: 9, mcap: 9, d90: 2, liq: 8 },
  "小市值黑馬": { rvol7: 8, turn: 8, m24: 2, m7: 7, m30: 5, rs: 6, vol: 3, mcap: 8, d90: 3, liq: 4 },
};
const PRESET_DIRS = { "量能優先": {}, "動量突破": {}, "高流動性藍籌": {}, "小市值黑馬": { mcap: -1 } };
const ZERO_W = Object.fromEntries(METRICS.map((m) => [m.key, 0]));
const ZERO_D = Object.fromEntries(METRICS.map((m) => [m.key, 1]));

/* 一鍵篩選 — 多頭 */
const QUICK_BULL = [
  {
    id: "surge", name: "量能突然放大", need: true, sort: "rvol7",
    desc: "成交量是平日的 2 倍以上，而且流動性過得去",
    test: (r) => r.rvol7 >= 2 && r.liq >= 35,
  },
  {
    id: "growth", name: "潛力成長幣", need: true, sort: "radar",
    desc: "量剛放大、價格還在區間中段，尚未追到高點",
    test: (r) => r.rvol7 >= 1.5 && r.d90 <= -8 && r.d90 >= -45 && r.m7 > 0 && r.liq >= 40,
  },
  {
    id: "smallcap", name: "小市值爆發", need: true, sort: "rvol7",
    desc: "市值 3 億美元以下、換手率高且今天明顯放量",
    test: (r) => r.mcap <= 3e8 && r.rvol7 >= 2 && r.turn >= 8 && r.vol >= 2e6,
  },
  {
    id: "blue", name: "強勢主流幣", need: true, sort: "radar",
    desc: "市值 20 億以上、強過大盤、位置仍在高檔附近",
    test: (r) => r.mcap >= 2e9 && r.rs > 0 && r.d90 >= -12 && r.rvol7 >= 1.2,
  },
  {
    id: "blade_long", name: "三刀流多方攻擊", need: true, sort: "radar",
    desc: "60 分 K 站上小綠與小橘、小藍正斜率，三把刀同方向往上",
    test: (r) => r.bladeState === "attackLong",
  },
  {
    id: "blade_pull", name: "多方回踩小綠", need: true, sort: "radar",
    desc: "方向仍偏多，價格回到小綠附近，是三刀流較理想的進場位置",
    test: (r) => (r.bladeState === "attackLong" || r.bladeState === "correction") &&
      r.dGreen != null && r.dGreen > -2.5 && r.dGreen < 2.5,
  },
  {
    id: "hot", name: "高風險過熱幣", need: true, sort: "riskChase",
    desc: "追高風險偏高，列出來是提醒別碰，不是叫你買",
    test: (r) => r.riskChase >= 65 || r.rvol7 >= 6 || r.turn >= 80,
  },
];

/* 一鍵篩選 — 空頭 */
const QUICK_BEAR = [
  { id: "accel", name: "加速下跌中", sort: "bear",
    desc: "第一階段：跌破 EMA20、更低高點與更低低點、下跌帶量，賣壓正在延續",
    test: (r) => r.bearStage === "drop" },
  { id: "rebound", name: "反彈到壓力", sort: "bear",
    desc: "第二階段：跌深後反彈到 EMA20 或 0.5–0.618 回撤帶，但反彈量縮、陽線轉弱",
    test: (r) => r.bearStage === "rebound" },
  { id: "broke", name: "剛跌破支撐", sort: "bear",
    desc: "價格跌破前一個樞紐低點，原本的支撐翻成壓力",
    test: (r) => r.brokeSupport && r.bearStage !== "none" },
  { id: "weakmajor", name: "弱勢主流幣", sort: "bear",
    desc: "市值 10 億美元以上、流動性足夠，但趨勢已經轉空",
    test: (r) => r.mcap >= 1e9 && r.bearStage !== "none" && r.liq >= 50 },
  { id: "blade_short", name: "三刀流空方攻擊", sort: "bear",
    desc: "60 分 K 跌破小綠與小橘、小藍負斜率，三把刀同方向往下",
    test: (r) => r.bladeState === "attackShort" },
  { id: "blade_bounce", name: "空方反彈到小綠", sort: "bear",
    desc: "空方架構下反彈到小綠附近，是三刀流的加空位置",
    test: (r) => r.bladeState === "attackShort" && r.dGreen != null && r.dGreen > -2.5 && r.dGreen < 1 },
  { id: "oversold", name: "超跌勿追（警示）", sort: "chaseShort",
    desc: "已經連續大跌、乖離過大，這個位置追空的風險最高，列出來是要你避開",
    test: (r) => r.chaseShort >= 65 },
];

export { BEAR_STAGES, METRICS, PRESETS, PRESET_DIRS, QUICK_BEAR, QUICK_BULL, STABLE, STAGES, WRAPPED, ZERO_D, ZERO_W };
