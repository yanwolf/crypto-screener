import React from "react";
import { sendNotify } from "../api.js";
import { C, FONT } from "../theme.js";
import { store } from "../util.js";

/* 由 App.jsx 抽出。共用狀態透過 s 傳入，避免逐一列 props。 */
export default function AlertsPage({ s }) {
  const { DOWN, UP, alertCfg, autoRefresh, autoScan, local, minLiq, mon, monHistory, monMsg, monRefresh, monScope, monSync, monTopN, narrow, notifyLog, pair, rows, scanEvery, setAlertCfg, setAutoScan, setMonMsg, setMonScope, setMonTopN, setNotifyLog, setPair, setScanEvery, setTgAdmin, setTgToken, startPair, target, tg, tgAdmin, tgCall, tgMsg, tgRefresh, tgSetup, tgToken, watch } = s;
  return (
<>
            <section className="rounded p-3.5" style={{ background: C.panel, border: `1px solid ${alertCfg.on ? C.teal : C.line}` }}>
              <div className="flex flex-wrap items-center gap-3">
                <h2 style={{ fontFamily: FONT.display, fontSize: 17 }}>訊號監控</h2>
                <button onClick={() => setAlertCfg({ ...alertCfg, on: !alertCfg.on })} className="px-3 py-1.5 rounded"
                  style={{ background: alertCfg.on ? C.teal : C.panel2, color: alertCfg.on ? C.ink : C.muted, border: `1px solid ${alertCfg.on ? C.teal : C.line}`, fontSize: 12.5 }}>
                  {alertCfg.on ? "監控中，點此停止" : "開啟監控"}
                </button>
                <label className="flex items-center gap-1.5" style={{ fontSize: 12 }}>
                  監控範圍
                  <select value={alertCfg.scope} onChange={(e) => setAlertCfg({ ...alertCfg, scope: e.target.value })}
                    className="px-2 py-1 rounded" style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.bone }}>
                    <option value="watch">只看觀察清單</option><option value="scanned">全部已掃描的幣</option>
                  </select>
                </label>
                <label className="flex items-center gap-1.5" style={{ fontSize: 12 }}>
                  <input type="checkbox" checked={autoScan} onChange={(e) => setAutoScan(e.target.checked)} style={{ accentColor: C.teal }} />
                  自動重掃觀察清單
                  <select value={scanEvery} onChange={(e) => setScanEvery(+e.target.value)} disabled={!autoScan}
                    className="px-2 py-1 rounded" style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.bone }}>
                    <option value={5}>每 5 分鐘</option><option value={15}>每 15 分鐘</option>
                    <option value={30}>每 30 分鐘</option><option value={60}>每小時</option>
                  </select>
                </label>
              </div>
              <p style={{ fontSize: 11.5, color: C.muted, lineHeight: 1.8, marginTop: 8 }}>
                行情每 90 秒更新一次（標題列的自動更新），觀察清單的深度資料依上面的間隔重掃。
                每次資料更新後就會重新評估一次訊號；同一檔幣在冷卻時間內不會重複通知。
                {!store.ok && " 目前環境無法保存設定，重整後會回到預設值。"}
              </p>
            </section>


            <section className="rounded p-3.5" style={{ background: C.panel, border: `1px solid ${mon && mon.on ? C.teal : C.line}` }}>
              <div className="flex flex-wrap items-center gap-2 mb-2">
                <h3 style={{ fontFamily: FONT.display, fontSize: 15 }}>伺服器背景監控</h3>
                {!local ? <span style={{ fontSize: 11.5, color: C.gold }}>需要伺服器版本才能使用</span>
                  : mon == null ? <span style={{ fontSize: 11.5, color: C.muted }}>讀取中…</span>
                  : !mon.engine ? <span style={{ fontSize: 11.5, color: C.red }}>伺服器缺少 engine.py</span>
                  : <span style={{ fontSize: 11.5, color: mon.on ? C.teal : C.muted }}>
                      {mon.on ? `運作中　${mon.scope === "top" ? `市值前 ${mon.topN} 檔` : `觀察清單 ${mon.watch.length} 檔`}` : "尚未啟動"}
                      {mon.lastRun ? `　上次檢查 ${new Date(mon.lastRun).toLocaleTimeString("zh-TW")}（${mon.lastCount} 檔）` : ""}
                    </span>}
              </div>

              <p style={{ fontSize: 11.5, color: C.muted, lineHeight: 1.85 }}>
                前面那個「訊號監控」只在網頁開著時運作，關掉分頁就停了。
                這裡則是把觀察清單與門檻同步到伺服器，由伺服器自己定時計算並推播，
                <span style={{ color: C.bone }}>手機關掉、App 關掉都照常運作</span>。
                伺服器用的是與網頁完全相同的評分公式，所以推播的分數和你在畫面上看到的一致。
              </p>

              {local && mon && mon.engine && (
                <>
                  <div className="flex flex-wrap items-center gap-2 mt-3" style={{ fontSize: 12 }}>
                    <span style={{ color: C.muted }}>伺服器監控範圍</span>
                    <select value={monScope} onChange={(e) => setMonScope(e.target.value)} className="px-2 py-1 rounded"
                      style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.bone }}>
                      <option value="watch">觀察清單（{watch.length} 檔）</option>
                      <option value="top">市值前 N 檔</option>
                    </select>
                    {monScope === "top" && (
                      <select value={monTopN} onChange={(e) => setMonTopN(+e.target.value)} className="px-2 py-1 rounded"
                        style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.bone }}>
                        <option value={50}>前 50</option><option value={100}>前 100</option>
                        <option value={150}>前 150</option><option value={250}>前 250</option>
                      </select>
                    )}
                  </div>

                  {alertCfg.scope === "scanned" && monScope === "watch" && (
                    <div className="mt-2 px-2.5 py-2 rounded" style={{ background: "#241A1A", border: `1px solid ${C.gold}`, fontSize: 11.5, lineHeight: 1.8 }}>
                      上面的「訊號監控」範圍選了<span style={{ color: C.bone }}>全部已掃描的幣</span>，但伺服器這裡是觀察清單，兩邊監控的標的不一樣。
                      「已掃描」是瀏覽器端的概念，伺服器有自己的快取，無法對應同一批幣，所以伺服器改用<span style={{ color: C.bone }}>市值前 N 檔</span>來表達「全部」。
                      要讓兩邊一致，請把這裡改成「市值前 N 檔」。
                    </div>
                  )}

                  <div className="flex flex-wrap items-center gap-2 mt-2">
                    <button onClick={() => monSync(true)} disabled={monScope === "watch" && !watch.length}
                      className="px-3 py-1.5 rounded"
                      style={{ background: (monScope === "top" || watch.length) ? C.teal : C.panel2,
                        color: (monScope === "top" || watch.length) ? C.ink : C.muted, fontSize: 12 }}>
                      {mon.on ? "重新同步設定" : "同步並啟動背景監控"}
                    </button>
                    {mon.on && (
                      <button onClick={() => monSync(false)} className="px-3 py-1.5 rounded"
                        style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.muted, fontSize: 12 }}>停止</button>
                    )}
                    <button onClick={async () => {
                        setMonMsg(["ok", "檢查中…"]);
                        const j = await tgCall("/api/monitor/run", {});
                        setMonMsg(j.error ? ["err", "執行失敗：" + j.error]
                          : ["ok", `檢查了 ${j.checked} 檔，觸發 ${j.fired} 則`]);
                        monRefresh(); monHistory();
                      }} className="px-3 py-1.5 rounded"
                      style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.bone, fontSize: 12 }}>立即執行一次</button>
                    {monScope === "watch" && !watch.length && <span style={{ fontSize: 11.5, color: C.gold }}>觀察清單是空的，先去雷達頁加星號</span>}
                  </div>

                  {(mon.adminRequired || (tg && tg.adminRequired)) && (
                    <div className="flex flex-wrap items-center gap-2 mt-2.5">
                      <input value={tgAdmin} onChange={(e) => setTgAdmin(e.target.value)} type="password"
                        placeholder="管理金鑰" className="px-2 py-1.5 rounded"
                        style={{ background: C.panel2, border: `1px solid ${tgAdmin ? C.line : C.gold}`, color: C.bone, fontSize: 12, width: 180 }} />
                      <span style={{ fontSize: 11, color: C.muted, lineHeight: 1.7 }}>
                        這是你在 Zeabur 的 Variables 裡設定的 <span style={{ fontFamily: FONT.data }}>ADMIN_KEY</span>。
                        填一次就會記住。不想要的話，把那個環境變數刪掉再重新部署即可。
                      </span>
                    </div>
                  )}

                  <div style={{ fontSize: 11.5, color: C.muted, marginTop: 8, lineHeight: 1.8 }}>
                    同步的內容是<span style={{ color: C.bone }}>
                      {monScope === "top" ? `市值前 ${monTopN} 檔` : `目前的觀察清單（${watch.length} 檔）`}與上面設定的多空門檻</span>。
                    {monScope === "top" && "　範圍越大首輪要抓的歷史資料越多，建議搭配環境變數 PREFETCH 讓伺服器先把快取養起來。"}
                    之後改了條件或增減觀察清單，要再按一次「重新同步設定」才會生效。
                    通知走伺服器已配對的 Telegram 聊天室，所以請先完成下方的機器人配對。
                  </div>

                  {(() => {
                    const every = scanEvery && mon.on ? scanEvery : 30;
                    const perMonth = Math.round((1440 / every) * 30 * (1 + (mon.maxRefresh ?? 8) * 0.3)
                      + (autoRefresh ? (86400 / 90) * 30 : 0));
                    const over = perMonth > 9000;
                    return (
                      <div className="mt-2 px-2.5 py-2 rounded"
                        style={{ background: over ? "#241A1A" : C.panel2, border: `1px solid ${over ? C.red : C.line}`, fontSize: 11.5, lineHeight: 1.8 }}>
                        <span style={{ color: over ? C.red : C.teal }}>額度預估　每月約 {perMonth.toLocaleString()} 次</span>
                        <span style={{ color: C.muted }}>　免費 Demo Key 上限 10,000 次／月</span>
                        {over && (
                          <div style={{ color: C.red }}>
                            超過免費額度。把伺服器的 MONITOR_EVERY 調到 30 分鐘以上、關閉標題列的「自動更新」，或升級 CoinGecko 方案。
                          </div>
                        )}
                        <div style={{ color: C.muted }}>
                          歷史資料在 {mon.histTTL ?? 12} 小時內不重抓，每輪最多補抓 {mon.maxRefresh ?? 8} 檔；
                          上一輪實際補抓 {mon.lastRefreshed ?? 0} 檔。
                        </div>
                      </div>
                    );
                  })()}

                  {mon.gateStats && mon.gateStats.n > 0 && (
                    <div className="mt-2 px-2.5 py-2 rounded" style={{ background: C.panel2, border: `1px solid ${C.line}`, fontSize: 11.5 }}>
                      <div style={{ color: C.bone, marginBottom: 4 }}>
                        上輪 {mon.gateStats.n} 檔的各條件通過率
                        <span style={{ color: C.muted, fontSize: 10.5 }}>　通過率最低的那條，就是訊號少的主因</span>
                      </div>
                      {[["bull", "多頭", UP], ["bear", "空頭", DOWN]].map(([k, lab, col]) => {
                        const st = mon.gateStats[k] || {};
                        const rows = Object.entries(st).map(([name, v]) => [name, v.pass, v.pass + v.fail])
                          .sort((a, b) => a[1] / a[2] - b[1] / b[2]);
                        return (
                          <div key={k} className="mt-1.5">
                            <span style={{ color: col }}>{lab}</span>
                            {rows.map(([name, p, t]) => (
                              <div key={name} className="flex justify-between" style={{ fontFamily: FONT.data, fontSize: 11 }}>
                                <span style={{ color: C.muted }}>{name}</span>
                                <span style={{ color: p / t < 0.1 ? C.red : p / t < 0.3 ? C.gold : C.bone }}>
                                  {p}/{t}　{Math.round(100 * p / t)}%
                                </span>
                              </div>
                            ))}
                          </div>
                        );
                      })}
                    </div>
                  )}

                  {mon.lastError && (
                    <div style={{ fontSize: 11.5, color: C.red, marginTop: 6 }}>上次執行發生錯誤：{mon.lastError}</div>
                  )}
                  {monMsg && (
                    <div style={{ fontSize: 11.5, marginTop: 6, color: monMsg[0] === "ok" ? C.teal : C.red }}>{monMsg[1]}</div>
                  )}
                </>
              )}
            </section>

            <div className="grid md:grid-cols-2 gap-4">
              <section className="rounded p-3.5" style={{ background: C.panel, border: `1px solid ${C.line}` }}>
                <h3 style={{ fontFamily: FONT.display, fontSize: 15, color: UP }}>多頭提醒條件（須全部滿足）</h3>
                <p style={{ fontSize: 11.5, color: C.muted, margin: "4px 0 8px", lineHeight: 1.7 }}>
                  量能放大、價格走強、流動性合格、追高風險不過高，四項缺一不可，再加上雷達分數門檻。
                </p>
                <label className="flex items-center gap-1.5 cursor-pointer mb-2" style={{ fontSize: 12 }}>
                  <input type="checkbox" checked={alertCfg.bull.requireBlade} style={{ accentColor: UP }}
                    onChange={(e) => setAlertCfg({ ...alertCfg, bull: { ...alertCfg.bull, requireBlade: e.target.checked } })} />
                  另需三刀流站上小綠與小橘
                  <span style={{ color: C.muted, fontSize: 11 }}>60 分 K 方向確認，避免逆勢進場</span>
                </label>
                {[["minRvol", "量能倍數至少", 1, 6, 0.5, "x"], ["minRadar", "雷達分數至少", 40, 95, 5, ""],
                  ["minLiq", "流動性至少", 20, 90, 5, ""], ["maxChase", "追高風險上限", 30, 100, 5, ""]].map(([k, lab, lo, hi, st, unit]) => (
                  <div key={k} className="flex items-center gap-2.5 mb-1.5">
                    <span className="shrink-0" style={{ fontSize: narrow ? 11.5 : 12, width: narrow ? 88 : 112 }}>{lab}</span>
                    <input type="range" min={lo} max={hi} step={st} value={alertCfg.bull[k]} className="flex-1" style={{ accentColor: UP }}
                      onChange={(e) => setAlertCfg({ ...alertCfg, bull: { ...alertCfg.bull, [k]: +e.target.value } })} />
                    <span className="w-12 text-right" style={{ fontFamily: FONT.data, fontSize: 12, color: C.gold }}>{alertCfg.bull[k]}{unit}</span>
                  </div>
                ))}
              </section>

              <section className="rounded p-3.5" style={{ background: C.panel, border: `1px solid ${C.line}` }}>
                <h3 style={{ fontFamily: FONT.display, fontSize: 15, color: DOWN }}>空頭提醒條件（須全部滿足）</h3>
                <p style={{ fontSize: 11.5, color: C.muted, margin: "4px 0 8px", lineHeight: 1.7 }}>
                  趨勢向下、反彈失敗或賣壓延續、流動性合格、止損位置清晰，四項缺一不可，再加上空頭評分門檻。
                </p>
                <label className="flex items-center gap-1.5 cursor-pointer mb-2" style={{ fontSize: 12 }}>
                  <input type="checkbox" checked={alertCfg.bear.requireBlade} style={{ accentColor: DOWN }}
                    onChange={(e) => setAlertCfg({ ...alertCfg, bear: { ...alertCfg.bear, requireBlade: e.target.checked } })} />
                  另需三刀流跌破小綠與小橘
                  <span style={{ color: C.muted, fontSize: 11 }}>60 分 K 方向確認</span>
                </label>
                {[["minStruct", "空頭結構至少", 50, 100, 5, ""], ["maxVolRatio", "反彈量比上限", 0.4, 1, 0.05, "x"],
                  ["minLiq", "流動性至少", 20, 90, 5, ""], ["maxStopPct", "止損距離上限", 4, 25, 1, "%"],
                  ["minBear", "空頭評分至少", 40, 95, 5, ""]].map(([k, lab, lo, hi, st, unit]) => (
                  <div key={k} className="flex items-center gap-2.5 mb-1.5">
                    <span className="shrink-0" style={{ fontSize: narrow ? 11.5 : 12, width: narrow ? 88 : 112 }}>{lab}</span>
                    <input type="range" min={lo} max={hi} step={st} value={alertCfg.bear[k]} className="flex-1" style={{ accentColor: DOWN }}
                      onChange={(e) => setAlertCfg({ ...alertCfg, bear: { ...alertCfg.bear, [k]: +e.target.value } })} />
                    <span className="w-12 text-right" style={{ fontFamily: FONT.data, fontSize: 12, color: C.gold }}>{alertCfg.bear[k]}{unit}</span>
                  </div>
                ))}
              </section>
            </div>

            <section className="rounded p-3.5" style={{ background: C.panel, border: `1px solid ${C.line}` }}>
              <h3 style={{ fontFamily: FONT.display, fontSize: 15 }}>重複通知的判定</h3>
              <p style={{ fontSize: 11.5, color: C.muted, margin: "4px 0 10px", lineHeight: 1.8 }}>
                同一檔幣的同一個方向，首次成立後就不再重複通知。只有這四種狀態改變會再發：
                評分比先前最高再高出設定值、價格完成突破、條件一度失效後重新成立、以及冷卻時間到期後的新變化。
              </p>
              {[["minDelta", "評分需再提高", 3, 25, 1, " 分"], ["breakoutPct", "突破需超過", 0.5, 8, 0.5, "%"],
                ["cooldownMin", "冷卻時間", 15, 360, 15, " 分鐘"]].map(([k, lab, lo, hi, st, unit]) => (
                <div key={k} className="flex items-center gap-2.5 mb-1.5">
                  <span className="shrink-0" style={{ fontSize: narrow ? 11.5 : 12, width: narrow ? 88 : 112 }}>{lab}</span>
                  <input type="range" min={lo} max={hi} step={st} value={alertCfg[k]} className="flex-1" style={{ accentColor: C.gold }}
                    onChange={(e) => setAlertCfg({ ...alertCfg, [k]: +e.target.value })} />
                  <span className="w-16 text-right" style={{ fontFamily: FONT.data, fontSize: 12, color: C.gold }}>{alertCfg[k]}{unit}</span>
                </div>
              ))}
            </section>

            <section className="rounded p-3.5 grid gap-2.5" style={{ background: C.panel, border: `1px solid ${C.line}` }}>
              <h3 style={{ fontFamily: FONT.display, fontSize: 15 }}>通知管道</h3>

              <div className="flex flex-wrap items-center gap-3" style={{ fontSize: 12 }}>
                <label className="flex items-center gap-1.5 cursor-pointer">
                  <input type="checkbox" checked={alertCfg.ch.browser} style={{ accentColor: C.teal }}
                    onChange={async (e) => {
                      if (e.target.checked && typeof Notification !== "undefined" && Notification.permission !== "granted") {
                        try { await Notification.requestPermission(); } catch (x) {}
                      }
                      setAlertCfg({ ...alertCfg, ch: { ...alertCfg.ch, browser: e.target.checked } });
                    }} />
                  瀏覽器通知
                  <span style={{ color: C.muted, fontSize: 11 }}>
                    {typeof Notification === "undefined" ? "（此環境不支援）" : `（${Notification.permission === "granted" ? "已授權" : "未授權"}）`}
                  </span>
                </label>
                <span style={{ fontSize: 11.5, color: local ? C.teal : C.gold }}>
                  {local ? "已連上伺服器，通知由伺服器發送" : "沒有伺服器，只能由瀏覽器直送"}
                </span>
                <button onClick={async () => {
                    const res = await sendNotify({ ...alertCfg.ch, useServer: local },
                      { title: "測試通知", text: "這是一則測試訊息，收到代表管道設定正確。" });
                    setNotifyLog((l) => [{ ts: Date.now(), sym: "測試", type: "first", res }, ...l].slice(0, 20));
                  }} className="px-3 py-1.5 rounded" style={{ background: C.gold, color: C.ink, fontSize: 12 }}>發送測試通知</button>
              </div>

              {local ? (
                <div className="rounded p-3" style={{ background: C.panel2 }}>
                  <div className="flex flex-wrap items-center gap-2 mb-2">
                    <span style={{ fontSize: 13 }}>Telegram</span>
                    {tg == null ? <span style={{ fontSize: 11.5, color: C.muted }}>讀取中…</span>
                      : !tg.hasToken ? <span style={{ fontSize: 11.5, color: C.gold }}>尚未設定機器人</span>
                      : <span style={{ fontSize: 11.5, color: C.teal }}>
                          機器人 @{tg.bot || "已設定"}　已配對 {tg.chats.length} 個聊天室
                        </span>}
                    <button onClick={tgRefresh} className="px-2 py-1 rounded"
                      style={{ background: C.panel, border: `1px solid ${C.line}`, color: C.muted, fontSize: 11.5 }}>重新整理</button>
                  </div>

                  {tg && !tg.hasToken && (
                    <div className="grid gap-2">
                      <div style={{ fontSize: 11.5, color: C.muted, lineHeight: 1.8 }}>
                        <span style={{ color: C.bone }}>步驟一　建立機器人</span><br />
                        在 Telegram 搜尋 <span style={{ color: C.teal }}>@BotFather</span> → 送出 <span style={{ fontFamily: FONT.data }}>/newbot</span> →
                        取個名字 → 它會給你一串像 <span style={{ fontFamily: FONT.data }}>123456:AAE…</span> 的 Token，貼到下面。
                        Token 只會存在伺服器，不會留在瀏覽器。
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <input value={tgToken} onChange={(e) => setTgToken(e.target.value)} placeholder="貼上 Bot Token"
                          className="px-2 py-1.5 rounded flex-1" style={{ background: C.panel, border: `1px solid ${C.line}`, color: C.bone, fontSize: 12, minWidth: 200 }} />
                        {tg.adminRequired && (
                          <input value={tgAdmin} onChange={(e) => setTgAdmin(e.target.value)} placeholder="管理金鑰"
                            className="px-2 py-1.5 rounded" style={{ background: C.panel, border: `1px solid ${C.line}`, color: C.bone, fontSize: 12, width: 130 }} />
                        )}
                        <button onClick={tgSetup} disabled={!tgToken.trim()} className="px-3 py-1.5 rounded"
                          style={{ background: tgToken.trim() ? C.teal : C.panel, color: tgToken.trim() ? C.ink : C.muted, fontSize: 12 }}>驗證並儲存</button>
                      </div>
                    </div>
                  )}

                  {tg && tg.hasToken && (
                    <div className="grid gap-2">
                      {!pair && (
                        <div className="flex flex-wrap items-center gap-2">
                          <button onClick={startPair} className="px-3 py-1.5 rounded"
                            style={{ background: C.teal, color: C.ink, fontSize: 12 }}>開始配對</button>
                          <span style={{ fontSize: 11.5, color: C.muted }}>
                            產生一組一次性代碼，你在 Telegram 按一下就完成，不必自己查 chat id
                          </span>
                        </div>
                      )}

                      {pair && pair.state === "waiting" && (
                        <div className="rounded p-2.5" style={{ background: C.panel, border: `1px dashed ${C.teal}` }}>
                          <div style={{ fontSize: 12, lineHeight: 1.8 }}>
                            點下面的按鈕，Telegram 會自動開啟並帶入代碼，按「開始／START」即可。
                          </div>
                          <div className="flex flex-wrap items-center gap-2 mt-2">
                            <a href={pair.link} target="_blank" rel="noreferrer" className="px-3 py-1.5 rounded"
                              style={{ background: C.teal, color: C.ink, fontSize: 12.5 }}>在 Telegram 開啟</a>
                            <span style={{ fontFamily: FONT.data, fontSize: 16, color: C.gold, letterSpacing: "0.12em" }}>{pair.code}</span>
                            <span style={{ fontSize: 11, color: C.muted }}>
                              或手動傳送　<span style={{ fontFamily: FONT.data }}>/start {pair.code}</span>　給 @{pair.bot}
                            </span>
                          </div>
                          <div className="flex items-center gap-2 mt-2" style={{ fontSize: 11.5, color: C.muted }}>
                            <span style={{ color: C.teal }}>等待配對中…</span>
                            {pair.left != null && <span>剩餘 {Math.max(0, Math.floor(pair.left / 60))} 分鐘</span>}
                            <button onClick={() => setPair(null)} className="px-2 py-0.5 rounded"
                              style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.muted, fontSize: 11 }}>取消</button>
                          </div>
                        </div>
                      )}

                      {pair && pair.state === "paired" && (
                        <div className="rounded p-2.5" style={{ background: C.panel, border: `1px solid ${C.teal}`, fontSize: 12 }}>
                          <span style={{ color: C.teal }}>配對成功　</span>{pair.chat.name}
                          <button onClick={() => setPair(null)} className="px-2 py-0.5 rounded ml-2"
                            style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.muted, fontSize: 11 }}>完成</button>
                        </div>
                      )}

                      {pair && pair.state === "expired" && (
                        <div className="rounded p-2.5" style={{ background: C.panel, border: `1px solid ${C.gold}`, fontSize: 12 }}>
                          代碼已過期。
                          <button onClick={startPair} className="ml-1 underline" style={{ color: C.gold }}>重新產生</button>
                        </div>
                      )}

                      {tg.chats.length > 0 && (
                        <div className="grid gap-1">
                          {tg.chats.map((c) => (
                            <div key={c.id} className="flex items-center gap-2" style={{ fontSize: 11.5 }}>
                              <span style={{ color: C.teal }}>●</span>
                              <span>{c.name}</span>
                              <span style={{ fontFamily: FONT.data, color: C.muted }}>{c.id}</span>
                              <button onClick={async () => { await tgCall("/api/tg/unpair", { id: c.id, admin: tgAdmin.trim() }); tgRefresh(); }}
                                className="ml-auto px-2 py-0.5 rounded"
                                style={{ background: C.panel, border: `1px solid ${C.line}`, color: C.muted, fontSize: 11 }}>移除</button>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}

                  {tgMsg && (
                    <div style={{ fontSize: 11.5, marginTop: 6, color: tgMsg[0] === "ok" ? C.teal : C.red, lineHeight: 1.7 }}>{tgMsg[1]}</div>
                  )}
                </div>
              ) : (
                <div className="grid md:grid-cols-2 gap-2">
                  <div className="md:col-span-2" style={{ fontSize: 11.5, color: C.muted, lineHeight: 1.8 }}>
                    這個頁面沒有連上伺服器，只能由瀏覽器直送 Telegram，需要自己填 Token 與 Chat ID。
                    部署伺服器版本後會改成一鍵配對，電子郵件與 Discord 也才能運作。
                  </div>
                  <input value={alertCfg.ch.tgToken} onChange={(e) => setAlertCfg({ ...alertCfg, ch: { ...alertCfg.ch, tgToken: e.target.value } })}
                    placeholder="Telegram Bot Token（@BotFather）" className="px-2 py-1.5 rounded"
                    style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.bone, fontSize: 12 }} />
                  <input value={alertCfg.ch.tgChat} onChange={(e) => setAlertCfg({ ...alertCfg, ch: { ...alertCfg.ch, tgChat: e.target.value } })}
                    placeholder="Chat ID（@userinfobot 查詢）" className="px-2 py-1.5 rounded"
                    style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.bone, fontSize: 12 }} />
                  <input value={alertCfg.ch.discord} onChange={(e) => setAlertCfg({ ...alertCfg, ch: { ...alertCfg.ch, discord: e.target.value } })}
                    placeholder="Discord Webhook 網址（瀏覽器直送常被跨域擋下）" className="px-2 py-1.5 rounded md:col-span-2"
                    style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.bone, fontSize: 12 }} />
                </div>
              )}

              {notifyLog.length > 0 && (
                <div className="pt-2" style={{ borderTop: `1px solid ${C.line}` }}>
                  <div style={{ fontSize: 11.5, color: C.muted, marginBottom: 4 }}>最近的發送結果</div>
                  {notifyLog.map((l, i) => (
                    <div key={i} style={{ fontSize: 11.5, lineHeight: 1.7 }}>
                      <span style={{ fontFamily: FONT.data, color: C.muted }}>{new Date(l.ts).toLocaleTimeString("zh-TW")}</span>　
                      {l.sym}　
                      {l.res.map(([name, ok, note], j) => (
                        <span key={j} style={{ color: ok ? C.teal : C.red }}>{name} {ok ? "成功" : "失敗"}{note ? `（${note}）` : ""}　</span>
                      ))}
                      {!l.res.length && <span style={{ color: C.muted }}>沒有啟用任何管道</span>}
                    </div>
                  ))}
                </div>
              )}
            </section>
          </>
  );
}
