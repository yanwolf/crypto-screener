import React from "react";
import { CoinCard } from "../components/Cards.jsx";
import { BearMap, RadarMap, Stat } from "../components/Small.jsx";
import { METRICS, PRESETS, QUICK_BEAR, QUICK_BULL } from "../constants.js";
import { C, FONT } from "../theme.js";

/* 由 App.jsx 抽出。共用狀態透過 s 傳入，避免逐一列 props。 */
export default function RadarPage({ s }) {
  const { COLS, DOWN, UP, activePreset, applyQuick, batch, batchN, bgScan, cancelRef, cell, coverage, dirs, kpi, loading, local, minLiq, minMcap, minVol, narrow, noSort, noStable, noWrapped, q, queue, quick, rows, saveState, scan, scanTTL, scannedCount, serverTs, setActivePreset, setBatchN, setBgScan, setDetail, setDirs, setMinLiq, setMinMcap, setMinVol, setNoStable, setNoWrapped, setPreset, setQ, setScanTTL, setShowWeights, setSortDir, setSortKey, setView, setWeights, showWeights, side, sortBy, sortDir, sortKey, sorted, source, srvRefresh, toggleWatch, universe, view, watch, weights } = s;
  return (
<>
        {/* ── 雷達掃描控制：核心入口 ── */}
        <section className="rounded p-3.5" style={{ background: C.panel, border: `1px solid ${C.gold}` }}>
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 mb-2">
            <h2 style={{ fontFamily: FONT.display, fontSize: 18 }}>{side === "bull" ? "量能異動雷達" : "空頭結構雷達"}</h2>
            <span style={{ fontSize: 11.5, color: C.muted, lineHeight: 1.6 }}>
              {side === "bull"
                ? "比對每檔幣「現在的成交量」與「它自己過去 90 天的常態」，找出量突然放大、而且價格有跟上的標的。"
                : "用日線 EMA20、樞紐高低點與分段量能判斷趨勢是否真的轉弱，再分成加速下跌與反彈等待兩個階段。"}
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <select value={batchN} onChange={(e) => setBatchN(+e.target.value)} className="px-2 py-1.5 rounded"
              style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.bone, fontSize: 12 }}>
              <option value={15}>15 檔</option><option value={30}>30 檔</option>
              <option value={50}>50 檔</option><option value={100}>100 檔</option>
            </select>
            {!batch.running ? (
              <>
                <button onClick={() => scan(queue.slice(0, batchN))} disabled={!queue.length || source === "demo"}
                  className="px-3.5 py-1.5 rounded"
                  style={{ background: source === "demo" || !queue.length ? C.panel2 : C.gold, color: source === "demo" || !queue.length ? C.muted : C.ink, border: `1px solid ${C.gold}`, fontSize: 12.5 }}>
                  {source === "demo" ? "示範資料已內含掃描結果" : queue.length ? "補掃未完成的" : "全部已是最新"}
                </button>
                <button onClick={() => scan(sorted.slice(0, batchN))} disabled={source === "demo"}
                  className="px-3 py-1.5 rounded"
                  style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.bone, fontSize: 12 }}>
                  重掃目前排序前段
                </button>
              </>
            ) : (
              <button onClick={() => { cancelRef.current = true; }} className="px-3.5 py-1.5 rounded"
                style={{ background: C.panel2, color: C.gold, border: `1px solid ${C.gold}`, fontSize: 12.5 }}>
                停止（{batch.done}/{batch.total} · {batch.now}）
              </button>
            )}
            <label className="flex items-center gap-1.5 cursor-pointer" style={{ fontSize: 12, color: bgScan ? C.teal : C.bone }}>
              <input type="checkbox" checked={bgScan} onChange={(e) => setBgScan(e.target.checked)} style={{ accentColor: C.teal }} />
              背景持續掃描
            </label>
            <label className="flex items-center gap-1.5" style={{ fontSize: 12 }}>
              資料保鮮
              {local && Object.keys(serverTs).length > 0 ? (
                <span style={{ color: C.teal }} title="伺服器補抓新資料時，網頁會自動重算該檔">跟隨伺服器</span>
              ) : (
                <select value={scanTTL} onChange={(e) => setScanTTL(+e.target.value)} className="px-2 py-1 rounded"
                  style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.bone }}>
                  <option value={6}>6 小時</option><option value={12}>12 小時</option>
                  <option value={24}>24 小時</option><option value={72}>3 天</option>
                </select>
              )}
            </label>
          </div>

          {/* 覆蓋率 */}
          <div className="mt-2.5">
            <div className="flex flex-wrap items-baseline gap-x-3" style={{ fontSize: 11.5 }}>
              <span style={{ color: C.muted }}>深度資料覆蓋率</span>
              <span style={{ fontFamily: FONT.data, color: C.gold }}>{coverage.done}/{coverage.n}　{coverage.pct.toFixed(0)}%</span>
              {coverage.todo > 0 && (
                <span style={{ color: C.muted, fontFamily: FONT.data }}>
                  待掃 {coverage.todo} 檔（
                  {coverage.stale > 0 && <span style={{ color: C.gold }}>過期 {coverage.stale}</span>}
                  {coverage.stale > 0 && coverage.never > 0 && "、"}
                  {coverage.never > 0 && <span style={{ color: C.teal }}>新增 {coverage.never}</span>}
                  ），約需 {coverage.etaMin < 1 ? "不到 1" : coverage.etaMin.toFixed(0)} 分鐘
                </span>
              )}
              {batch.running && <span style={{ color: C.teal }}>掃描中 {batch.done}/{batch.total}</span>}
              {srvRefresh && srvRefresh.n === 0 && srvRefresh.source && srvRefresh.source !== "direct" && (
                <span style={{ color: C.muted, fontFamily: FONT.data }}>深度資料由 {srvRefresh.source} 供應</span>
              )}
              {srvRefresh && srvRefresh.n > 0 && (
                <span style={{ color: C.muted, fontFamily: FONT.data }}>
                  伺服器滾動補抓　新鮮 <span style={{ color: C.teal }}>{srvRefresh.fresh}</span>
                  {srvRefresh.stale > 0 && <>　過期 <span style={{ color: C.gold }}>{srvRefresh.stale}</span></>}
                  {srvRefresh.never > 0 && <>　新增 <span style={{ color: C.teal }}>{srvRefresh.never}</span></>}
                  　每 {srvRefresh.interval}s 補一檔　今日 {srvRefresh.callsToday}/{srvRefresh.budget}
                  {srvRefresh.last && <>　上次 {srvRefresh.last}</>}
                </span>
              )}
              {bgScan && !batch.running && <span style={{ color: C.teal }}>背景掃描運作中</span>}
              <span style={{ color: saveState === "ok" ? C.muted : C.gold }}>
                {saveState === "ok" ? `已保存 ${scannedCount} 檔，重整後不會消失`
                  : saveState === "trimmed" ? `儲存空間接近上限，已自動保留較新的 ${scannedCount} 檔`
                  : saveState === "nostore" ? "此環境無法保存，重整後會清空"
                  : "儲存失敗，重整後可能遺失"}
              </span>
            </div>
            <div className="h-1.5 mt-1.5 rounded" style={{ background: C.line }}>
              <div className="h-1.5 rounded" style={{ width: `${coverage.pct}%`, background: C.gold }} />
            </div>
            <div style={{ fontSize: 11, color: C.muted, marginTop: 6, lineHeight: 1.8 }}>
              免費金鑰每分鐘 30 次，深度資料一檔一次請求，所以第一輪本來就掃不完整個篩選池——這是設計上就接受的事。
              解法是分工：<span style={{ color: C.bone }}>基準量、EMA、樞紐點、90 日高低這些變化慢的資料快取起來，
              現價與現量則由每 90 秒一次的行情端點供應（一次涵蓋 250 檔）</span>。
              因此量能倍數與距離高點都是即時算的，不會停在上次深掃那一刻，只有結構類欄位需要定期補掃。
              掃過的結果會存在瀏覽器，關掉再開不用重來。
            </div>
          </div>

          {/* 一鍵篩選 */}
          <div className="mt-3 pt-3" style={{ borderTop: `1px solid ${C.line}` }}>
            <div className="flex items-baseline gap-2 mb-2">
              <span style={{ fontSize: 12.5 }}>一鍵篩選</span>
              <span style={{ fontSize: 10.5, color: C.muted }}>只會列出已掃描的幣，再按一次可取消</span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {(side === "bull" ? QUICK_BULL : QUICK_BEAR).map((qk) => (
                <button key={qk.id} onClick={() => applyQuick(qk)} title={qk.desc} className="px-2.5 py-1.5 rounded text-left"
                  style={{
                    fontSize: 12,
                    background: quick === qk.id ? (qk.id === "hot" || qk.id === "oversold" ? C.red : side === "bear" ? C.gold : C.teal) : C.panel2,
                    color: quick === qk.id ? C.ink : C.bone,
                    border: `1px solid ${quick === qk.id ? (qk.id === "hot" || qk.id === "oversold" ? C.red : side === "bear" ? C.gold : C.teal) : C.line}`,
                  }}>
                  {qk.name}
                </button>
              ))}
            </div>
            {quick && (
              <div className="mt-2" style={{ fontSize: 11.5, color: C.muted, lineHeight: 1.6 }}>
                {[...QUICK_BULL, ...QUICK_BEAR].find((x) => x.id === quick).desc}
                {scannedCount === 0 && <span style={{ color: C.gold }}>　目前還沒有掃描資料，先按「開始雷達掃描」。</span>}
                {scannedCount > 0 && universe.length === 0 && <span style={{ color: C.gold }}>　已掃描的 {scannedCount} 檔裡沒有符合的，可以擴大掃描範圍。</span>}
              </div>
            )}
          </div>
        </section>

        {kpi && side === "bull" && (
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <Stat label="篩選池" value={`${kpi.n} 檔`} sub={`已掃描 ${kpi.scanned} 檔`} />
            <Stat label="量能放大 2 倍以上" value={`${kpi.surge} 檔`} sub="今天明顯有資金動作" accent={kpi.surge ? C.gold : C.muted} />
            <Stat label="啟動或加速中" value={`${kpi.igniting} 檔`} sub="量價同步往上" accent={C.teal} />
            <Stat label="過熱或追高風險高" value={`${kpi.hot} 檔`} sub="留意回檔" accent={kpi.hot ? C.red : C.muted} />
            <Stat label="24 小時上漲比例" value={`${kpi.upRatio.toFixed(0)}%`} sub="市場廣度" accent={kpi.upRatio >= 50 ? UP : DOWN} />
          </div>
        )}
        {side === "bear" && (
          <div className="rounded px-3.5 py-3" style={{ background: "#241A1A", border: `1px solid ${C.red}`, fontSize: 12, lineHeight: 1.8 }}>
            <span style={{ color: C.red }}>做空前先看這段。</span>
            空頭雷達找的是「趨勢已經轉弱、而且還沒跌完」的結構，不是跌最多的幣。已經連續大跌、乖離過大的幣會被標成追空風險高，
            那是提醒你避開——那種位置最常見的結局是空單被反彈軋掉。第二階段的反彈等待通常比第一階段的追跌安全，因為進場點離止損近。
            所有價位都是依日線 EMA20、樞紐高低點與斐波那契回撤算出的參考區間，不是訊號，也不構成投資建議。
          </div>
        )}
        {kpi && side === "bear" && (
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <Stat label="篩選池" value={`${kpi.n} 檔`} sub={`已掃描 ${kpi.scanned} 檔`} />
            <Stat label="加速下跌" value={`${kpi.dropping} 檔`} sub="第一階段，賣壓延續中" accent={kpi.dropping ? C.red : C.muted} />
            <Stat label="反彈等待做空" value={`${kpi.waiting} 檔`} sub="第二階段，接近壓力帶" accent={kpi.waiting ? C.gold : C.muted} />
            <Stat label="追空風險偏高" value={`${kpi.oversold} 檔`} sub="已連續大跌，別追" accent={kpi.oversold ? C.red : C.muted} />
            <Stat label="24 小時上漲比例" value={`${kpi.upRatio.toFixed(0)}%`} sub="低於三成代表全面走弱" accent={kpi.upRatio >= 50 ? UP : DOWN} />
          </div>
        )}

        <section className="rounded p-3.5" style={{ background: C.panel, border: `1px solid ${C.line}` }}>
          <h2 style={{ fontFamily: FONT.display, fontSize: 16, marginBottom: 4 }}>
            {side === "bull" ? "量能－位置分布" : "回撤－反彈量能分布"}
          </h2>
          {side === "bull"
            ? <RadarMap rows={sorted} onPick={setDetail} />
            : <BearMap rows={sorted} onPick={setDetail} />}
        </section>

        {/* 篩選條件 */}
        <section className="rounded p-3.5 grid gap-3" style={{ background: C.panel, border: `1px solid ${C.line}` }}>
          <div className="flex flex-wrap items-center gap-2">
            <h2 style={{ fontFamily: FONT.display, fontSize: 16 }}>篩選條件</h2>
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜尋代號或名稱"
              className="px-2.5 py-1.5 rounded flex-1" style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.bone, fontSize: 12.5, minWidth: 160 }} />
            <button onClick={() => setShowWeights((v) => !v)} className="px-2.5 py-1.5 rounded"
              style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.muted, fontSize: 12 }}>
              {showWeights ? "收起權重" : "自訂權重"}
            </button>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2" style={{ fontSize: 12 }}>
            <label className="grid gap-1">
              <span style={{ color: C.muted, fontSize: 11 }}>最低市值</span>
              <select value={minMcap} onChange={(e) => setMinMcap(+e.target.value)} className="px-2 py-1.5 rounded"
                style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.bone }}>
                <option value={0}>不限</option><option value={1e6}>$1M</option><option value={1e7}>$10M</option>
                <option value={1e8}>$100M</option><option value={1e9}>$1B</option><option value={1e10}>$10B</option>
              </select>
            </label>
            <label className="grid gap-1">
              <span style={{ color: C.muted, fontSize: 11 }}>最低 24h 成交額</span>
              <select value={minVol} onChange={(e) => setMinVol(+e.target.value)} className="px-2 py-1.5 rounded"
                style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.bone }}>
                <option value={0}>不限</option><option value={1e5}>$100K</option><option value={1e6}>$1M</option>
                <option value={1e7}>$10M</option><option value={1e8}>$100M</option>
              </select>
            </label>
            <label className="grid gap-1">
              <span style={{ color: C.muted, fontSize: 11 }}>流動性門檻</span>
              <select value={minLiq} onChange={(e) => setMinLiq(+e.target.value)} className="px-2 py-1.5 rounded"
                style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.bone }}>
                <option value={0}>不限</option><option value={30}>30 分以上</option>
                <option value={50}>50 分以上（建議）</option><option value={70}>70 分以上</option>
              </select>
            </label>
            <div className="flex flex-col justify-end gap-1" style={{ fontSize: 12 }}>
              <label className="flex items-center gap-1.5 cursor-pointer">
                <input type="checkbox" checked={noStable} onChange={(e) => setNoStable(e.target.checked)} style={{ accentColor: C.gold }} />排除穩定幣
              </label>
              <label className="flex items-center gap-1.5 cursor-pointer">
                <input type="checkbox" checked={noWrapped} onChange={(e) => setNoWrapped(e.target.checked)} style={{ accentColor: C.gold }} />排除包裝幣
              </label>
            </div>
          </div>

          {showWeights && (
            <div className="pt-3" style={{ borderTop: `1px solid ${C.line}` }}>
              <div className="flex flex-wrap gap-1.5 mb-2.5">
                {Object.keys(PRESETS).map((p) => (
                  <button key={p} onClick={() => setPreset(p)} className="px-2.5 py-1 rounded"
                    style={{ fontSize: 11.5, background: activePreset === p ? C.violet : C.panel2, color: activePreset === p ? C.ink : C.bone, border: `1px solid ${activePreset === p ? C.violet : C.line}` }}>{p}</button>
                ))}
                <span style={{ fontSize: 10.5, color: C.muted, alignSelf: "center" }}>
                  這組權重算的是「加權分數」，與雷達分數並列顯示，排序時可各自選用
                </span>
              </div>
              <div className="grid md:grid-cols-2 gap-x-6 gap-y-1.5">
                {METRICS.map((m) => (
                  <div key={m.key} className="flex items-center gap-2.5" title={m.hint}>
                    <span className="shrink-0" style={{ fontSize: narrow ? 11.5 : 12, width: narrow ? 78 : 96 }}>{m.label}</span>
                    <input type="range" min={0} max={10} step={1} value={weights[m.key]}
                      onChange={(e) => { setWeights({ ...weights, [m.key]: +e.target.value }); setActivePreset(null); }}
                      className="flex-1" style={{ accentColor: C.violet }} />
                    <span className="w-5 text-right" style={{ fontFamily: FONT.data, fontSize: 12, color: weights[m.key] ? C.violet : C.muted }}>{weights[m.key]}</span>
                    <button onClick={() => { setDirs({ ...dirs, [m.key]: dirs[m.key] === 1 ? -1 : 1 }); setActivePreset(null); }}
                      className="px-1.5 py-0.5 rounded shrink-0"
                      style={{ fontSize: 10.5, width: 44, background: C.panel2, border: `1px solid ${C.line}`, color: dirs[m.key] === 1 ? C.teal : C.gold }}>
                      {dirs[m.key] === 1 ? "偏高" : "偏低"}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>

        {/* 表格 */}
        <section className="rounded" style={{ background: C.panel, border: `1px solid ${C.line}` }}>
          <div className="px-3.5 py-2.5 flex flex-wrap items-center gap-2" style={{ borderBottom: `1px solid ${C.line}` }}>
            <h2 style={{ fontFamily: FONT.display, fontSize: 16 }}>篩選結果</h2>
            {narrow ? (
              <select value={sortKey} onChange={(e) => { setSortKey(e.target.value); setSortDir(-1); }}
                className="px-2 py-1 rounded" style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.bone, fontSize: 12 }}>
                {(side === "bull"
                  ? [["radar", "雷達分數"], ["rvol7", "量能倍數"], ["turn", "換手率"], ["m24", "24h 漲跌"], ["m7", "7 日漲跌"], ["d90", "距 90 日高"], ["liq", "流動性"], ["mcap", "市值"]]
                  : [["bear", "空頭評分"], ["drop", "跌幅"], ["distEma", "距 EMA20"], ["volRatio", "彈/跌量"], ["chaseShort", "追空風險"], ["liq", "流動性"], ["mcap", "市值"]]
                ).map(([v, t]) => <option key={v} value={v}>{t} ↓</option>)}
              </select>
            ) : (
              <span style={{ fontSize: 10.5, color: C.muted }}>點欄位標題排序 · 點列看完整解讀</span>
            )}
            <div className="ml-auto flex gap-1" style={{ display: side === "bear" ? "none" : undefined }}>
              {[["radar", "雷達欄位"], ["full", "完整欄位"]].map(([v, t]) => (
                <button key={v} onClick={() => setView(v)} className="px-2.5 py-1 rounded"
                  style={{ fontSize: 11.5, background: view === v ? C.panel2 : "transparent", border: `1px solid ${view === v ? C.teal : C.line}`, color: view === v ? C.teal : C.muted }}>{t}</button>
              ))}
            </div>
          </div>
          {narrow ? (
            <div style={{ maxHeight: 640, overflowY: "auto" }}>
              {sorted.map((r) => (
                <CoinCard key={r.id} r={r} side={side} onPick={setDetail}
                  starred={watch.includes(r.id)} onStar={toggleWatch} UP={UP} DOWN={DOWN} />
              ))}
              {!sorted.length && !loading && (
                <div className="px-4 py-10 text-center" style={{ color: C.muted, fontSize: 12.5 }}>
                  沒有幣種符合目前條件。取消一鍵篩選、放寬門檻，或先執行雷達掃描。
                </div>
              )}
            </div>
          ) : (
          <div className="overflow-x-auto" style={{ maxHeight: 640 }}>
            <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 12.5 }}>
              <thead className="sticky top-0" style={{ background: C.panel2, zIndex: 2 }}>
                <tr>
                  {COLS.map((c) => (
                    <th key={c.k} onClick={() => !noSort.has(c.k) && sortBy(c.k)}
                      className="px-2.5 py-2 text-left whitespace-nowrap select-none"
                      style={{ cursor: noSort.has(c.k) ? "default" : "pointer", color: sortKey === c.k ? C.gold : C.muted, fontWeight: 400, fontSize: 11, borderBottom: `1px solid ${C.line}` }}>
                      {c.t}{sortKey === c.k ? (sortDir === -1 ? " ↓" : " ↑") : ""}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sorted.map((r) => (
                  <tr key={r.id} onClick={() => setDetail(r)} className="cursor-pointer"
                    style={{ borderBottom: `1px solid ${C.line}`, opacity: r.scanned ? 1 : 0.62 }}>
                    {COLS.map((c) => <td key={c.k} className="px-2.5 py-1.5 whitespace-nowrap">{cell(r, c.k)}</td>)}
                  </tr>
                ))}
                {!sorted.length && !loading && (
                  <tr><td colSpan={COLS.length} className="px-4 py-10 text-center" style={{ color: C.muted }}>
                    沒有幣種符合目前條件。取消一鍵篩選、放寬門檻，或先執行雷達掃描。
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
          )}
        </section>

        </>
  );
}
