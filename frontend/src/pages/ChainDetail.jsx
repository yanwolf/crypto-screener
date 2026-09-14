import React from "react";
import { BUCKETS, NETWORKS, bucketOf } from "../chain.js";
import { C, FONT } from "../theme.js";
import { fmtPct, fmtPrice, fmtUsd } from "../util.js";

/* 由 App.jsx 抽出。共用狀態透過 s 傳入，避免逐一列 props。 */
export default function ChainDetail({ s }) {
  const { chainDetail, minSafe, net, setChainDetail, target, tone } = s;
  return (
<div className="fixed inset-0 flex items-end md:items-center justify-center p-0 md:p-6"
          style={{ background: "rgba(6,10,12,0.75)", zIndex: 50 }} onClick={() => setChainDetail(null)}>
          <div className="w-full md:max-w-xl rounded-t-lg md:rounded-lg p-4"
            style={{ background: C.panel, border: `1px solid ${C.violet}`, maxHeight: "88vh", overflowY: "auto" }}
            onClick={(e) => e.stopPropagation()}>
            <div className="flex items-start gap-3">
              <div>
                <div style={{ fontFamily: FONT.display, fontSize: 20 }}>{chainDetail.sym}</div>
                <div style={{ fontSize: 11.5, color: C.muted }}>{chainDetail.tokenName} · {chainDetail.name}</div>
              </div>
              <button onClick={() => setChainDetail(null)} className="ml-auto px-2 py-1 rounded"
                style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.muted, fontSize: 12 }}>關閉</button>
            </div>
            <div style={{ fontFamily: FONT.data, fontSize: 10.5, color: C.muted, marginTop: 6, wordBreak: "break-all" }}>{chainDetail.addr}</div>

            <div className="mt-3 px-3 py-2.5 rounded" style={{ background: BUCKETS[bucketOf(chainDetail, minSafe)].color === C.red ? "#241A1A" : C.panel2, fontSize: 12.5, lineHeight: 1.75 }}>
              <span style={{ color: BUCKETS[bucketOf(chainDetail, minSafe)].color }}>{BUCKETS[bucketOf(chainDetail, minSafe)].label}　</span>
              安全 {chainDetail.sec == null ? "—" : chainDetail.sec.toFixed(0)} · 熱度 {chainDetail.heat.toFixed(0)}
              <div style={{ color: C.muted, marginTop: 3 }}>{BUCKETS[bucketOf(chainDetail, minSafe)].say}</div>
            </div>

            <div className="mt-3 grid grid-cols-2 gap-2" style={{ fontFamily: FONT.data, fontSize: 12 }}>
              <div><span style={{ color: C.muted }}>價格 </span>{fmtPrice(chainDetail.price)}</div>
              <div><span style={{ color: C.muted }}>24h </span><span style={{ color: tone(chainDetail.chg24) }}>{fmtPct(chainDetail.chg24)}</span></div>
              <div><span style={{ color: C.muted }}>流動性 </span>{fmtUsd(chainDetail.liq)}</div>
              <div><span style={{ color: C.muted }}>24h 量 </span>{fmtUsd(chainDetail.vol24)}</div>
              <div><span style={{ color: C.muted }}>買/賣筆數 </span>{chainDetail.buys}/{chainDetail.sells}</div>
              <div><span style={{ color: C.muted }}>買/賣人數 </span>{chainDetail.buyers}/{chainDetail.sellers}</div>
              <div><span style={{ color: C.muted }}>持幣地址 </span>{chainDetail.holders ?? "—"}</div>
              <div><span style={{ color: C.muted }}>前十集中 </span>{chainDetail.top10 == null ? "—" : chainDetail.top10.toFixed(1) + "%"}</div>
              {chainDetail.lockedPct != null && <div><span style={{ color: C.muted }}>LP 鎖倉 </span>{chainDetail.lockedPct.toFixed(0)}%</div>}
              {chainDetail.deep && <div><span style={{ color: C.muted }}>大額買入 </span>{chainDetail.bigBuys} 筆 {fmtUsd(chainDetail.bigBuyUsd)}</div>}
              {chainDetail.deep && <div><span style={{ color: C.muted }}>淨流入 </span><span style={{ color: tone(chainDetail.netUsd) }}>{fmtUsd(Math.abs(chainDetail.netUsd))}{chainDetail.netUsd < 0 ? " 淨流出" : ""}</span></div>}
            </div>

            <div className="mt-3 px-3 py-2.5 rounded" style={{ background: C.panel2, fontSize: 12, lineHeight: 1.8 }}>
              <div style={{ color: C.muted, marginBottom: 4 }}>持幣地址增長</div>
              {chainDetail.holderGrowth == null
                ? `目前只有 ${chainDetail.histLen || 0} 筆快照。這個數字要靠多次掃描累積，掃過兩次以上才算得出成長率。`
                : `從 ${new Date(chainDetail.histFrom).toLocaleString("zh-TW")} 的快照到現在，持幣地址${chainDetail.holderGrowth >= 0 ? "增加" : "減少"} ${Math.abs(chainDetail.holderGrowth).toFixed(1)}%，共 ${chainDetail.histLen} 筆紀錄。`}
            </div>

            <div className="mt-3">
              <div style={{ fontSize: 12, color: C.muted, marginBottom: 5 }}>合約風險檢查</div>
              {!chainDetail.flags.length && <div style={{ fontSize: 12, color: C.teal }}>沒有查到已知風險項目。</div>}
              <div className="grid gap-1.5">
                {chainDetail.flags.map((f, i) => {
                  const col = f.level === "block" ? C.red : f.level === "high" ? C.gold : f.level === "ok" ? C.teal : C.muted;
                  const lab = f.level === "block" ? "致命" : f.level === "high" ? "高" : f.level === "ok" ? "良好" : f.level === "mid" ? "中" : "低";
                  return (
                    <div key={i} className="px-2.5 py-2 rounded" style={{ background: C.panel2 }}>
                      <div className="flex items-center gap-2">
                        <span className="px-1.5 py-0.5 rounded" style={{ fontSize: 10.5, color: col, border: `1px solid ${col}` }}>{lab}</span>
                        <span style={{ fontSize: 12.5 }}>{f.k}</span>
                      </div>
                      <div style={{ fontSize: 12, marginTop: 4, lineHeight: 1.7 }}>{f.t}</div>
                    </div>);
                })}
              </div>
            </div>

            <div className="mt-3 flex gap-3" style={{ fontSize: 12 }}>
              <a href={`https://www.geckoterminal.com/${net}/pools/${chainDetail.pool}`} target="_blank" rel="noreferrer" style={{ color: C.violet }}>GeckoTerminal 池子頁 →</a>
              <a href={`https://gopluslabs.io/token-security/${NETWORKS.find((n) => n.id === net).gp}/${chainDetail.addr}`} target="_blank" rel="noreferrer" style={{ color: C.violet }}>GoPlus 完整報告 →</a>
            </div>
          </div>
        </div>
  );
}
