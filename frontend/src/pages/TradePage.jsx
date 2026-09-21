import React from "react";
import { C, FONT } from "../theme.js";
import { fmtPrice, store } from "../util.js";

/* 由 App.jsx 抽出。共用狀態透過 s 傳入，避免逐一列 props。 */
export default function TradePage({ s }) {
  const { DOWN, UP, autoEdit, cfg, liveBusy, liveCheck, pf, pfBusy, refreshTrade, setAutoEdit, setLiveBusy, setLiveCheck, setPf, setPfBusy, setTargetEdit, setTgAdmin, setTrade, setTradeIdx, setTradeMsg, setTradeTargets, setTradesLimit, setTradesOpen, targetEdit, tgAdmin, trade, tradeBaseRef, tradeBusy, tradeCall, tradeIdx, tradeMsg, tradeTargets, tradesLimit, tradesOpen } = s;
  return (
<div className="mt-3">
            {/* 網路別橫幅：正式網必須一眼看出來 */}
            <div className="px-3 py-2 rounded flex items-center gap-2 flex-wrap"
              style={{ background: trade.net && trade.net.includes("LIVE") ? "#241F0F" : C.panel2,
                       border: `1px solid ${trade.net && trade.net.includes("LIVE") ? C.gold : C.line}` }}>
              <div className="flex rounded overflow-hidden" style={{ border: `1px solid ${C.line}` }}>
                {tradeTargets.map((t, i) => (
                  <button key={i} onClick={() => setTradeIdx(i)} className="px-2.5 py-1"
                    style={{ background: i === tradeIdx ? C.teal : "transparent",
                             color: i === tradeIdx ? C.ink : C.muted, fontSize: 11.5 }}>
                    {t.label}
                  </button>
                ))}
                <button onClick={() => setTargetEdit((v) => !v)} className="px-2 py-1"
                  style={{ background: "transparent", color: C.muted, fontSize: 11.5, borderLeft: `1px solid ${C.line}` }}>
                  {targetEdit ? "完成" : "＋"}
                </button>
              </div>
              <span style={{ fontFamily: FONT.display, fontSize: 13,
                             color: trade.net && trade.net.includes("LIVE") ? C.gold : C.teal }}>
                {trade.net || "讀取中…"}
              </span>
              {trade.hasCreds === false && (
                <span style={{ fontSize: 11.5, color: C.gold }}>
                  未設定 BN_KEY／BN_SECRET，只能檢查合約、無法下單
                </span>
              )}
              <button onClick={refreshTrade} disabled={tradeBusy} className="ml-auto px-2.5 py-1 rounded"
                style={{ background: C.panel, border: `1px solid ${C.line}`, color: C.muted, fontSize: 11.5 }}>
                {tradeBusy ? "…" : "重新整理"}
              </button>
              <button onClick={async () => { const j = await tradeCall("sync", {}); if (j) { setTradeMsg(["ok", `對帳完成${j.closed?.length ? "，平倉 " + j.closed.join("、") : "，無異動"}`]); refreshTrade(); } }}
                disabled={tradeBusy} className="px-2.5 py-1 rounded"
                style={{ background: C.panel, border: `1px solid ${C.line}`, color: C.muted, fontSize: 11.5 }}>
                與交易所對帳
              </button>
            </div>

            {/* 管理金鑰：與提醒設定共用同一組，填一次兩邊都生效 */}
            <div className="mt-2 rounded p-3"
              style={{ background: C.panel2, border: `1px solid ${tgAdmin || trade.adminRequired === false ? C.line : C.gold}` }}>
              <div className="flex items-center gap-2 flex-wrap">
                <span style={{ fontSize: 12, color: C.bone }}>管理金鑰</span>
                <span style={{ fontSize: 11, color: tgAdmin ? C.teal : trade.adminRequired === false ? C.muted : C.gold }}>
                  {tgAdmin ? "已填寫"
                    : trade.adminRequired === false ? "伺服器未設 ADMIN_KEY，可留白"
                    : "未填寫，下單與平倉會被拒絕"}
                </span>
              </div>
              <input type="password" value={tgAdmin} onChange={(e) => setTgAdmin(e.target.value)}
                placeholder="伺服器 ADMIN_KEY 環境變數的值"
                className="mt-2 w-full px-2.5 py-1.5 rounded"
                style={{ background: C.panel, border: `1px solid ${C.line}`, color: C.bone, fontSize: 12, fontFamily: FONT.data }} />
              <div className="mt-1.5" style={{ fontSize: 10.5, color: C.muted, lineHeight: 1.6 }}>
                這是你在 Zeabur 設定的 ADMIN_KEY，用來防止別人透過你的網址下單。
                只存在這台裝置的瀏覽器，不會送到別的地方。與「提醒設定」頁共用同一組。
                若伺服器沒有設 ADMIN_KEY，留白即可。
              </div>
            </div>

            {trade.balance && (
              <div className="mt-2 px-3 py-2 rounded flex items-center gap-x-4 gap-y-1 flex-wrap"
                style={{ background: C.panel2, border: `1px solid ${C.line}`, fontFamily: FONT.data, fontSize: 12 }}>
                <span><span style={{ color: C.muted }}>權益 </span>{trade.balance.equity.toFixed(2)} U</span>
                <span><span style={{ color: C.muted }}>可用 </span>{trade.balance.avail.toFixed(2)} U</span>
                <span><span style={{ color: C.muted }}>保證金 </span>{trade.balance.used.toFixed(2)} U</span>
                {Math.abs(trade.balance.upnl) > 0.01 && (
                  <span style={{ color: trade.balance.upnl > 0 ? UP : DOWN }}>
                    未實現 {trade.balance.upnl > 0 ? "+" : ""}{trade.balance.upnl.toFixed(2)} U
                  </span>
                )}
                {trade.capital && (
                  <span style={{ color: C.muted, fontSize: 10.5, marginLeft: "auto" }}>
                    階梯 {trade.capital.tier.toFixed(0)} U × {trade.capital.usablePct}% ＝ 可動用 {trade.capital.usable.toFixed(0)} U，
                    剩 <span style={{ color: trade.capital.free < trade.capital.perPosCap ? C.gold : C.muted }}>{trade.capital.free.toFixed(0)} U</span>
                    　每筆上限 {trade.capital.perPosCap.toFixed(0)} U
                  </span>
                )}
              </div>
            )}

            {trade.autoOn && trade.signalSource && !trade.signalSource.monitorOn && (
              <div className="mt-2 px-3 py-2 rounded" style={{ background: "#241A1A", border: `1px solid ${C.red}`, fontSize: 12, color: C.red, lineHeight: 1.8 }}>
                自動下單已啟用，但這個服務的<span style={{ fontWeight: 600 }}>伺服器背景監控</span>未啟動，不會收到任何訊號，因此永遠不會下單。
                <div style={{ color: C.muted, fontSize: 11 }}>
                  注意是「伺服器背景監控」，不是提醒設定頁上方那個「訊號監控」——後者只在網頁開著時運作。
                  到該服務的網址 → 提醒設定 → 捲到「伺服器背景監控」→ 按「同步並啟動背景監控」。
                  在交易頁切換服務只改變你在看誰，不會把訊號送過去。
                </div>
              </div>
            )}
            {trade.liveBlocked && trade.liveBlocked.length > 0 && (
              <div className="mt-2 px-3 py-2 rounded" style={{ background: "#241A1A", border: `1px solid ${C.red}`, fontSize: 12, color: C.red, lineHeight: 1.8 }}>
                設定了正式網（--live / ALLOW_LIVE）但條件不足，已降級為模擬網並停用下單：
                {trade.liveBlocked.map((x, i) => <div key={i}>· {x}</div>)}
                <div style={{ color: C.muted, fontSize: 11 }}>補齊後重新部署即可。雷達與資料代理不受影響。</div>
              </div>
            )}
            {targetEdit && (
              <div className="mt-2 rounded p-3" style={{ background: C.panel2, border: `1px solid ${C.line}` }}>
                <div style={{ fontSize: 12, color: C.bone, marginBottom: 6 }}>交易服務</div>
                {tradeTargets.map((t, i) => (
                  <div key={i} className="flex gap-1.5 mb-1.5 flex-wrap">
                    <input value={t.label} placeholder="名稱" onChange={(e) => setTradeTargets((a) => a.map((x, j) => j === i ? { ...x, label: e.target.value } : x))}
                      className="px-2 py-1 rounded" style={{ width: 72, background: C.panel, border: `1px solid ${C.line}`, color: C.bone, fontSize: 11.5 }} />
                    <input value={t.url} placeholder={i === 0 ? "（本站，留空）" : "https://xxx.zeabur.app"} disabled={i === 0}
                      onChange={(e) => setTradeTargets((a) => a.map((x, j) => j === i ? { ...x, url: e.target.value.trim() } : x))}
                      className="px-2 py-1 rounded flex-1" style={{ minWidth: 140, background: C.panel, border: `1px solid ${C.line}`, color: C.bone, fontSize: 11.5, opacity: i === 0 ? 0.5 : 1 }} />
                    <input type="password" value={t.key} placeholder={i === 0 ? "沿用提醒設定" : "該服務的 ADMIN_KEY"} disabled={i === 0}
                      onChange={(e) => setTradeTargets((a) => a.map((x, j) => j === i ? { ...x, key: e.target.value } : x))}
                      className="px-2 py-1 rounded" style={{ width: 130, background: C.panel, border: `1px solid ${C.line}`, color: C.bone, fontSize: 11.5, opacity: i === 0 ? 0.5 : 1 }} />
                    {i > 0 && (
                      <button onClick={() => { setTradeTargets((a) => a.filter((_, j) => j !== i)); if (tradeIdx >= i) setTradeIdx(0); }}
                        className="px-2 py-1 rounded" style={{ background: "transparent", border: `1px solid ${C.line}`, color: C.muted, fontSize: 11.5 }}>移除</button>
                    )}
                  </div>
                ))}
                <button onClick={() => setTradeTargets((a) => [...a, { label: "正式網", url: "", key: "" }])}
                  className="px-2.5 py-1 rounded" style={{ background: C.panel, border: `1px solid ${C.line}`, color: C.muted, fontSize: 11.5 }}>
                  新增服務
                </button>
                <div className="mt-1.5" style={{ fontSize: 10.5, color: C.muted, lineHeight: 1.6 }}>
                  正式網與模擬網是兩個獨立部署，這裡切換的只是交易頁看哪一個；雷達與訊號仍來自本站。
                  網址與金鑰只存在這台裝置的瀏覽器。
                </div>
              </div>
            )}

            {tradeMsg && (
              <div className="mt-2 px-3 py-2 rounded" style={{ fontSize: 12,
                background: tradeMsg[0] === "err" ? "#241A1A" : C.panel2,
                border: `1px solid ${tradeMsg[0] === "err" ? C.red : C.line}`,
                color: tradeMsg[0] === "err" ? C.red : C.bone }}>
                {tradeMsg[1]}
                <button onClick={() => setTradeMsg(null)} className="ml-2" style={{ color: C.muted }}>關閉</button>
              </div>
            )}

            {/* 上線就緒：放在交易頁，不必去連線設定找 */}
            <div className="mt-3 rounded p-3" style={{ background: C.panel2, border: `1px solid ${trade.net && trade.net.includes("LIVE") ? C.gold : C.line}` }}>
              <div className="flex items-center gap-2 flex-wrap">
                <span style={{ fontFamily: FONT.display, fontSize: 13 }}>接入正式網的準備</span>
                {liveCheck && (
                  <span style={{ fontSize: 11.5, color: C.muted }}>
                    {liveCheck.list.filter((x) => x.ok).length}/{liveCheck.list.length} 就緒
                  </span>
                )}
                <button
                  onClick={async () => {
                    setLiveBusy(true);
                    try {
                      const r = await fetch(tradeBaseRef.current + "/api/health?probe=1", { cache: "no-store" });
                      const j = await r.json();
                      setLiveCheck({ list: j.liveChecklist || [], ip: j.egressIp || null, live: !!j.live });
                    } catch (e) { setTradeMsg(["err", "連不到伺服器：" + e.message]); }
                    finally { setLiveBusy(false); }
                  }}
                  disabled={liveBusy} className="ml-auto px-2.5 py-1 rounded"
                  style={{ background: C.panel, border: `1px solid ${C.line}`, color: C.muted, fontSize: 11.5 }}>
                  {liveBusy ? "檢查中…" : liveCheck ? "重新檢查" : "檢查"}
                </button>
              </div>

              {!liveCheck && (
                <div className="mt-1.5" style={{ fontSize: 11, color: C.muted, lineHeight: 1.6 }}>
                  按「檢查」會列出還缺哪幾項，並顯示伺服器出口 IP 供幣安 API 白名單使用。
                  目前是模擬網，這裡只是預先看準備進度，不會切換任何東西。
                </div>
              )}

              {liveCheck && (
                <div className="mt-2">
                  {liveCheck.ip && (
                    <div className="mb-2 px-2.5 py-1.5 rounded flex items-center gap-2 flex-wrap"
                      style={{ background: C.panel, border: `1px solid ${C.line}`, fontSize: 11.5 }}>
                      <span style={{ color: C.muted }}>伺服器出口 IP</span>
                      <span style={{ fontFamily: FONT.data, color: C.teal, userSelect: "all" }}>{liveCheck.ip}</span>
                      <span style={{ color: C.muted, fontSize: 10.5 }}>← 填入幣安 API 金鑰的 IP 白名單</span>
                    </div>
                  )}
                  {liveCheck.list.map((x, i) => (
                    <div key={i} className="flex items-center gap-2" style={{ fontSize: 11.5, lineHeight: 1.9 }}>
                      <span style={{ color: x.ok ? C.teal : C.gold }}>{x.ok ? "✓" : "○"}</span>
                      <span style={{ color: x.ok ? C.bone : C.muted }}>{x.item}</span>
                    </div>
                  ))}
                  <div className="mt-1.5" style={{ fontSize: 10.5, color: C.muted, lineHeight: 1.6 }}>
                    完整步驟在專案的 GO_LIVE.md。條件不齊時正式網會拒絕啟動，不會誤上線。
                  </div>
                </div>
              )}
            </div>

            {/* 影子追蹤：被持倉上限擋掉的訊號後來怎麼了 */}
            {trade.missed && (trade.missed.evaluated > 0 || trade.missed.pending > 0) && (
              <div className="mt-3 rounded p-3" style={{ background: C.panel2, border: `1px solid ${C.line}` }}>
                <div className="flex items-center gap-2 flex-wrap">
                  <span style={{ fontFamily: FONT.display, fontSize: 13 }}>被持倉上限擋掉的訊號</span>
                  <span style={{ fontSize: 11, color: C.muted }}>
                    已評估 {trade.missed.evaluated}　待評估 {trade.missed.pending}
                  </span>
                </div>
                {trade.missed.evaluated > 0 ? (
                  <>
                    <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1" style={{ fontFamily: FONT.data, fontSize: 12 }}>
                      <div className="flex justify-between"><span style={{ color: C.muted }}>72h 內到 2R</span><span style={{ color: UP }}>{trade.missed.tp2}</span></div>
                      <div className="flex justify-between"><span style={{ color: C.muted }}>先碰停損</span><span style={{ color: DOWN }}>{trade.missed.stop}</span></div>
                      <div className="flex justify-between"><span style={{ color: C.muted }}>都沒碰到</span><span>{trade.missed.none}</span></div>
                      <div className="flex justify-between"><span style={{ color: C.muted }}>平均</span>
                        <span style={{ color: trade.missed.avgR > 0 ? UP : trade.missed.avgR < 0 ? DOWN : C.bone }}>
                          {trade.missed.avgR > 0 ? "+" : ""}{trade.missed.avgR}R
                        </span></div>
                    </div>
                    <div className="mt-2" style={{ fontSize: 10.5, color: C.muted, lineHeight: 1.7 }}>
                      這是「如果沒有上限會怎樣」的近似：用逐時收盤價回算，先碰停損記 -1R、先到 2R 記 +2R，都沒碰到以 72 小時收盤算。
                      沒有模擬移損與移動停利，實際會略好於此。累積 20 筆以上再比：若平均 R 明顯高於已成交的期望值，才值得調高上限。
                    </div>
                  </>
                ) : (
                  <div className="mt-1.5" style={{ fontSize: 11, color: C.muted }}>
                    被擋掉的訊號會在 72 小時後用歷史價格回算結果，累積後顯示在這裡。
                  </div>
                )}
              </div>
            )}

            {/* 反向訊號追蹤：持有中的部位收到反方向訊號後，後來怎麼了 */}
            {trade.conflicts && (trade.conflicts.evaluated > 0 || trade.conflicts.pending > 0) && (
              <div className="mt-3 rounded p-3" style={{ background: C.panel2, border: `1px solid ${C.line}` }}>
                <div className="flex items-center gap-2 flex-wrap">
                  <span style={{ fontFamily: FONT.display, fontSize: 13 }}>持有中收到反向訊號</span>
                  <span style={{ fontSize: 11, color: C.muted }}>
                    已平倉 {trade.conflicts.evaluated}　持有中 {trade.conflicts.pending}
                  </span>
                  <label className="ml-auto flex items-center gap-1.5 cursor-pointer" style={{ fontSize: 11.5, color: trade.cfg?.conflictTighten ? C.teal : C.muted }}>
                    <input type="checkbox" checked={!!trade.cfg?.conflictTighten} style={{ accentColor: C.teal }}
                      onChange={async (e) => {
                        const j = await tradeCall("config", { conflictTighten: e.target.checked ? 1 : 0 });
                        if (j && j.cfg) setTrade((x) => ({ ...x, cfg: j.cfg }));
                      }} />
                    反向訊號過閘門時移損到成本
                  </label>
                </div>
                {trade.conflicts.evaluated > 0 ? (
                  <>
                    <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1" style={{ fontFamily: FONT.data, fontSize: 12 }}>
                      <div className="flex justify-between"><span style={{ color: C.muted }}>訊號當下平均</span><span>{trade.conflicts.avgRAtSignal > 0 ? "+" : ""}{trade.conflicts.avgRAtSignal}R</span></div>
                      <div className="flex justify-between"><span style={{ color: C.muted }}>最終平均</span><span>{trade.conflicts.avgFinalR > 0 ? "+" : ""}{trade.conflicts.avgFinalR}R</span></div>
                      <div className="flex justify-between"><span style={{ color: C.muted }}>之後平均再走</span>
                        <span style={{ color: trade.conflicts.avgDrift > 0 ? UP : trade.conflicts.avgDrift < 0 ? DOWN : C.bone }}>
                          {trade.conflicts.avgDrift > 0 ? "+" : ""}{trade.conflicts.avgDrift}R
                        </span></div>
                      <div className="flex justify-between"><span style={{ color: C.muted }}>變差／變好</span><span>{trade.conflicts.worseAfter} / {trade.conflicts.betterAfter}</span></div>
                    </div>
                    <div className="mt-2" style={{ fontSize: 10.5, color: C.muted, lineHeight: 1.7 }}>
                      「之後平均再走」明顯為負，代表反向訊號有預警價值，該開啟上面的收緊；接近零或為正，代表該無視。
                      不同時間框架的訊號本來就常打架，累積 20 筆以上再判斷。
                    </div>
                  </>
                ) : (
                  <div className="mt-1.5" style={{ fontSize: 11, color: C.muted }}>
                    持有中的部位收到反向訊號時會記下當時的 R，平倉後回填最終 R，累積後顯示在這裡。反向訊號不會反手。
                  </div>
                )}
              </div>
            )}

            {/* 交易所自檢：每一項對應 BINANCE_LESSONS.md 的一條 */}
            <div className="mt-3 rounded p-3" style={{ background: C.panel2, border: `1px solid ${C.line}` }}>
              <div className="flex items-center gap-2 flex-wrap">
                <span style={{ fontFamily: FONT.display, fontSize: 13 }}>交易所自檢</span>
                {pf && (
                  <span style={{ fontSize: 11, color: C.muted }}>
                    {pf.results.filter((x) => x.status === "ok").length}/{pf.results.length} 通過　{pf.version}
                    {pf.cached ? "（60 秒內的結果）" : ""}
                  </span>
                )}
                <button
                  onClick={async () => {
                    setPfBusy(true);
                    try {
                      const r = await fetch(tradeBaseRef.current + "/api/trade/preflight", { cache: "no-store" });
                      setPf(await r.json());
                    } catch (e) { setTradeMsg(["err", "自檢失敗：" + e.message]); }
                    finally { setPfBusy(false); }
                  }}
                  disabled={pfBusy} className="ml-auto px-2.5 py-1 rounded"
                  style={{ background: C.panel, border: `1px solid ${C.line}`, color: C.muted, fontSize: 11.5 }}>
                  {pfBusy ? "檢查中…" : pf ? "重新檢查" : "執行"}
                </button>
              </div>
              {!pf && (
                <div className="mt-1.5" style={{ fontSize: 11, color: C.muted, lineHeight: 1.6 }}>
                  確認幣安 API 沒有改動：精度、條件單端點、持倉模式、槓桿上限、限流、未記帳的送單、孤兒條件單。
                  全部唯讀，不會下單。開機時也會自動跑一次，有異常會推 Telegram。
                </div>
              )}
              {pf && pf.results.map((x, i) => (
                <div key={i} className="flex gap-2" style={{ fontSize: 11.5, lineHeight: 1.8 }}>
                  <span style={{ color: x.status === "ok" ? C.teal : x.status === "warn" ? C.gold : C.red, minWidth: 12 }}>
                    {x.status === "ok" ? "✓" : x.status === "warn" ? "⚠" : "✕"}
                  </span>
                  <span style={{ color: C.bone, minWidth: 96 }}>{x.item}</span>
                  <span style={{ color: C.muted, wordBreak: "break-all" }}>
                    {x.msg}{x.lesson ? `　〔第 ${x.lesson} 條〕` : ""}
                  </span>
                </div>
              ))}
              {pf && pf.results.flatMap((x) => x.orphans || []).map((o) => (
                <div key={o.algoId} className="mt-1.5 px-2.5 py-1.5 rounded flex items-center gap-2 flex-wrap"
                  style={{ background: C.panel, border: `1px solid ${C.gold}`, fontFamily: FONT.data, fontSize: 11.5 }}>
                  <span>{o.symbol}</span>
                  <span style={{ color: C.muted }}>{o.type}</span>
                  {o.price && <span style={{ color: C.muted }}>@ {o.price}</span>}
                  <span style={{ color: o.ours ? C.teal : C.muted, fontSize: 10.5 }}>{o.ours ? "本專案" : "來源不明"}</span>
                  <button
                    onClick={async () => {
                      const j = await tradeCall("cancel_orphan", { symbol: o.symbol, algoId: o.algoId });
                      if (j && j.ok) {
                        setTradeMsg(["ok", `已撤掉 ${o.symbol} #${o.algoId}`]);
                        const r = await fetch(tradeBaseRef.current + "/api/trade/preflight", { cache: "no-store" });
                        setPf(await r.json());
                      }
                    }}
                    className="ml-auto px-2 py-0.5 rounded"
                    style={{ background: "transparent", border: `1px solid ${C.gold}`, color: C.gold, fontSize: 11 }}>
                    撤掉
                  </button>
                </div>
              ))}
              {pf && pf.results.some((x) => (x.orphans || []).some((o) => !o.ours)) && (
                <div className="mt-1" style={{ fontSize: 10.5, color: C.muted, lineHeight: 1.6 }}>
                  「來源不明」是帳上沒有紀錄的單：正式網子帳戶只有本專案在用，可以放心撤；
                  模擬網共用帳號時可能是其他專案的，撤之前先確認。有部位的幣一律拒撤。
                </div>
              )}
            </div>

            {/* 自動下單 */}
            {trade.auto && (
              <div className="mt-3 rounded p-3"
                style={{ background: C.panel2, border: `1px solid ${trade.auto.on ? C.teal : C.line}` }}>
                <div className="flex items-center gap-2 flex-wrap">
                  <span style={{ fontFamily: FONT.display, fontSize: 13 }}>自動下單</span>
                  <span style={{ fontSize: 11.5, color: trade.auto.on ? C.teal : C.muted }}>
                    {trade.auto.on ? "運作中" : "已關閉"}
                  </span>
                  <button
                    onClick={async () => {
                      const j = await tradeCall("auto", { on: !trade.auto.on });
                      if (j && !j.error) { setTradeMsg(["ok", j.auto.on ? "自動下單已啟用" : "自動下單已關閉"]); refreshTrade(); }
                    }}
                    disabled={tradeBusy}
                    className="ml-auto px-3 py-1 rounded"
                    style={{ background: trade.auto.on ? C.panel : C.teal,
                             color: trade.auto.on ? C.red : C.ink,
                             border: trade.auto.on ? `1px solid ${C.red}` : "none", fontSize: 12 }}>
                    {trade.auto.on ? "停止" : "啟用"}
                  </button>
                </div>

                <div className="mt-2.5 grid grid-cols-2 gap-x-4 gap-y-1.5" style={{ fontFamily: FONT.data, fontSize: 11.5 }}>
                  <div className="flex justify-between">
                    <span style={{ color: C.muted }}>今日開倉</span>
                    <span>{trade.auto.opened} / {trade.auto.maxPerDay}</span>
                  </div>
                  <div className="flex justify-between">
                    <span style={{ color: C.muted }}>今日已實現</span>
                    <span style={{ color: (trade.auto.closedR ?? 0) > 0 ? UP : (trade.auto.closedR ?? 0) < 0 ? DOWN : C.bone }}>
                      {(trade.auto.closedR ?? 0).toFixed(2)}R
                      {trade.auto.closedUsd != null && <span style={{ fontSize: 11 }}>（{trade.auto.closedUsd > 0 ? "+" : ""}{trade.auto.closedUsd.toFixed(0)} U）</span>}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span style={{ color: C.muted }}>同時持倉</span>
                    <span>{trade.positions?.length || 0} / {trade.cfg?.maxPositions ?? "—"}</span>
                  </div>
                  <div className="flex justify-between">
                    <span style={{ color: C.muted }}>單筆風險</span>
                    <span>{trade.cfg?.riskPct ?? "—"}%{trade.auto.oneR != null && <span style={{ fontSize: 11, color: C.muted }}>　1R ≈ {trade.auto.oneR.toFixed(0)} U</span>}</span>
                  </div>
                </div>

                {/* 可調參數 */}
                <div className="mt-3 pt-3" style={{ borderTop: `1px solid ${C.line}` }}>
                  <div className="flex items-center justify-between">
                    <span style={{ fontSize: 12, color: C.bone }}>參數設定</span>
                    <button onClick={() => setAutoEdit((v) => !v)} className="px-2 py-0.5 rounded"
                      style={{ background: C.panel, border: `1px solid ${C.line}`, color: C.muted, fontSize: 11 }}>
                      {autoEdit ? "收起" : "調整"}
                    </button>
                  </div>

                  {autoEdit && (
                    <div className="mt-2">
                      {[
                        ["maxPerDay", "每日最多開倉", [1, 2, 3, 4, 6, 10], "筆"],
                        ["minScore", "分數門檻", [58, 62, 65, 70, 75, 80], "分"],
                        ["dailyLossR", "當日停損上限", [1, 2, 3, 4, 5], "R"],
                        ["cooldownMin", "虧損平倉後冷卻", [30, 60, 120, 240, 480], "分鐘"],
                        ["cooldownWinMin", "獲利平倉後冷卻", [0, 15, 30, 60], "分鐘"],
                      ].map(([key, label, opts, unit]) => {
                        const cur = key === "dailyLossR"
                          ? Math.abs(trade.auto.dailyLossR ?? 3) : trade.auto[key];
                        return (
                          <div key={key} className="mt-2">
                            <div style={{ fontSize: 11.5, color: C.muted, marginBottom: 4 }}>
                              {label}　<span style={{ color: C.bone }}>{cur} {unit}</span>
                            </div>
                            <div className="flex flex-wrap gap-1.5">
                              {opts.map((o) => (
                                <button key={o}
                                  onClick={async () => {
                                    const j = await tradeCall("auto", { [key]: o });
                                    if (j && !j.error) { setTrade((t) => ({ ...t, auto: j.auto })); }
                                  }}
                                  disabled={tradeBusy}
                                  className="px-2.5 py-1 rounded"
                                  style={{ background: o === cur ? C.teal : C.panel,
                                           color: o === cur ? C.ink : C.muted,
                                           border: `1px solid ${o === cur ? C.teal : C.line}`, fontSize: 11.5 }}>
                                  {key === "dailyLossR" ? `-${o}` : o}
                                </button>
                              ))}
                            </div>
                          </div>
                        );
                      })}
                      {/* 這兩項直接決定每筆虧多少錢，改動前多想一下 */}
                      <div className="mt-3 pt-2.5" style={{ borderTop: `1px solid ${C.line}` }}>
                        <div style={{ fontSize: 11, color: C.gold, marginBottom: 6 }}>
                          以下直接決定每筆的虧損金額與停損位置
                        </div>
                        <div className="mt-2">
                          <div style={{ fontSize: 11.5, color: C.muted, marginBottom: 4 }}>
                            停損來源　<span style={{ color: C.bone }}>
                              {{ ma: "均線", atr: "ATR", tighter: "取近的" }[trade.cfg?.stopMode] || "取近的"}
                            </span>
                          </div>
                          <div className="flex flex-wrap gap-1.5">
                            {[["tighter", "均線／ATR 取近"], ["atr", "只用 ATR"], ["ma", "只用均線"]].map(([v, t]) => (
                              <button key={v}
                                onClick={async () => {
                                  const j = await tradeCall("config", { stopMode: v });
                                  if (j && j.cfg) setTrade((x) => ({ ...x, cfg: j.cfg }));
                                }}
                                disabled={tradeBusy} className="px-2.5 py-1 rounded"
                                style={{ background: (trade.cfg?.stopMode || "tighter") === v ? C.gold : C.panel,
                                         color: (trade.cfg?.stopMode || "tighter") === v ? C.ink : C.muted,
                                         border: `1px solid ${(trade.cfg?.stopMode || "tighter") === v ? C.gold : C.line}`, fontSize: 11.5 }}>
                                {t}
                              </button>
                            ))}
                          </div>
                          <div className="mt-1" style={{ fontSize: 10.5, color: C.muted, lineHeight: 1.6 }}>
                            風險基準採本金階梯（500／1000／1500／2000／3000／5000…）而非實際餘額：
                            同一級距內每筆的 1R 金額固定，統計才可比，獲利也不會立刻放大部位。
                            可動用比例是保證金總額上限，留緩衝給浮虧與手續費。
                          </div>
                          <div className="mt-1" style={{ fontSize: 10.5, color: C.muted, lineHeight: 1.6 }}>
                            現價離小綠 15% 以上時，用均線定停損會寬到讓部位失去意義、2R 遠到等不到；
                            取近的能把距離壓回該幣的實際波動範圍。
                          </div>
                        </div>
                        {[
                          ["maxPositions", "同時持倉上限", [1, 2, 3, 5, 8], "筆", trade.cfg?.maxPositions],
                          ["riskPct", "單筆風險", [0.25, 0.5, 1, 1.5, 2], "%", trade.cfg?.riskPct],
                          ["usablePct", "可動用保證金比例", [50, 60, 75, 90, 100], "%", trade.cfg?.usablePct],
                          ["stopAtrMult", "ATR 停損倍數", [1, 1.5, 2, 2.5, 3], "×ATR", trade.cfg?.stopAtrMult],
                          ["maxStopPct", "停損距離上限", [6, 8, 10, 12, 15], "%", trade.cfg?.maxStopPct],
                          ["breakevenR", "移損到成本（0 = 關閉）", [0, 0.8, 1, 1.5, 2], "R", trade.cfg?.breakevenR],
                          ["trailR", "移動停利回撤", [0.3, 0.5, 0.8, 1], "R", trade.cfg?.trailR],
                        ].map(([key, label, opts, unit, cur]) => (
                          <div key={key} className="mt-2">
                            <div style={{ fontSize: 11.5, color: C.muted, marginBottom: 4 }}>
                              {label}　<span style={{ color: C.bone }}>{cur ?? "—"} {unit}</span>
                            </div>
                            <div className="flex flex-wrap gap-1.5">
                              {opts.map((o) => (
                                <button key={o}
                                  onClick={async () => {
                                    const j = await tradeCall("config", { [key]: o });
                                    if (j && j.cfg) setTrade((t) => ({ ...t, cfg: j.cfg }));
                                  }}
                                  disabled={tradeBusy}
                                  className="px-2.5 py-1 rounded"
                                  style={{ background: o === cur ? C.gold : C.panel,
                                           color: o === cur ? C.ink : C.muted,
                                           border: `1px solid ${o === cur ? C.gold : C.line}`, fontSize: 11.5 }}>
                                  {o}
                                </button>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>

                      <div className="mt-2.5" style={{ fontSize: 10.5, color: C.muted, lineHeight: 1.7 }}>
                        第一天建議：每日最多 2 筆、分數門檻 70、同時持倉 2 筆，
                        確認通知與成交都正常後再放寬。
                        移損到成本會讓「衝到 1.5R 又跌回去」的單從虧 1R 變成打平，
                        代價是被小回檔掃出場的次數會變多；移動停利的回撤以 R 計，不同波動的幣才會一致。
                        設定存在伺服器的 trader.json，優先於環境變數。
                        環境變數（MAX_POSITIONS、RISK_PCT、LEVERAGE）只在第一次啟動、
                        還沒有存檔時當預設值。
                        存檔位於 CACHE_DIR，沒掛 Volume 的話重新部署會清空並退回環境變數。
                      </div>
                    </div>
                  )}
                </div>

                {trade.auto.blocked && (
                  <div className="mt-2 px-2.5 py-1.5 rounded"
                    style={{ background: "#241A1A", border: `1px solid ${C.red}`, fontSize: 11.5, color: C.red, lineHeight: 1.7 }}>
                    今日停止新開倉。
                    {trade.auto.blockedAtR != null && <> 於 {trade.auto.blockedAtR.toFixed(2)}R（{(trade.auto.blockedAtUsd ?? 0).toFixed(0)} U）觸發，</>}
                    目前累計 {(trade.auto.closedR ?? 0).toFixed(2)}R（{(trade.auto.closedUsd ?? 0).toFixed(0)} U）
                    {trade.positions?.length > 0 && <>；已有 {trade.positions.length} 筆部位仍會依停損或停利出場，數字可能繼續變動</>}。
                    停損上限 {trade.auto.dailyLossR}R ≈ {trade.auto.oneR != null ? (trade.auto.dailyLossR * trade.auto.oneR).toFixed(0) : "—"} U。
                  </div>
                )}

                <div className="mt-2.5 pt-2.5 flex items-center gap-2 flex-wrap"
                  style={{ borderTop: `1px solid ${C.line}`, fontSize: 11.5 }}>
                  <span style={{ color: C.muted }}>快取用量</span>
                  <span style={{ fontFamily: FONT.data }}>{trade.diskMB ?? "—"} MB</span>
                  <button
                    onClick={async () => {
                      const j = await tradeCall("cleanup", { dry: true });
                      if (j && !j.error) {
                        setTradeMsg(["ok", j.removed
                          ? `可清除 ${j.removed} 個過期檔（${j.freedMB} MB），清完剩 ${(j.diskMB - j.freedMB).toFixed(1)} MB。`
                          : `沒有可清的檔案。目前 ${j.diskMB} MB、${j.scanned} 個快取檔，`
                            + `最舊的才 ${j.oldestH ?? "—"} 小時（保留期 ${j.maxAgeH} 小時）`
                            + (j.lastAuto != null ? `。上次${j.lastBy === "auto" ? "自動" : "手動"}清理在 ${j.lastAuto} 分鐘前` : "")
                            + "。"]);
                      }
                    }}
                    disabled={tradeBusy} className="px-2.5 py-1 rounded"
                    style={{ background: C.panel, border: `1px solid ${C.line}`, color: C.muted, fontSize: 11 }}>
                    試算
                  </button>
                  <button
                    onClick={async () => {
                      const j = await tradeCall("cleanup", {});
                      if (j && !j.error) {
                        setTradeMsg(["ok", j.removed
                          ? `已清除 ${j.removed} 個過期檔，釋出 ${j.freedMB} MB，剩餘 ${j.diskMB} MB。`
                          : `沒有東西需要清。${j.scanned} 個快取檔都在保留期內`
                            + `（最舊 ${j.oldestH ?? "—"} 小時，保留期 ${j.maxAgeH} 小時），共 ${j.diskMB} MB。`
                            + (j.lastAuto != null && j.lastAuto < 720
                               ? `上次${j.lastBy === "auto" ? "自動" : "手動"}清理在 ${j.lastAuto} 分鐘前，可能已經清過了。` : "")]);
                        refreshTrade();
                      }
                    }}
                    disabled={tradeBusy} className="px-2.5 py-1 rounded"
                    style={{ background: C.panel, border: `1px solid ${C.line}`, color: C.muted, fontSize: 11 }}>
                    立即清理
                  </button>
                  <span style={{ color: C.muted, fontSize: 10.5 }}>每 6 小時自動清一次</span>
                </div>

                <div className="mt-2" style={{ fontSize: 10.5, color: C.muted, lineHeight: 1.7 }}>
                  由背景監控觸發，套用與畫面相同的證據檢查，但分數門檻更嚴（{trade.auto.minScore} 分）。
                  開倉與平倉都會推播 Telegram。同一檔虧損平倉後 {trade.auto.cooldownMin} 分鐘、獲利平倉後 {trade.auto.cooldownWinMin ?? 15} 分鐘內不再進場。
                  當日虧損達 {trade.auto.dailyLossR}R 會自動停止，隔日（UTC）重置。
                </div>
              </div>
            )}

            {/* 績效 */}
            {trade.perf && trade.perf.count > 0 ? (
              <div className="mt-3 rounded p-3" style={{ background: C.panel2, border: `1px solid ${C.line}` }}>
                <div style={{ fontFamily: FONT.display, fontSize: 13, marginBottom: 8 }}>
                  績效　{trade.perf.count} 筆計入
                  {trade.perf.excluded > 0 && <span style={{ fontSize: 11, color: C.muted }}>　（另 {trade.perf.excluded} 筆已排除）</span>}
                </div>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-2" style={{ fontFamily: FONT.data, fontSize: 12 }}>
                  {[["勝率", `${trade.perf.winRate}%`, C.bone],
                    ["總損益", `${trade.perf.pnl} U`, trade.perf.pnl > 0 ? UP : DOWN],
                    ["平均獲利", `${trade.perf.avgWinR}R`, UP],
                    ["平均虧損", `${trade.perf.avgLossR}R`, DOWN],
                    ["賺賠比", trade.perf.payoff ?? "—", (trade.perf.payoff ?? 0) >= 2 ? UP : C.gold],
                    ["期望值", `${trade.perf.expectancyR}R`, (trade.perf.expectancyR ?? 0) > 0 ? UP : DOWN],
                    ["最佳／最差", `${trade.perf.best ?? "—"} / ${trade.perf.worst ?? "—"}R`, C.bone],
                    ["最大回落", `${trade.perf.maxDrawdown} U`, DOWN]].map(([k, v, c]) => (
                    <div key={k} className="flex justify-between">
                      <span style={{ color: C.muted }}>{k}</span><span style={{ color: c }}>{v}</span>
                    </div>
                  ))}
                </div>
                {/* 分策略版本：改版前後直接比，不必靠記憶 */}
                {trade.perfByVersion && Object.keys(trade.perfByVersion).length > 1 && (
                  <div className="mt-3 pt-2.5" style={{ borderTop: `1px solid ${C.line}` }}>
                    <div style={{ fontSize: 12, color: C.bone, marginBottom: 4 }}>
                      分版本　<span style={{ fontSize: 10.5, color: C.muted }}>目前 {trade.strategyVersion}</span>
                    </div>
                    {Object.entries(trade.perfByVersion).map(([v, p]) => (
                      <div key={v} className="flex items-center gap-3 flex-wrap" style={{ fontFamily: FONT.data, fontSize: 11.5, lineHeight: 1.9 }}>
                        <span style={{ color: v === trade.strategyVersion ? C.teal : C.muted, fontSize: 10.5, wordBreak: "break-all" }}>
                          {v}{v === trade.strategyVersion ? "（目前）" : ""}
                        </span>
                        <span style={{ color: C.muted }}>{p.count} 筆</span>
                        <span>勝率 {p.winRate}%</span>
                        <span>賺賠 {p.payoff ?? "—"}</span>
                        <span style={{ color: (p.expectancyR ?? 0) > 0 ? UP : DOWN }}>期望 {p.expectancyR > 0 ? "+" : ""}{p.expectancyR}R</span>
                      </div>
                    ))}
                    <div className="mt-1" style={{ fontSize: 10.5, color: C.muted, lineHeight: 1.6 }}>
                      標籤由參數自動組成：停損模式／ATR 倍數／距離上下限、移損、回撤、停利、分數門檻。
                      在介面改任何一項，之後的交易就自動歸入新桶，不必手動記版本。
                      樣本少時差異可能是行情而非策略，各累積 40 筆以上再下結論。
                    </div>
                  </div>
                )}
                <div className="mt-2.5" style={{ fontSize: 11, color: C.muted, lineHeight: 1.7 }}>
                  大賺小賠看的是賺賠比與期望值，不是勝率。賺賠比 2 以上、期望值為正，
                  即使勝率只有三成也是可行的系統；反過來勝率八成但賺賠比 0.2 會慢慢虧光。
                </div>
              </div>
            ) : (
              <div className="mt-3 px-3 py-2 rounded" style={{ background: C.panel2, border: `1px solid ${C.line}`, fontSize: 12, color: C.muted }}>
                還沒有已平倉的交易。從雷達點進個股詳情，用「送模擬單」建立第一筆。
              </div>
            )}

            {/* 交易明細：每一筆的來龍去脈，並可排除非策略出場 */}
            {trade.trades?.length > 0 && (
              <div className="mt-3">
                <div className="flex items-center gap-2 flex-wrap">
                  <button onClick={() => setTradesOpen((v) => !v)} className="flex items-center gap-1.5"
                    style={{ background: "transparent", color: C.bone }}>
                    <span style={{ fontFamily: FONT.display, fontSize: 13 }}>交易明細</span>
                    <span style={{ fontSize: 11, color: C.muted }}>
                      {trade.trades.length} 筆{trade.perf?.excluded ? `，已排除 ${trade.perf.excluded} 筆` : ""}
                    </span>
                    <span style={{ color: C.muted, fontSize: 11 }}>{tradesOpen ? "▲ 收起" : "▼ 展開"}</span>
                  </button>
                  {trade.trades.some((t) => !t.excluded && /強制平倉|遺失/.test(t.reason || "")) && (
                    <button
                      onClick={async () => {
                        for (const t of trade.trades.filter((x) => !x.excluded && /強制平倉|遺失/.test(x.reason || ""))) {
                          await tradeCall("exclude", { id: t.id, excluded: true });
                        }
                        refreshTrade();
                      }}
                      disabled={tradeBusy} className="ml-auto px-2.5 py-1 rounded"
                      style={{ background: C.panel, border: `1px solid ${C.gold}`, color: C.gold, fontSize: 11 }}>
                      一鍵排除所有強制平倉
                    </button>
                  )}
                </div>

                {tradesOpen && (<>
                <div className="mt-2 flex flex-col gap-1.5">
                  {trade.trades.slice(0, tradesLimit).map((t) => {
                    const win = (t.pnl ?? 0) > 0;
                    const sys = /強制平倉|遺失/.test(t.reason || "");
                    const held = t.closed && t.opened ? (t.closed - t.opened) / 60000 : null;
                    return (
                      <div key={t.id} className="rounded px-2.5 py-2"
                        style={{ background: C.panel2, opacity: t.excluded ? 0.45 : 1,
                                 border: `1px solid ${t.excluded ? C.line : sys ? C.gold : C.line}` }}>
                        <div className="flex items-center gap-2 flex-wrap" style={{ fontFamily: FONT.data, fontSize: 12 }}>
                          <span>{t.symbol}</span>
                          <span style={{ fontSize: 10.5, color: t.side === "LONG" ? UP : DOWN }}>
                            {t.side === "LONG" ? "多" : "空"}
                          </span>
                          <span style={{ color: win ? UP : DOWN }}>
                            {t.pnl == null ? "損益未知" : `${win ? "+" : ""}${t.pnl.toFixed(2)} U`}
                          </span>
                          {t.rMultiple != null && (
                            <span style={{ color: win ? UP : DOWN }}>{t.rMultiple > 0 ? "+" : ""}{Number(t.rMultiple).toFixed(2)}R</span>
                          )}
                          {held != null && (
                            <span style={{ color: C.muted, fontSize: 10.5 }}>
                              {held >= 60 ? `${Math.floor(held / 60)}h${Math.round(held % 60)}m` : `${Math.round(held)}m`}
                            </span>
                          )}
                          <button
                            onClick={async () => {
                              const j = await tradeCall("exclude", { id: t.id, excluded: !t.excluded });
                              if (j?.ok) refreshTrade();
                            }}
                            disabled={tradeBusy} className="ml-auto px-2 py-0.5 rounded"
                            style={{ background: "transparent", border: `1px solid ${C.line}`,
                                     color: t.excluded ? C.teal : C.muted, fontSize: 10.5 }}>
                            {t.excluded ? "恢復計入" : "排除"}
                          </button>
                        </div>
                        <div className="mt-1 flex items-center gap-2 flex-wrap" style={{ fontSize: 10.5, color: C.muted }}>
                          <span>{fmtPrice(t.entry)} → {fmtPrice(t.exit)}</span>
                          <span style={{ color: sys ? C.gold : C.muted }}>{t.reason}</span>
                          {t.note && <span>· {t.note}</span>}
                        </div>
                      </div>
                    );
                  })}
                </div>
                {trade.trades.length > tradesLimit && (
                  <button onClick={() => setTradesLimit((n) => n + 20)} className="mt-2 px-3 py-1.5 rounded self-start"
                    style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.muted, fontSize: 11.5 }}>
                    顯示更多（還有 {trade.trades.length - tradesLimit} 筆）
                  </button>
                )}
                <div className="mt-1.5" style={{ fontSize: 10.5, color: C.muted, lineHeight: 1.6 }}>
                  黃框是系統故障造成的非策略出場，排除後不列入績效統計；排除可隨時恢復。
                </div>
                </>)}
              </div>
            )}

            {/* 持倉。未實現固定佔一行：數字寬度會變，跟標題同行會忽上忽下 */}
            <div className="mt-3">
              <div className="flex items-baseline gap-2 flex-wrap">
                <span style={{ fontFamily: FONT.display, fontSize: 13 }}>
                  目前持倉 {trade.positions?.length || 0} 筆
                </span>
                {trade.positions?.length > 0 && trade.poll && (
                  <span style={{ fontSize: 10.5, color: C.muted }}>
                    伺服器每 {trade.poll} 秒與交易所對帳
                  </span>
                )}
              </div>
              {trade.positions?.length > 0 && trade.openPnl != null && (
                <div style={{ fontFamily: FONT.data, fontSize: 12, marginTop: 2,
                              color: trade.openPnl > 0 ? UP : trade.openPnl < 0 ? DOWN : C.muted }}>
                  未實現 {trade.openPnl > 0 ? "+" : ""}{trade.openPnl.toFixed(2)} U
                  {trade.openR != null && `　${trade.openR > 0 ? "+" : ""}${trade.openR.toFixed(2)}R`}
                </div>
              )}
            </div>
            {(trade.positions || []).map((p) => (
              <div key={p.symbol} className="mt-2 rounded p-3" style={{ background: C.panel2, border: `1px solid ${C.line}` }}>
                <div className="flex items-center gap-2 flex-wrap">
                  <span style={{ fontFamily: FONT.data, fontSize: 14 }}>{p.symbol}</span>
                  <span className="px-2 py-0.5 rounded" style={{ background: p.side === "LONG" ? UP : DOWN, color: C.ink, fontSize: 11 }}>
                    {p.side === "LONG" ? "做多" : "做空"}
                  </span>
                  <span style={{ fontFamily: FONT.data, fontSize: 11.5, color: C.muted }}>
                    {p.qty} @ {fmtPrice(p.entry)}
                  </span>
                  <button onClick={async () => { const j = await tradeCall("close", { symbol: p.symbol }); if (j?.ok) { setTradeMsg(["ok", `${p.symbol} 已平倉`]); refreshTrade(); } }}
                    disabled={tradeBusy} className="ml-auto px-2.5 py-1 rounded"
                    style={{ background: C.panel, border: `1px solid ${C.red}`, color: C.red, fontSize: 11.5 }}>
                    平倉
                  </button>
                </div>
                {/* 即時損益：這筆現在賺賠多少，以及離停損還有多遠 */}
                {/* 用格線固定各段位置：數字長度會變，靠 flex 自動排會忽上忽下 */}
                {p.mark != null && (
                  <div className="mt-2 px-2.5 py-1.5 rounded"
                    style={{ background: C.panel, border: `1px solid ${(p.pnl ?? 0) >= 0 ? C.line : C.red}`,
                             fontFamily: FONT.data, fontSize: 12,
                             display: "grid", gridTemplateColumns: "1fr auto", alignItems: "center", gap: "2px 8px" }}>
                    <span style={{ whiteSpace: "nowrap" }}>
                      <span style={{ color: C.muted }}>現價 </span>{fmtPrice(p.mark)}
                      {p.leverage && <span style={{ color: C.muted, fontSize: 10.5 }}>　{p.leverage}x</span>}
                    </span>
                    {p.toStopPct != null && (
                      <span style={{ textAlign: "right", whiteSpace: "nowrap",
                                     color: p.toStopPct < 2 ? C.red : p.toStopPct < 5 ? C.gold : C.muted, fontSize: 11 }}>
                        距停損 {p.toStopPct}%
                      </span>
                    )}
                    <span style={{ gridColumn: "1 / -1", whiteSpace: "nowrap",
                                   color: (p.pnl ?? 0) > 0 ? UP : (p.pnl ?? 0) < 0 ? DOWN : C.bone, fontSize: 13 }}>
                      {(p.pnl ?? 0) > 0 ? "+" : ""}{(p.pnl ?? 0).toFixed(2)} U
                      {p.rMultiple != null && `　${p.rMultiple > 0 ? "+" : ""}${Number(p.rMultiple).toFixed(2)}R`}
                    </span>
                  </div>
                )}

                <div className="mt-2 grid grid-cols-3 gap-x-3" style={{ fontFamily: FONT.data, fontSize: 11.5 }}>
                  <div className="flex justify-between"><span style={{ color: C.muted }}>進場</span><span>{fmtPrice(p.entry)}</span></div>
                  <div className="flex justify-between"><span style={{ color: C.muted }}>停損{p.beMoved ? "·成本" : ""}</span><span style={{ color: p.beMoved ? C.teal : DOWN }}>{fmtPrice(p.stop)}</span></div>
                  <div className="flex justify-between"><span style={{ color: C.muted }}>2R 目標</span><span style={{ color: UP }}>{fmtPrice(p.exits?.tp1)}</span></div>
                </div>
                {p.warnings?.length > 0 && (
                  <div className="mt-1.5" style={{ fontSize: 11, color: C.gold }}>{p.warnings.join("；")}</div>
                )}
              </div>
            ))}
          </div>
  );
}
