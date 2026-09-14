import React from "react";
import { BUCKETS, NETWORKS, makeChainDemo, pctNum } from "../chain.js";
import { ChainCard } from "../components/Cards.jsx";
import { ScoreCell, Stat } from "../components/Small.jsx";
import { C, FONT } from "../theme.js";
import { fmtPct, fmtUsd } from "../util.js";

/* 由 App.jsx 抽出。共用狀態透過 s 傳入，避免逐一列 props。 */
export default function ChainPage({ s }) {
  const { DOWN, UP, chainAt, chainBuckets, chainBusy, chainErr, chainTokens, deepN, minSafe, narrow, net, poolMode, rows, scanChain, setChainAt, setChainDetail, setChainErr, setChainTokens, setDeepN, setMinSafe, setNet, setPoolMode, setSmartRaw, smartRaw } = s;
  return (
<>
            <section className="rounded p-3.5" style={{ background: C.panel, border: `1px solid ${C.violet}` }}>
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 mb-2">
                <h2 style={{ fontFamily: FONT.display, fontSize: 18 }}>鏈上異動雷達</h2>
                <span style={{ fontSize: 11.5, color: C.muted, lineHeight: 1.6 }}>
                  熱度來自 GeckoTerminal 的池子資料，安全來自 GoPlus 合約檢查。兩份資料合起來才決定一個代幣放在哪一區。
                </span>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <select value={net} onChange={(e) => setNet(e.target.value)} className={narrow ? "px-2 py-1.5 rounded flex-1" : "px-2 py-1.5 rounded"}
                  style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.bone, fontSize: 12 }}>
                  {NETWORKS.map((n) => <option key={n.id} value={n.id}>{n.name}</option>)}
                </select>
                <select value={poolMode} onChange={(e) => setPoolMode(e.target.value)} className={narrow ? "px-2 py-1.5 rounded flex-1" : "px-2 py-1.5 rounded"}
                  style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.bone, fontSize: 12 }}>
                  <option value="new">新上線的池子</option><option value="trending">熱門池子</option>
                </select>
                <select value={deepN} onChange={(e) => setDeepN(+e.target.value)} className="px-2 py-1.5 rounded"
                  style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.bone, fontSize: 12 }}>
                  <option value={0}>不做大額交易分析</option><option value={5}>深度分析前 5 檔</option>
                  <option value={8}>前 8 檔</option><option value={15}>前 15 檔</option>
                </select>
                <button onClick={scanChain} disabled={!!chainBusy} className="px-3.5 py-1.5 rounded"
                  style={{ background: chainBusy ? C.panel2 : C.violet, color: chainBusy ? C.muted : C.ink, fontSize: 12.5 }}>
                  {chainBusy || "開始鏈上掃描"}
                </button>
                <button onClick={() => { setChainTokens(makeChainDemo(net)); setChainAt(new Date()); setChainErr(null); }}
                  className="px-2.5 py-1.5 rounded" style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.muted, fontSize: 12 }}>
                  載入示範資料
                </button>
                <label className="flex items-center gap-1.5" style={{ fontSize: 12 }}>
                  安全分門檻
                  <input type="range" min={30} max={90} step={5} value={minSafe} onChange={(e) => setMinSafe(+e.target.value)}
                    style={{ accentColor: C.violet, width: 90 }} />
                  <span style={{ fontFamily: FONT.data, color: C.violet }}>{minSafe}</span>
                </label>
                {chainAt && <span style={{ fontSize: 11, color: C.muted, fontFamily: FONT.data }}>更新 {chainAt.toLocaleTimeString("zh-TW")}</span>}
              </div>
              {net === "solana" && (
                <div style={{ fontSize: 11.5, color: C.gold, marginTop: 8, lineHeight: 1.7 }}>
                  Solana 的合約檢查項目比 EVM 少：可查增發、凍結、關閉帳戶、轉帳稅與持幣集中度，但查不到流動性池的鎖倉狀態，
                  因此撤池風險無法判斷，安全分只能當作下限參考。
                </div>
              )}
              <div style={{ fontSize: 11.5, color: C.muted, marginTop: 8, lineHeight: 1.8 }}>
                持幣地址增長需要時間累積：每次掃描會記下當下的持幣地址數，掃過兩次以上才算得出成長率。
                「聰明錢」沒有免費的公開標記資料，這裡改成比對你自己的地址名單——貼上你追蹤的錢包，掃描時會標出它們的進出。
              </div>
              <textarea value={smartRaw} onChange={(e) => setSmartRaw(e.target.value)} rows={2}
                placeholder="貼上你要追蹤的錢包地址，一行一個或用逗號分隔（選填）"
                className="w-full mt-2 px-2 py-1.5 rounded"
                style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.bone, fontSize: 11.5, fontFamily: FONT.data }} />
            </section>

            {chainErr && (
              <div className="px-3 py-2.5 rounded" style={{ background: "#2A1618", border: `1px solid ${C.red}`, fontSize: 12.5, lineHeight: 1.7 }}>{chainErr}</div>
            )}

            {chainTokens.length > 0 && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {["opportunity", "watchlist", "risk", "unverified"].map((b) => (
                  <Stat key={b} label={BUCKETS[b].label} value={`${chainBuckets[b].length} 檔`}
                    sub={BUCKETS[b].say.slice(0, 18)} accent={BUCKETS[b].color} />
                ))}
              </div>
            )}

            {["opportunity", "risk", "watchlist", "unverified"].map((b) => (
              chainBuckets[b].length > 0 && (
                <section key={b} className="rounded" style={{ background: C.panel, border: `1px solid ${b === "risk" ? C.red : C.line}` }}>
                  <div className="px-3.5 py-2.5" style={{ borderBottom: `1px solid ${C.line}`, background: b === "risk" ? "#241A1A" : undefined }}>
                    <div className="flex flex-wrap items-baseline gap-2">
                      <h3 style={{ fontFamily: FONT.display, fontSize: 16, color: BUCKETS[b].color }}>{BUCKETS[b].label}</h3>
                      <span style={{ fontSize: 11.5, color: C.muted }}>{chainBuckets[b].length} 檔</span>
                    </div>
                    <div style={{ fontSize: 11.5, color: b === "risk" ? C.red : C.muted, marginTop: 3, lineHeight: 1.7 }}>
                      {BUCKETS[b].say}
                      {b === "risk" && "　這一區的代幣不會出現在機會榜，無論鏈上多熱鬧。"}
                    </div>
                  </div>
                  {narrow ? (
                    <div>
                      {chainBuckets[b].map((t) => (
                        <ChainCard key={t.pool} t={t} minSafe={minSafe} onPick={setChainDetail} UP={UP} DOWN={DOWN} />
                      ))}
                    </div>
                  ) : (
                  <div className="overflow-x-auto">
                    <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 12.5 }}>
                      <thead style={{ background: C.panel2 }}>
                        <tr>{["代幣", "池齡", "流動性", "24h 量", "量/流動性", "買/賣", "買方人數", "大額買入", "持幣數", "增長", "前十集中", "稅", "安全", "熱度", "風險標記"].map((t, i) => (
                          <th key={i} className="px-2.5 py-2 text-left whitespace-nowrap"
                            style={{ color: C.muted, fontWeight: 400, fontSize: 11, borderBottom: `1px solid ${C.line}` }}>{t}</th>))}
                        </tr>
                      </thead>
                      <tbody>
                        {chainBuckets[b].map((t) => {
                          const tax = t.raw ? Math.max(pctNum(t.raw.buy_tax) ?? 0, pctNum(t.raw.sell_tax) ?? 0) : null;
                          const blocks = t.flags.filter((f) => f.level === "block").length;
                          const highs = t.flags.filter((f) => f.level === "high").length;
                          return (
                            <tr key={t.pool} onClick={() => setChainDetail(t)} className="cursor-pointer" style={{ borderBottom: `1px solid ${C.line}` }}>
                              <td className="px-2.5 py-1.5 whitespace-nowrap">
                                {t.sym}<span style={{ color: C.muted, fontSize: 11 }}> {t.tokenName.slice(0, 12)}</span>
                              </td>
                              <td className="px-2.5 py-1.5" style={{ fontFamily: FONT.data, color: t.ageH != null && t.ageH < 24 ? C.gold : C.muted }}>
                                {t.ageH == null ? "—" : t.ageH < 24 ? `${t.ageH.toFixed(0)} 小時` : `${(t.ageH / 24).toFixed(0)} 天`}
                              </td>
                              <td className="px-2.5 py-1.5" style={{ fontFamily: FONT.data, color: t.liq < 30000 ? C.red : C.bone }}>{fmtUsd(t.liq)}</td>
                              <td className="px-2.5 py-1.5" style={{ fontFamily: FONT.data }}>{fmtUsd(t.vol24)}</td>
                              <td className="px-2.5 py-1.5" style={{ fontFamily: FONT.data, color: t.turn > 3 ? C.gold : C.bone }}>{t.turn.toFixed(1)}x</td>
                              <td className="px-2.5 py-1.5" style={{ fontFamily: FONT.data }}>
                                <span style={{ color: UP }}>{t.buys}</span>/<span style={{ color: DOWN }}>{t.sells}</span>
                              </td>
                              <td className="px-2.5 py-1.5" style={{ fontFamily: FONT.data }}>{t.buyers}</td>
                              <td className="px-2.5 py-1.5" style={{ fontFamily: FONT.data, color: t.bigBuys ? C.gold : C.muted }}>
                                {t.deep ? `${t.bigBuys} 筆 ${fmtUsd(t.bigBuyUsd)}` : "未分析"}
                                {t.smartBuys ? <span style={{ color: C.teal }}> ·名單 {t.smartBuys}買{t.smartSells}賣</span> : null}
                              </td>
                              <td className="px-2.5 py-1.5" style={{ fontFamily: FONT.data }}>{t.holders ?? "—"}</td>
                              <td className="px-2.5 py-1.5" style={{ fontFamily: FONT.data, color: t.holderGrowth == null ? C.muted : t.holderGrowth > 0 ? UP : DOWN }}>
                                {t.holderGrowth == null ? "待累積" : fmtPct(t.holderGrowth)}
                              </td>
                              <td className="px-2.5 py-1.5" style={{ fontFamily: FONT.data, color: (t.top10 ?? 0) >= 50 ? C.red : C.bone }}>
                                {t.top10 == null ? "—" : t.top10.toFixed(0) + "%"}
                              </td>
                              <td className="px-2.5 py-1.5" style={{ fontFamily: FONT.data, color: (tax ?? 0) >= 10 ? C.red : C.bone }}>
                                {tax == null ? "—" : tax.toFixed(0) + "%"}
                              </td>
                              <td className="px-2.5 py-1.5"><ScoreCell score={t.sec} color={t.sec >= minSafe ? C.teal : C.red} /></td>
                              <td className="px-2.5 py-1.5"><ScoreCell score={t.heat} color={C.violet} /></td>
                              <td className="px-2.5 py-1.5" style={{ fontSize: 11.5, whiteSpace: "nowrap" }}>
                                {blocks ? <span style={{ color: C.red }}>致命 {blocks}　</span> : null}
                                {highs ? <span style={{ color: C.gold }}>高 {highs}　</span> : null}
                                {!blocks && !highs ? <span style={{ color: C.teal }}>無重大標記</span> : null}
                              </td>
                            </tr>);
                        })}
                      </tbody>
                    </table>
                  </div>
                  )}
                </section>
              )
            ))}

            {!chainTokens.length && !chainBusy && !chainErr && (
              <div className="rounded px-4 py-10 text-center" style={{ background: C.panel, border: `1px solid ${C.line}`, color: C.muted, fontSize: 12.5, lineHeight: 1.9 }}>
                選一條鏈，按「開始鏈上掃描」。<br />
                流程是先抓池子清單，再一次批次查合約安全，最後才對熱度最高的幾檔做大額交易分析，總共約 3 到 12 次請求。
              </div>
            )}

            <p style={{ fontSize: 11, color: C.muted, lineHeight: 1.8 }}>
              合約檢查來自 GoPlus 的自動偵測，能抓到大多數已知的詐騙結構，但無法保證沒有漏網之魚——
              可升級的合約可以在檢查通過之後才改成惡意版本，稅率可調的代幣也可能在你賣出時才把稅拉高。
              安全分高只代表「目前沒有查到已知風險」，不等於安全。新池代幣的風險本質上遠高於主流幣，
              本頁所有內容僅供鏈上活動觀察，不構成投資建議。
            </p>
          </>
  );
}
