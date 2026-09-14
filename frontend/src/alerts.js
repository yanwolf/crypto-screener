import { BLADE } from "./blades.js";
import { BEAR_STAGES, STAGES } from "./constants.js";
import { C } from "./theme.js";
import { fmtPrice, fmtX } from "./util.js";

/* ════ 提醒設定預設值 ════════════════════════════════════ */
const DEFAULT_ALERT = {
  on: false,
  scope: "watch",              // watch | scanned
  cooldownMin: 60,             // 同一檔的最短提醒間隔
  minDelta: 8,                 // 評分要再提高多少才值得重新提醒
  breakoutPct: 1.5,            // 價格要突破多少百分比才算新的突破
  bull: { minRvol: 2, minLiq: 50, maxChase: 70, minRadar: 65, requireBlade: true },
  bear: { minStruct: 75, maxVolRatio: 0.8, minLiq: 50, maxStopPct: 12, minBear: 60, requireBlade: true },
  ch: { browser: true, useServer: false, tgToken: "", tgChat: "", discord: "", email: "" },
};

/* ════ 提醒條件檢查 ══════════════════════════════════════ */
function bullGate(r, c) {
  const checks = [
    ["量能放大", r.rvol7 >= c.minRvol, `量能 ${fmtX(r.rvol7)}，門檻 ${c.minRvol}x`],
    ["價格走強", (r.m24 ?? 0) > 0 && (r.pcTag === "價漲量增" || r.brk20),
      `${r.pcTag}${r.brk20 ? " · 站上 20 日新高" : ""}`],
    ["流動性合格", (r.liq ?? 0) >= c.minLiq, `流動性 ${(r.liq ?? 0).toFixed(0)}，門檻 ${c.minLiq}`],
    ["追高風險可控", (r.riskChase ?? 100) <= c.maxChase, `追高風險 ${(r.riskChase ?? 0).toFixed(0)}，上限 ${c.maxChase}`],
    ["雷達分數達標", (r.radar ?? 0) >= c.minRadar, `雷達分數 ${(r.radar ?? 0).toFixed(0)}，門檻 ${c.minRadar}`],
  ];
  if (c.requireBlade) checks.push(["三刀流站上綠橘",
    r.bladeState === "attackLong" || r.bladeState === "takeLong",
    `三刀流 ${(BLADE[r.bladeState] || BLADE.none).label}`]);
  return { pass: checks.every((x) => x[1]), checks, score: r.radar ?? 0 };
}
function bearGate(r, c) {
  const rebFail = r.bearStage === "rebound"
    ? r.volRatio != null && r.volRatio < c.maxVolRatio
    : r.bearStage === "drop" && r.volExpand >= 1.1;
  const checks = [
    ["趨勢向下", (r.structScore ?? 0) >= c.minStruct, `空頭結構 ${(r.structScore ?? 0).toFixed(0)}/100`],
    ["反彈失敗或賣壓延續", rebFail,
      r.bearStage === "rebound" ? `反彈量僅下跌量的 ${(r.volRatio != null ? r.volRatio * 100 : 0).toFixed(0)}%`
        : `下跌量能 ${(r.volExpand ?? 0).toFixed(2)} 倍`],
    ["流動性合格", (r.liq ?? 0) >= c.minLiq, `流動性 ${(r.liq ?? 0).toFixed(0)}，門檻 ${c.minLiq}`],
    ["止損位置清晰", r.stop != null && r.stopPct != null && r.stopPct <= c.maxStopPct,
      r.stopPct != null ? `止損距入場 ${r.stopPct.toFixed(1)}%，上限 ${c.maxStopPct}%` : "無有效止損位"],
    ["空頭評分達標", (r.bear ?? 0) >= c.minBear, `空頭評分 ${(r.bear ?? 0).toFixed(0)}，門檻 ${c.minBear}`],
  ];
  if (c.requireBlade) checks.push(["三刀流跌破綠橘",
    r.bladeState === "attackShort" || r.bladeState === "coverShort",
    `三刀流 ${(BLADE[r.bladeState] || BLADE.none).label}`]);
  return { pass: checks.every((x) => x[1]), checks, score: r.bear ?? 0 };
}

/* ════ 訊號狀態機 ════════════════════════════════════════
   同一個訊號只通知一次；只有這四種「狀態明顯改變」才重新通知：
   first 首次成立 / upgrade 評分再提高 / breakout 價格完成突破 / restart 回調後再啟動
   ════════════════════════════════════════════════════════ */
const EVENT = {
  first:    { label: "首次觸發", color: C.teal },
  upgrade:  { label: "評分提高", color: C.gold },
  breakout: { label: "完成突破", color: C.green },
  restart:  { label: "回調後再啟動", color: C.violet },
};

function evaluateSignals(rows, cfg, states, now) {
  const events = [];
  const next = { ...states };
  rows.forEach((r) => {
    if (!r.scanned) return;
    ["bull", "bear"].forEach((side) => {
      const g = side === "bull" ? bullGate(r, cfg.bull) : bearGate(r, cfg.bear);
      const key = r.id + ":" + side;
      const st = next[key];
      const price = r.price;

      if (!g.pass) {
        if (st) next[key] = { ...st, cooled: true };     // 條件失效，標記為冷卻，之後再成立算「再啟動」
        return;
      }
      const cool = st && now - st.lastTs < cfg.cooldownMin * 60000;
      let type = null;
      if (!st) type = "first";
      else if (st.cooled) type = "restart";
      else if (g.score >= st.peakScore + cfg.minDelta) type = "upgrade";
      else if (side === "bull" && price > st.refPrice * (1 + cfg.breakoutPct / 100) && r.brk20) type = "breakout";
      else if (side === "bear" && price < st.refPrice * (1 - cfg.breakoutPct / 100) && r.brokeSupport) type = "breakout";

      if (!type || (cool && type !== "restart")) {
        if (st) next[key] = { ...st, peakScore: Math.max(st.peakScore, g.score) };
        return;
      }
      next[key] = {
        firstTs: st ? st.firstTs : now, firstPrice: st ? st.firstPrice : price,
        firstScore: st ? st.firstScore : g.score,
        lastTs: now, refPrice: price, peakScore: Math.max(st ? st.peakScore : 0, g.score), cooled: false,
      };
      events.push({
        id: r.id, sym: r.sym, name: r.name, side, type, ts: now, price, score: g.score,
        stage: side === "bull" ? STAGES[r.stage].label : BEAR_STAGES[r.bearStage].label,
        checks: g.checks.map((c) => c[2]),
        // 空頭的進場區／止損只屬於空頭訊號；多頭改帶三刀流的多方計畫
        entryLo: side === "bear" ? (r.entryLo ?? null) : null,
        entryHi: side === "bear" ? (r.entryHi ?? null) : null,
        stop: side === "bear" ? (r.stop ?? null) : null,
        plan: r.bladePlan && ((side === "bull" && r.bladePlan.side === "多") ||
              (side === "bear" && r.bladePlan.side === "空")) ? r.bladePlan : null,
        blade: r.bladeState ? { state: r.bladeState, ma20: r.ma20, ma60: r.ma60,
              ma240: r.ma240, dGreen: r.dGreen, slope20: r.slope20 } : null,
        firstTs: next[key].firstTs, firstPrice: next[key].firstPrice, firstScore: next[key].firstScore,
        maxUp: 0, maxDown: 0, verdict: "open", hitTs: null,
      });
    });
  });
  return { events, next };
}

function alertText(e) {
  const bull = e.side === "bull";
  const title = `${e.sym} ${bull ? "多頭" : "空頭"} · ${EVENT[e.type].label}`;
  const L = [`${bull ? "▲ 做多方向" : "▼ 做空方向"}　${e.sym}　${fmtPrice(e.price)}`, e.name, "",
    `評分 ${e.score.toFixed(0)}　階段 ${e.stage}`];
  if (e.blade) {
    L.push(`三刀流 ${(BLADE[e.blade.state] || BLADE.none).label}` +
      `（距小綠 ${e.blade.dGreen >= 0 ? "+" : ""}${e.blade.dGreen.toFixed(1)}%，小藍斜率${e.blade.slope20 >= 0 ? "正" : "負"}）`);
  }
  L.push("", "── 進出場參考 ──");
  if (e.plan) L.push(`進場　${e.plan.entry}`, `出場　${e.plan.exit}`, `停損　${e.plan.stop}`);
  else if (e.entryLo) L.push(`進場　${fmtPrice(e.entryLo)}–${fmtPrice(e.entryHi)}`, `停損　${fmtPrice(e.stop)}`);
  else L.push("三刀流未給明確位置，先觀望");
  if (e.blade) L.push(`均線　小綠 ${fmtPrice(e.blade.ma60)}　小橘 ${fmtPrice(e.blade.ma240)}　小藍 ${fmtPrice(e.blade.ma20)}`);
  L.push("", "── 觸發條件 ──", ...e.checks.map((c) => "· " + c));
  if (e.type !== "first") L.push("", `首次觸發 ${fmtPrice(e.firstPrice)} / ${e.firstScore.toFixed(0)} 分`);
  L.push("", "僅供技術分析參考，不構成投資建議。");
  return { title, text: L.join("\n") };
}

export { DEFAULT_ALERT, EVENT, alertText, bearGate, bullGate, evaluateSignals };
