import React, { useEffect, useState } from "react";
import { pctNum } from "../chain.js";
import { BearTag, BladeTag, StageTag } from "../components/Small.jsx";
import { C, FONT } from "../theme.js";
import { fmtPct, fmtPrice, fmtUsd, fmtX } from "../util.js";

/* ════ 主元件 ════════════════════════════════════════════ */

/* 觀察清單的手機卡片：多空兩個方向同時呈現 */
function WatchCard({ r, sigB, sigR, onPick, onStar, UP, DOWN }) {
  const tone = (v) => (v == null ? C.muted : v > 0 ? UP : v < 0 ? DOWN : C.bone);
  return (
    <div onClick={() => onPick(r)} className="px-3 py-2.5" style={{ borderBottom: `1px solid ${C.line}` }}>
      <div className="flex items-center gap-2">
        <button onClick={(e) => { e.stopPropagation(); onStar(r.id); }} style={{ color: C.gold, fontSize: 17, lineHeight: 1 }}>★</button>
        <span style={{ fontSize: 14 }}>{r.sym}</span>
        <span style={{ color: C.muted, fontSize: 11 }}>{r.name.slice(0, 10)}</span>
        <span className="ml-auto" style={{ fontFamily: FONT.data, fontSize: 14 }}>{fmtPrice(r.price)}</span>
        <span style={{ fontFamily: FONT.data, fontSize: 13, color: tone(r.m24), minWidth: 54, textAlign: "right" }}>{fmtPct(r.m24)}</span>
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 mt-2">
        <div className="flex items-center gap-1.5">
          <span style={{ fontSize: 10.5, color: UP, width: 16 }}>多</span>
          <StageTag stage={r.stage} />
          <span className="ml-auto" style={{ fontFamily: FONT.data, fontSize: 13, color: C.gold }}>{r.radar == null ? "—" : r.radar.toFixed(0)}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span style={{ fontSize: 10.5, color: DOWN, width: 16 }}>空</span>
          <BearTag stage={r.bearStage} />
          <span className="ml-auto" style={{ fontFamily: FONT.data, fontSize: 13, color: C.red }}>{r.bear == null ? "—" : r.bear.toFixed(0)}</span>
        </div>
      </div>
      <div className="flex items-center gap-3 mt-2" style={{ fontFamily: FONT.data, fontSize: 11.5, color: C.muted }}>
        <span>量能 <span style={{ color: (r.rvol7 ?? 0) >= 2 ? C.gold : C.bone }}>{fmtX(r.rvol7)}</span></span>
        <span style={{ fontFamily: FONT.body }}>
          {sigB || sigR
            ? `已觸發${sigB ? " 多" + new Date(sigB.lastTs).toLocaleDateString("zh-TW", { month: "numeric", day: "numeric" }) : ""}${sigR ? " 空" + new Date(sigR.lastTs).toLocaleDateString("zh-TW", { month: "numeric", day: "numeric" }) : ""}`
            : "尚未觸發訊號"}
        </span>
      </div>
    </div>
  );
}

/* 鏈上代幣的手機卡片 */
function ChainCard({ t, minSafe, onPick, UP, DOWN }) {
  const tax = t.raw ? Math.max(pctNum(t.raw.buy_tax) ?? 0, pctNum(t.raw.sell_tax) ?? 0) : null;
  const blocks = t.flags.filter((f) => f.level === "block").length;
  const highs = t.flags.filter((f) => f.level === "high").length;
  return (
    <div onClick={() => onPick(t)} className="px-3 py-2.5" style={{ borderBottom: `1px solid ${C.line}` }}>
      <div className="flex items-center gap-2">
        <span style={{ fontSize: 14 }}>{t.sym}</span>
        <span style={{ color: C.muted, fontSize: 11 }}>{t.tokenName.slice(0, 12)}</span>
        <span className="ml-auto" style={{ fontFamily: FONT.data, fontSize: 13 }}>{fmtPrice(t.price)}</span>
        <span style={{ fontFamily: FONT.data, fontSize: 12.5, color: t.chg24 > 0 ? UP : DOWN, minWidth: 54, textAlign: "right" }}>{fmtPct(t.chg24)}</span>
      </div>
      <div className="flex items-center gap-2 mt-2">
        <span style={{ fontSize: 10.5, color: C.muted, width: 26 }}>安全</span>
        <div className="flex-1 h-1.5 rounded-sm" style={{ background: C.line }}>
          <div className="h-1.5 rounded-sm" style={{ width: `${t.sec ?? 0}%`, background: (t.sec ?? 0) >= minSafe ? C.teal : C.red }} />
        </div>
        <span style={{ fontFamily: FONT.data, fontSize: 12.5, color: (t.sec ?? 0) >= minSafe ? C.teal : C.red, width: 24, textAlign: "right" }}>{t.sec == null ? "—" : t.sec.toFixed(0)}</span>
        <span style={{ fontSize: 10.5, color: C.muted, width: 26 }}>熱度</span>
        <div className="flex-1 h-1.5 rounded-sm" style={{ background: C.line }}>
          <div className="h-1.5 rounded-sm" style={{ width: `${t.heat}%`, background: C.violet }} />
        </div>
        <span style={{ fontFamily: FONT.data, fontSize: 12.5, color: C.violet, width: 24, textAlign: "right" }}>{t.heat.toFixed(0)}</span>
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1 mt-2" style={{ fontFamily: FONT.data, fontSize: 11.5, color: C.muted }}>
        <span>池齡 <span style={{ color: t.ageH != null && t.ageH < 24 ? C.gold : C.bone }}>{t.ageH == null ? "—" : t.ageH < 24 ? `${t.ageH.toFixed(0)}h` : `${(t.ageH / 24).toFixed(0)}d`}</span></span>
        <span>流動性 <span style={{ color: t.liq < 30000 ? C.red : C.bone }}>{fmtUsd(t.liq)}</span></span>
        <span>量 <span style={{ color: C.bone }}>{fmtUsd(t.vol24)}</span></span>
        <span>買/賣 <span style={{ color: UP }}>{t.buys}</span>/<span style={{ color: DOWN }}>{t.sells}</span></span>
        <span>持幣 <span style={{ color: C.bone }}>{t.holders ?? "—"}</span></span>
        <span>前十 <span style={{ color: (t.top10 ?? 0) >= 50 ? C.red : C.bone }}>{t.top10 == null ? "—" : t.top10.toFixed(0) + "%"}</span></span>
        {tax != null && <span>稅 <span style={{ color: tax >= 10 ? C.red : C.bone }}>{tax.toFixed(0)}%</span></span>}
        {t.deep && <span>大額買 <span style={{ color: C.gold }}>{t.bigBuys}</span></span>}
      </div>
      <div style={{ fontSize: 11.5, marginTop: 4 }}>
        {blocks ? <span style={{ color: C.red }}>致命 {blocks}　</span> : null}
        {highs ? <span style={{ color: C.gold }}>高風險 {highs}　</span> : null}
        {!blocks && !highs ? <span style={{ color: C.teal }}>無重大標記</span> : null}
        {(blocks || highs) ? <span style={{ color: C.muted }}>{t.flags.filter((f) => f.level === "block" || f.level === "high").map((f) => f.k).slice(0, 2).join("、")}</span> : null}
      </div>
    </div>
  );
}

function useNarrow() {
  const [n, setN] = useState(() => { try { return window.innerWidth < 760; } catch (e) { return false; } });
  useEffect(() => {
    const h = () => setN(window.innerWidth < 760);
    window.addEventListener("resize", h);
    return () => window.removeEventListener("resize", h);
  }, []);
  return n;
}

/* 手機版結果卡片：表格在小螢幕上得橫向捲動才看得完，改成一張卡一檔幣 */
function CoinCard({ r, side, onPick, starred, onStar, UP, DOWN }) {
  const tone = (v) => (v == null ? C.muted : v > 0 ? UP : v < 0 ? DOWN : C.bone);
  const score = side === "bull" ? r.radar : r.bear;
  const col = side === "bull" ? C.gold : C.red;
  return (
    <div onClick={() => onPick(r)} className="px-3 py-2.5"
      style={{ borderBottom: `1px solid ${C.line}`, opacity: r.scanned ? 1 : 0.65 }}>
      <div className="flex items-center gap-2">
        <button onClick={(e) => { e.stopPropagation(); onStar(r.id); }}
          style={{ color: starred ? C.gold : C.line, fontSize: 17, lineHeight: 1 }}>★</button>
        {r.img && <img src={r.img} alt="" width={18} height={18} style={{ borderRadius: 4 }} />}
        <span style={{ fontSize: 14 }}>{r.sym}</span>
        <span style={{ color: C.muted, fontSize: 11 }}>{r.name.slice(0, 10)}</span>
        <span className="ml-auto" style={{ fontFamily: FONT.data, fontSize: 14 }}>{fmtPrice(r.price)}</span>
        <span style={{ fontFamily: FONT.data, fontSize: 13, color: tone(r.m24), minWidth: 54, textAlign: "right" }}>{fmtPct(r.m24)}</span>
      </div>

      <div className="flex items-center gap-2 mt-2">
        {side === "bull" ? <StageTag stage={r.stage} /> : <BearTag stage={r.bearStage} />}
        <BladeTag state={r.bladeState || "none"} tangle={r.tangle} />
        <div className="flex-1 h-1.5 rounded-sm" style={{ background: C.line }}>
          <div className="h-1.5 rounded-sm" style={{ width: `${score || 0}%`, background: col }} />
        </div>
        <span style={{ fontFamily: FONT.data, fontSize: 15, color: col, minWidth: 24, textAlign: "right" }}>
          {score == null ? "—" : score.toFixed(0)}
        </span>
      </div>

      <div className="flex flex-wrap gap-x-3 gap-y-1 mt-2" style={{ fontFamily: FONT.data, fontSize: 11.5, color: C.muted }}>
        {side === "bull" ? (
          <>
            <span>量能 <span style={{ color: (r.rvol7 ?? 0) >= 2 ? C.gold : C.bone }}>{fmtX(r.rvol7)}</span></span>
            <span>換手 <span style={{ color: C.bone }}>{r.turn == null ? "—" : r.turn.toFixed(1) + "%"}</span></span>
            <span>距高 <span style={{ color: (r.d90 ?? -99) > -8 ? C.gold : C.bone }}>{r.d90 == null ? "—" : r.d90.toFixed(1) + "%"}</span></span>
            <span>流動性 <span style={{ color: C.bone }}>{r.liq == null ? "—" : r.liq.toFixed(0)}</span></span>
            <span>距小綠 <span style={{ color: r.dGreen == null ? C.muted : r.dGreen > 0 ? UP : DOWN }}>{r.dGreen == null ? "—" : fmtPct(r.dGreen)}</span></span>
          </>
        ) : (
          <>
            <span>距 EMA20 <span style={{ color: tone(r.distEma) }}>{r.distEma == null ? "—" : fmtPct(r.distEma)}</span></span>
            <span>跌幅 <span style={{ color: DOWN }}>{r.drop == null ? "—" : fmtPct(r.drop)}</span></span>
            <span>反彈 <span style={{ color: C.bone }}>{r.bounce == null ? "—" : fmtPct(r.bounce)}</span></span>
            <span>彈/跌量 <span style={{ color: (r.volRatio ?? 1) < 0.7 ? C.gold : C.bone }}>{r.volRatio == null ? "—" : r.volRatio.toFixed(2) + "x"}</span></span>
          </>
        )}
      </div>

      {side === "bear" && r.entryLo && (
        <div className="mt-1.5" style={{ fontFamily: FONT.data, fontSize: 11.5 }}>
          <span style={{ color: C.teal }}>入場 {fmtPrice(r.entryLo)}–{fmtPrice(r.entryHi)}</span>
          <span style={{ color: C.red }}>　止損 {fmtPrice(r.stop)}</span>
          {r.rr && <span style={{ color: C.muted }}>　RR {r.rr.toFixed(2)}</span>}
        </div>
      )}

      <div style={{ fontSize: 11.5, color: r.scanned ? C.bone : C.muted, marginTop: 4 }}>
        {side === "bull" ? r.why : r.bearWhy}
      </div>
    </div>
  );
}

export { ChainCard, CoinCard, WatchCard, useNarrow };
