import React, { useState } from "react";
import { BLADE } from "../blades.js";
import { BEAR_STAGES, STAGES } from "../constants.js";
import { C, FONT } from "../theme.js";
import { clamp, fmtX } from "../util.js";

/* ════ 小元件 ════════════════════════════════════════════ */
function Sparkline({ data, color, w = 74, h = 22 }) {
  if (!data || data.length < 3) return <span style={{ color: C.muted }}>—</span>;
  const step = Math.max(1, Math.floor(data.length / 60));
  const pts = data.filter((_, i) => i % step === 0);
  const min = Math.min(...pts), max = Math.max(...pts), rng = max - min || 1;
  const d = pts.map((p, i) => `${(i / (pts.length - 1)) * w},${h - ((p - min) / rng) * (h - 2) - 1}`).join(" ");
  return <svg width={w} height={h} style={{ display: "block" }}><polyline points={d} fill="none" stroke={color} strokeWidth="1.2" /></svg>;
}

function RangeBar({ low, high, price, w = 96 }) {
  if (low == null || high == null || high <= low) return <span style={{ color: C.muted, fontSize: 11 }}>未掃描</span>;
  const pos = clamp((price - low) / (high - low), 0, 1);
  const x = pos * (w - 2) + 1;
  const dist = (price / high - 1) * 100;
  const near = dist > -8;
  return (
    <div className="flex items-center gap-2">
      <svg width={w} height={16}>
        <line x1="1" y1="8" x2={w - 1} y2="8" stroke={C.line} strokeWidth="4" strokeLinecap="round" />
        <line x1={x} y1="8" x2={w - 1} y2="8" stroke={near ? C.gold : C.teal} strokeWidth="4" strokeLinecap="round" opacity="0.35" />
        <circle cx={x} cy="8" r="3.4" fill={near ? C.gold : C.teal} />
        <line x1={w - 1} y1="3" x2={w - 1} y2="13" stroke={C.gold} strokeWidth="1.4" />
      </svg>
      <span style={{ fontFamily: FONT.data, fontSize: 11.5, color: near ? C.gold : C.bone }}>{dist.toFixed(1)}%</span>
    </div>
  );
}

/* 量能倍數的視覺化：以 1 倍為基準線 */
function RvolBar({ v }) {
  if (v == null) return <span style={{ color: C.muted }}>—</span>;
  const w = 62, base = w * 0.28;                    // 1 倍的位置
  const len = clamp((Math.log(Math.max(v, 0.15)) / Math.log(6)) * (w - base) + base, 3, w);
  const col = v >= 3 ? C.gold : v >= 1.5 ? C.teal : v < 0.8 ? C.muted : C.bone;
  return (
    <div className="flex items-center gap-1.5">
      <svg width={w} height={14}>
        <rect x="0" y="4" width={len} height="6" rx="1.5" fill={col} opacity="0.75" />
        <line x1={base} y1="1" x2={base} y2="13" stroke={C.muted} strokeWidth="1" strokeDasharray="2 2" />
      </svg>
      <span style={{ fontFamily: FONT.data, fontSize: 11.5, color: col }}>{v.toFixed(1)}x</span>
    </div>
  );
}

function ScoreCell({ score, color }) {
  if (score == null) return <span style={{ color: C.muted }}>—</span>;
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-12 rounded-sm" style={{ background: C.line }}>
        <div className="h-1.5 rounded-sm" style={{ width: `${score}%`, background: color || C.gold }} />
      </div>
      <span style={{ fontFamily: FONT.data, fontSize: 12 }}>{score.toFixed(0)}</span>
    </div>
  );
}

function StageTag({ stage }) {
  const s = STAGES[stage] || STAGES.unknown;
  return (
    <span className="px-1.5 py-0.5 rounded whitespace-nowrap"
      style={{ fontSize: 11, color: s.color, border: `1px solid ${s.color}`, opacity: stage === "unknown" ? 0.5 : 1 }}>
      {s.label}
    </span>
  );
}


function BearTag({ stage }) {
  const b = BEAR_STAGES[stage] || BEAR_STAGES.unknown;
  return (
    <span className="px-1.5 py-0.5 rounded whitespace-nowrap"
      style={{ fontSize: 11, color: b.color, border: `1px solid ${b.color}`, opacity: stage === "none" || stage === "unknown" ? 0.55 : 1 }}>
      {b.label}
    </span>
  );
}

/* 空頭分布圖：橫軸回撤位置、縱軸反彈量能，右下角是理想的等待做空區 */
function BearMap({ rows, onPick }) {
  const [hover, setHover] = useState(null);
  const W = 800, H = 300, P = { l: 54, r: 16, t: 14, b: 34 };
  const pts = rows.filter((r) => r.scanned && (r.bearStage === "drop" || r.bearStage === "rebound")).slice(0, 160);
  const px = (v) => P.l + (clamp(v, 0, 1) / 1) * (W - P.l - P.r);
  const py = (v) => H - P.b - (clamp(v, 0, 2) / 2) * (H - P.t - P.b);
  const rad = (m) => clamp(Math.sqrt((m || 1e6) / 1e9) * 4.2, 3, 17);
  if (!pts.length) {
    return <div className="py-10 text-center" style={{ color: C.muted, fontSize: 12.5 }}>
      已掃描的幣裡沒有成立的空頭結構。擴大掃描範圍，或切回多頭雷達。
    </div>;
  }
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1" style={{ minHeight: 18 }}>
        <span style={{ fontSize: 10.5, color: C.muted }}>橫軸 反彈回撤比例 · 縱軸 反彈量 ÷ 下跌量 · 泡泡大小為市值</span>
        {hover && (
          <span style={{ fontFamily: FONT.data, fontSize: 11.5, color: C.gold }}>
            {hover.sym} · 回撤 {(hover.retrace * 100).toFixed(0)}% · 量比 {hover.volRatio ? hover.volRatio.toFixed(2) : "—"}x · {BEAR_STAGES[hover.bearStage].label}
          </span>
        )}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height="300" style={{ display: "block" }}>
        <rect x={px(0.5)} y={P.t} width={px(0.618) - px(0.5)} height={H - P.t - P.b} fill={C.gold} opacity="0.08" />
        <text x={(px(0.5) + px(0.618)) / 2} y={P.t + 12} fill={C.gold} fontSize="10.5" textAnchor="middle" opacity="0.8">0.5–0.618</text>
        {[0, 0.25, 0.5, 0.618, 0.75, 1].map((v) => (
          <g key={v}>
            <line x1={px(v)} y1={P.t} x2={px(v)} y2={H - P.b} stroke={C.line} strokeDasharray="2 4" />
            <text x={px(v)} y={H - 12} fill={C.muted} fontSize="11" textAnchor="middle" fontFamily={FONT.data}>{(v * 100).toFixed(0)}%</text>
          </g>
        ))}
        {[0, 0.5, 1, 1.5, 2].map((v) => (
          <g key={v}>
            <line x1={P.l} y1={py(v)} x2={W - P.r} y2={py(v)} stroke={v === 1 ? C.muted : C.line} strokeDasharray={v === 1 ? "5 4" : "2 4"} />
            <text x={P.l - 8} y={py(v) + 4} fill={C.muted} fontSize="11" textAnchor="end" fontFamily={FONT.data}>{v}x</text>
          </g>
        ))}
        <text x={px(0.56)} y={py(0.35)} fill={C.red} fontSize="11" textAnchor="middle" opacity="0.8">反彈到壓力且量縮 · 較佳的等待區</text>
        {pts.map((r, i) => (
          <circle key={i} cx={px(r.retrace)} cy={py(r.volRatio == null ? 1 : r.volRatio)} r={rad(r.mcap)}
            fill={BEAR_STAGES[r.bearStage].color} fillOpacity={0.16 + (r.bear || 0) / 300}
            stroke={BEAR_STAGES[r.bearStage].color} strokeOpacity="0.7" style={{ cursor: "pointer" }}
            onMouseEnter={() => setHover(r)} onMouseLeave={() => setHover(null)} onClick={() => onPick(r)} />
        ))}
      </svg>
      <div className="flex flex-wrap gap-x-3 gap-y-1 mt-1" style={{ fontSize: 10.5 }}>
        <span style={{ color: C.red }}>● 加速下跌</span>
        <span style={{ color: C.gold }}>● 反彈等待</span>
        <span style={{ color: C.muted }}>量比低於 1 代表反彈的量比下跌時小</span>
      </div>
    </div>
  );
}

function BladeTag({ state, tangle }) {
  const b = BLADE[state] || BLADE.none;
  return (
    <span className="px-1.5 py-0.5 rounded whitespace-nowrap"
      style={{ fontSize: 11, color: b.color, border: `1px solid ${b.color}`, opacity: state === "none" ? 0.5 : 1 }}
      title={b.say}>
      {b.label}{tangle ? " ·糾結" : ""}
    </span>
  );
}

function RiskTag({ r }) {
  if (r == null) return <span style={{ color: C.muted }}>—</span>;
  const [t, c] = r >= 70 ? ["高", C.red] : r >= 45 ? ["中", C.gold] : ["低", C.teal];
  return <span style={{ fontFamily: FONT.data, fontSize: 11.5, color: c }}>{t} {r.toFixed(0)}</span>;
}

function Stat({ label, value, sub, accent }) {
  return (
    <div className="px-3 py-2.5 rounded" style={{ background: C.panel, border: `1px solid ${C.line}` }}>
      <div style={{ fontSize: 10.5, color: C.muted, letterSpacing: "0.08em" }}>{label}</div>
      <div style={{ fontFamily: FONT.data, fontSize: 19, color: accent || C.bone, marginTop: 2 }}>{value}</div>
      {sub && <div style={{ fontSize: 10.5, color: C.muted, marginTop: 1 }}>{sub}</div>}
    </div>
  );
}

/* 量能－位置雷達圖 */
function RadarMap({ rows, onPick }) {
  const [hover, setHover] = useState(null);
  const W = 800, H = 300, P = { l: 54, r: 16, t: 14, b: 34 };
  const pts = rows.filter((r) => r.scanned && r.d90 != null).slice(0, 160);
  const px = (v) => P.l + ((clamp(v, -100, 2) + 100) / 102) * (W - P.l - P.r);
  const py = (v) => {
    const y = clamp(Math.log(clamp(v, 0.2, 8)) / Math.log(8), -0.55, 1);
    return H - P.b - ((y + 0.55) / 1.55) * (H - P.t - P.b);
  };
  const rad = (m) => clamp(Math.sqrt((m || 1e6) / 1e9) * 4.2, 3, 17);
  if (!pts.length) {
    return <div className="py-10 text-center" style={{ color: C.muted, fontSize: 12.5 }}>
      還沒有掃描資料。按上方「開始雷達掃描」後，這裡會畫出每檔幣的量能與位置分布。
    </div>;
  }
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1" style={{ minHeight: 18 }}>
        <span style={{ fontSize: 10.5, color: C.muted }}>橫軸 距 90 日高 · 縱軸 量能倍數（對數）· 泡泡大小為市值</span>
        {hover && (
          <span style={{ fontFamily: FONT.data, fontSize: 11.5, color: C.gold }}>
            {hover.sym} · {fmtX(hover.rvol7)} · 距高 {hover.d90.toFixed(1)}% · {STAGES[hover.stage].label}
          </span>
        )}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height="300" style={{ display: "block" }}>
        {[-100, -80, -60, -40, -20, 0].map((v) => (
          <g key={v}>
            <line x1={px(v)} y1={P.t} x2={px(v)} y2={H - P.b} stroke={C.line} strokeDasharray="2 4" />
            <text x={px(v)} y={H - 12} fill={C.muted} fontSize="11" textAnchor="middle" fontFamily={FONT.data}>{v}%</text>
          </g>
        ))}
        {[0.5, 1, 2, 4, 8].map((v) => (
          <g key={v}>
            <line x1={P.l} y1={py(v)} x2={W - P.r} y2={py(v)} stroke={v === 1 ? C.muted : C.line} strokeDasharray={v === 1 ? "5 4" : "2 4"} />
            <text x={P.l - 8} y={py(v) + 4} fill={C.muted} fontSize="11" textAnchor="end" fontFamily={FONT.data}>{v}x</text>
          </g>
        ))}
        <text x={px(-55)} y={py(3.4)} fill={C.teal} fontSize="11" textAnchor="middle" opacity="0.75">低檔放量 · 剛啟動</text>
        <text x={px(-8)} y={py(3.4)} fill={C.gold} fontSize="11" textAnchor="middle" opacity="0.75">高檔爆量 · 過熱</text>
        {pts.map((r, i) => (
          <circle key={i} cx={px(r.d90)} cy={py(r.rvol7)} r={rad(r.mcap)}
            fill={STAGES[r.stage].color} fillOpacity={0.16 + (r.radar || 0) / 300}
            stroke={STAGES[r.stage].color} strokeOpacity="0.7" style={{ cursor: "pointer" }}
            onMouseEnter={() => setHover(r)} onMouseLeave={() => setHover(null)} onClick={() => onPick(r)} />
        ))}
      </svg>
      <div className="flex flex-wrap gap-x-3 gap-y-1 mt-1" style={{ fontSize: 10.5 }}>
        {["ignite", "accel", "hot", "dump", "fade", "quiet", "active"].map((k) => (
          <span key={k} style={{ color: STAGES[k].color }}>● {STAGES[k].label}</span>
        ))}
      </div>
    </div>
  );
}

export { BearMap, BearTag, BladeTag, RadarMap, RangeBar, RiskTag, RvolBar, ScoreCell, Sparkline, StageTag, Stat };
