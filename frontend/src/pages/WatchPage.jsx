import React from "react";
import { WatchCard } from "../components/Cards.jsx";
import { BearTag, RvolBar, ScoreCell, StageTag } from "../components/Small.jsx";
import { C, FONT } from "../theme.js";
import { fmtPct, fmtPrice, store } from "../util.js";

/* 由 App.jsx 抽出。共用狀態透過 s 傳入，避免逐一列 props。 */
export default function WatchPage({ s }) {
  const { DOWN, UP, batch, narrow, rows, runEngine, scan, setDetail, sigStates, source, toggleWatch, tone, watch } = s;
  return (
<section className="rounded" style={{ background: C.panel, border: `1px solid ${C.line}` }}>
            <div className="px-3.5 py-2.5 flex flex-wrap items-center gap-2" style={{ borderBottom: `1px solid ${C.line}` }}>
              <h2 style={{ fontFamily: FONT.display, fontSize: 16 }}>觀察清單</h2>
              <span style={{ fontSize: 11, color: C.muted }}>
                {watch.length} 檔　提醒只會針對這份清單（可在提醒設定改成全部已掃描）
                {!store.ok && "　· 這個環境無法保存，重整後會清空"}
              </span>
              <div className="ml-auto flex gap-2">
                <button onClick={() => scan(rows.filter((r) => watch.includes(r.id)))}
                  disabled={batch.running || !watch.length || source === "demo"} className="px-3 py-1.5 rounded"
                  style={{ background: C.teal, color: C.ink, fontSize: 12 }}>
                  {batch.running ? `掃描中 ${batch.done}/${batch.total}` : "掃描觀察清單"}
                </button>
                <button onClick={() => runEngine(true)} className="px-3 py-1.5 rounded"
                  style={{ background: C.panel2, border: `1px solid ${C.gold}`, color: C.gold, fontSize: 12 }}>立即檢查訊號</button>
              </div>
            </div>
            {!watch.length ? (
              <div className="px-4 py-10 text-center" style={{ color: C.muted, fontSize: 12.5, lineHeight: 1.8 }}>
                清單是空的。回到雷達頁，點任一列最左邊的 ★ 就會加進來。<br />
                觀察清單的幣可以自動定期重掃，額度不會浪費在不關心的標的上。
              </div>
            ) : (
              narrow ? (
                <div>
                  {rows.filter((r) => watch.includes(r.id)).map((r) => (
                    <WatchCard key={r.id} r={r} sigB={sigStates[r.id + ":bull"]} sigR={sigStates[r.id + ":bear"]}
                      onPick={setDetail} onStar={toggleWatch} UP={UP} DOWN={DOWN} />
                  ))}
                </div>
              ) : (
              <div className="overflow-x-auto">
                <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 12.5 }}>
                  <thead style={{ background: C.panel2 }}>
                    <tr>{["", "幣種", "價格", "24h", "量能", "多頭階段", "雷達", "空頭階段", "空頭", "訊號狀態"].map((t, i) => (
                      <th key={i} className="px-2.5 py-2 text-left" style={{ color: C.muted, fontWeight: 400, fontSize: 11, borderBottom: `1px solid ${C.line}` }}>{t}</th>
                    ))}</tr>
                  </thead>
                  <tbody>
                    {rows.filter((r) => watch.includes(r.id)).map((r) => {
                      const sb = sigStates[r.id + ":bull"], sr = sigStates[r.id + ":bear"];
                      return (
                        <tr key={r.id} onClick={() => setDetail(r)} className="cursor-pointer" style={{ borderBottom: `1px solid ${C.line}` }}>
                          <td className="px-2.5 py-1.5">
                            <button onClick={(e) => { e.stopPropagation(); toggleWatch(r.id); }} style={{ color: C.gold, fontSize: 15 }}>★</button>
                          </td>
                          <td className="px-2.5 py-1.5 whitespace-nowrap">{r.sym} <span style={{ color: C.muted, fontSize: 11 }}>{r.name.slice(0, 12)}</span></td>
                          <td className="px-2.5 py-1.5" style={{ fontFamily: FONT.data }}>{fmtPrice(r.price)}</td>
                          <td className="px-2.5 py-1.5" style={{ fontFamily: FONT.data, color: tone(r.m24) }}>{fmtPct(r.m24)}</td>
                          <td className="px-2.5 py-1.5"><RvolBar v={r.rvol7} /></td>
                          <td className="px-2.5 py-1.5"><StageTag stage={r.stage} /></td>
                          <td className="px-2.5 py-1.5"><ScoreCell score={r.radar} color={C.gold} /></td>
                          <td className="px-2.5 py-1.5"><BearTag stage={r.bearStage} /></td>
                          <td className="px-2.5 py-1.5"><ScoreCell score={r.bear} color={C.red} /></td>
                          <td className="px-2.5 py-1.5" style={{ fontSize: 11.5, color: C.muted, whiteSpace: "nowrap" }}>
                            {sb ? `多頭已觸發 ${new Date(sb.lastTs).toLocaleDateString("zh-TW")}` : ""}
                            {sb && sr ? " · " : ""}
                            {sr ? `空頭已觸發 ${new Date(sr.lastTs).toLocaleDateString("zh-TW")}` : ""}
                            {!sb && !sr ? "尚未觸發" : ""}
                          </td>
                        </tr>);
                    })}
                  </tbody>
                </table>
              </div>
              )
            )}
          </section>
  );
}
