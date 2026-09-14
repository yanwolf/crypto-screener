import React from "react";
import { EVENT } from "../alerts.js";
import { Stat } from "../components/Small.jsx";
import { C, FONT } from "../theme.js";
import { fmtPct, fmtPrice } from "../util.js";

/* 由 App.jsx 抽出。共用狀態透過 s 傳入，避免逐一列 props。 */
export default function SignalsPage({ s }) {
  const { DOWN, UP, allHistory, history, narrow, setHistory, setSigStates, srvHistory, stats, tone } = s;
  return (
<>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              <Stat label="累計提醒" value={`${stats.n} 則`} sub="最多保留 400 則" />
              <Stat label="追蹤中" value={`${stats.open} 則`} sub="未滿 72 小時" accent={C.teal} />
              <Stat label="達標" value={`${stats.win} 則`} sub="順方向先走到 5%" accent={C.green} />
              <Stat label="反向" value={`${stats.lose} 則`} sub="逆方向先走到 5%" accent={C.red} />
              <Stat label="達標率" value={stats.rate == null ? "—" : stats.rate.toFixed(0) + "%"} sub="已結案的樣本" accent={C.gold} />
            </div>
            <section className="rounded" style={{ background: C.panel, border: `1px solid ${C.line}` }}>
              <div className="px-3.5 py-2.5 flex flex-wrap items-center gap-2" style={{ borderBottom: `1px solid ${C.line}` }}>
                <h2 style={{ fontFamily: FONT.display, fontSize: 16 }}>訊號歷史</h2>
                <span style={{ fontSize: 11, color: C.muted }}>
                  網頁與伺服器的紀錄已合併{srvHistory.length ? `（伺服器 ${srvHistory.length} 則）` : ""}。
                  結果以提醒當下的價格為基準，用每次資料更新的快照追蹤 72 小時，順方向或逆方向先走到 5% 就結案
                </span>
                {history.length > 0 && (
                  <button onClick={() => { if (confirm("確定清空所有訊號紀錄？")) { setHistory([]); setSigStates({}); } }}
                    className="ml-auto px-2.5 py-1 rounded" style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.muted, fontSize: 11.5 }}>清空紀錄</button>
                )}
              </div>
              {!allHistory.length ? (
                <div className="px-4 py-10 text-center" style={{ color: C.muted, fontSize: 12.5, lineHeight: 1.8 }}>
                  還沒有任何訊號。到提醒設定把監控打開，並確認觀察清單裡有幣。
                </div>
              ) : (
                <div className="grid" style={{ maxHeight: 620, overflowY: "auto" }}>
                  {allHistory.map((e, i) => {
                    const v = e.verdict === "win" ? ["達標", C.green] : e.verdict === "lose" ? ["反向", C.red]
                      : e.verdict === "expired" ? ["逾時", C.muted] : ["追蹤中", C.teal];
                    return (
                      <div key={i} className="px-3.5 py-2.5" style={{ borderBottom: `1px solid ${C.line}` }}>
                        <div className="flex flex-wrap items-center gap-2" style={{ fontSize: 12.5 }}>
                          <span className="px-1.5 py-0.5 rounded" style={{ fontSize: 11, color: C.ink,
                            background: e.side === "bull" ? UP : DOWN }}>{e.side === "bull" ? "▲ 多" : "▼ 空"}</span>
                          <span style={{ fontFamily: FONT.data }}>{e.sym}</span>
                          <span className="px-1.5 py-0.5 rounded" style={{ fontSize: 11, color: EVENT[e.type].color, border: `1px solid ${EVENT[e.type].color}` }}>{EVENT[e.type].label}</span>
                          <span style={{ color: C.muted, fontSize: 11.5 }}>{e.stage}</span>
                          {e.source === "server" && (
                            <span className="px-1.5 py-0.5 rounded" style={{ fontSize: 10, color: C.teal, border: `1px solid ${C.teal}` }}>伺服器</span>
                          )}
                          <span style={{ fontFamily: FONT.data, color: C.muted, fontSize: 11.5 }}>
                            {narrow ? new Date(e.ts).toLocaleString("zh-TW", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })
                              : new Date(e.ts).toLocaleString("zh-TW")}　{fmtPrice(e.price)}　{e.score.toFixed(0)} 分
                          </span>
                          <span className={narrow ? "px-2 py-0.5 rounded" : "ml-auto px-2 py-0.5 rounded"}
                            style={{ fontSize: 11, color: v[1], border: `1px solid ${v[1]}` }}>{v[0]}</span>
                          {e.last != null && (
                            <span style={{ fontFamily: FONT.data, fontSize: 11.5, color: tone(e.side === "bull" ? e.last : -e.last) }}>
                              目前 {fmtPct(e.last)}
                            </span>
                          )}
                        </div>
                        <div style={{ fontSize: 11.5, color: C.muted, marginTop: 3, lineHeight: 1.7 }}>
                          {e.checks.join("　·　")}
                          {e.plan ? `　·　進場 ${e.plan.entry}　停損 ${e.plan.stop}`
                            : e.entryLo ? `　·　進場區 ${fmtPrice(e.entryLo)}–${fmtPrice(e.entryHi)}　止損 ${fmtPrice(e.stop)}` : ""}
                          {e.verdict !== "open" ? `　·　最大順向 ${e.maxUp.toFixed(1)}%　最大逆向 ${e.maxDown.toFixed(1)}%` : ""}
                        </div>
                      </div>);
                  })}
                </div>
              )}
            </section>
          </>
  );
}
