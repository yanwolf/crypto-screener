import React from "react";
import { gate } from "../api.js";
import { BLADE } from "../blades.js";
import { BearTag, BladeTag, RangeBar, RiskTag, Sparkline, StageTag } from "../components/Small.jsx";
import { BEAR_STAGES, STAGES } from "../constants.js";
import { C, FONT } from "../theme.js";
import { derivScoreAdjust, stopPct, tradeGate } from "../trade.js";
import { clamp, fmtPct, fmtPrice, fmtUsd, r1, store } from "../util.js";

/* 由 App.jsx 抽出。共用狀態透過 s 傳入，避免逐一列 props。 */
export default function DetailModal({ s }) {
  const { DOWN, UP, batch, deriv, derivBusy, detail, refreshTrade, scan, setDetail, setTradeMsg, setTradeSide, side, source, target, tgAdmin, tone, trade, tradeBaseRef, tradeBusy, tradeCall, tradeMsg, tradeSide } = s;
  return (
<div className="fixed inset-0 flex items-end md:items-center justify-center p-0 md:p-6"
          style={{ background: "rgba(6,10,12,0.75)", zIndex: 50 }} onClick={() => setDetail(null)}>
          <div className="w-full md:max-w-xl rounded-t-lg md:rounded-lg p-4"
            style={{ background: C.panel, border: `1px solid ${C.gold}`, maxHeight: "88vh", overflowY: "auto" }}
            onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center gap-3">
              {detail.img && <img src={detail.img} alt="" width={30} height={30} style={{ borderRadius: 6 }} />}
              <div>
                <div style={{ fontFamily: FONT.display, fontSize: 20 }}>{detail.name}</div>
                <div style={{ fontFamily: FONT.data, fontSize: 11.5, color: C.muted }}>{detail.sym} · 市值排名 {detail.rank ?? "—"}</div>
              </div>
              <button onClick={() => setDetail(null)} className="ml-auto px-2 py-1 rounded"
                style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.muted, fontSize: 12 }}>關閉</button>
            </div>

            <div className="mt-3 flex flex-wrap items-end gap-x-4 gap-y-1">
              <div style={{ fontFamily: FONT.data, fontSize: 26 }}>{fmtPrice(detail.price)}</div>
              <div style={{ fontFamily: FONT.data, fontSize: 14, color: tone(detail.m24) }}>{fmtPct(detail.m24)} 今日</div>
              <div className="ml-auto flex items-center gap-2">
                <StageTag stage={detail.stage} />
                {detail.radar != null && (
                  <span style={{ fontFamily: FONT.data, fontSize: 20, color: C.gold }}>{detail.radar.toFixed(0)}</span>
                )}
                <span style={{ fontSize: 11, color: C.muted }}>雷達分數</span>
              </div>
            </div>

            {/* ── 衍生品面板 ── */}
            {(() => {
              const dv = deriv[detail.id];
              if (derivBusy && !dv) return (
                <div className="mt-3 px-3 py-2 rounded" style={{ background: C.panel2, border: `1px solid ${C.line}`, fontSize: 12, color: C.muted }}>
                  讀取合約數據…
                </div>
              );
              if (!dv) return null;
              if (dv.none) return (
                <div className="mt-3 px-3 py-2 rounded" style={{ background: C.panel2, border: `1px solid ${C.line}`, fontSize: 12, color: C.muted }}>
                  Binance 沒有這檔的 USDT 永續合約，或資料暫時取不到。衍生品指標僅適用於有合約的幣種。
                </div>
              );
              const posTone = (v, inv) => (v == null ? C.muted : (inv ? -v : v) > 0 ? UP : (inv ? -v : v) < 0 ? DOWN : C.bone);
              const qColor = dv.bias > 0.5 ? UP : dv.bias < -0.5 ? DOWN : C.gold;
              const [adjB, whyB] = derivScoreAdjust(detail.score ?? 50, dv, side === "bear" ? "bear" : "bull");
              return (
                <div className="mt-3 rounded" style={{ background: C.panel2, border: `1px solid ${C.line}`, padding: 12 }}>
                  <div className="flex items-center gap-2 flex-wrap">
                    <span style={{ fontFamily: FONT.display, fontSize: 13 }}>合約市場結構</span>
                    <span style={{ fontFamily: FONT.data, fontSize: 10.5, color: C.muted }}>{dv.symbol} · Binance 永續</span>
                    {dv.quadrant && (
                      <span className="px-2 py-0.5 rounded" style={{ background: qColor, color: C.ink, fontSize: 11.5 }}>{dv.quadrant}</span>
                    )}
                  </div>

                  {dv.note && <div className="mt-2" style={{ fontSize: 12, color: C.bone, lineHeight: 1.7 }}>{dv.note}</div>}

                  <div className="mt-2.5 grid grid-cols-2 gap-x-4 gap-y-1.5" style={{ fontFamily: FONT.data, fontSize: 11.5 }}>
                    <div className="flex justify-between">
                      <span style={{ color: C.muted }}>未平倉量 24h</span>
                      <span style={{ color: posTone(dv.oiChg24h) }}>{dv.oiChg24h == null ? "—" : fmtPct(dv.oiChg24h)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span style={{ color: C.muted }}>未平倉量 4h</span>
                      <span style={{ color: posTone(dv.oiChg4h) }}>{dv.oiChg4h == null ? "—" : fmtPct(dv.oiChg4h)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span style={{ color: C.muted }}>資金費率年化</span>
                      <span style={{ color: posTone(dv.fundingApr) }}>{dv.fundingApr == null ? "—" : fmtPct(dv.fundingApr)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span style={{ color: C.muted }}>多空帳戶比</span>
                      <span style={{ color: dv.lsRatio == null ? C.muted : dv.lsRatio > 1 ? UP : DOWN }}>{dv.lsRatio == null ? "—" : dv.lsRatio.toFixed(2)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span style={{ color: C.muted }}>主動買賣比</span>
                      <span style={{ color: dv.taker == null ? C.muted : dv.taker > 1 ? UP : DOWN }}>{dv.taker == null ? "—" : dv.taker.toFixed(2)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span style={{ color: C.muted }}>擁擠度</span>
                      <span style={{ color: (dv.crowd ?? 0) > 65 ? C.red : C.bone }}>{dv.crowd == null ? "—" : dv.crowd}</span>
                    </div>
                  </div>

                  {dv.fuel != null && Math.abs(dv.fuel) > 20 && (
                    <div className="mt-2.5 px-2.5 py-1.5 rounded" style={{ background: C.panel, border: `1px solid ${dv.fuel > 0 ? C.line : C.red}`, fontSize: 11.5, lineHeight: 1.7 }}>
                      {dv.fuel > 0
                        ? `空方部位偏擁擠（燃料 ${dv.fuel}）。若價格向上突破，空單回補可能加速漲勢；反過來說，這也是空單的風險位置。`
                        : `多方部位偏擁擠（燃料 ${dv.fuel}）。多單付出高額資金費率支撐行情，一旦轉弱容易連鎖平倉。`}
                    </div>
                  )}

                  {whyB && (
                    <div className="mt-2" style={{ fontSize: 11.5, color: C.muted, lineHeight: 1.7 }}>
                      對{side === "bear" ? "空頭" : "多頭"}評分的影響：{detail.score == null ? "—" : r1(detail.score)} → <span style={{ color: adjB > (detail.score ?? 50) ? UP : adjB < (detail.score ?? 50) ? DOWN : C.bone }}>{adjB}</span>　{whyB}
                    </div>
                  )}

                  <div className="mt-2" style={{ fontSize: 10.5, color: C.muted, lineHeight: 1.6 }}>
                    資料為 Binance 公開端點，與現貨雷達的量價結論獨立。兩者一致時訊號較可靠，衝突時以量價結構為主。
                  </div>
                </div>
              );
            })()}

            {/* ── 送模擬單 ── */}
            {(() => {
              const dv0 = deriv[detail.id];
              const dvOk = dv0 && !dv0.none ? dv0 : null;
              // 預設方向跟著分頁，但衍生品明確指向反向時，改推薦反向
              const suggest = dvOk && dvOk.bias != null && Math.abs(dvOk.bias) >= 1
                ? (dvOk.bias > 0 ? "bull" : "bear") : null;
              const bear = tradeSide != null ? tradeSide === "bear" : side === "bear";
              const cfgT = trade.cfg || {};
              const [sPct, sDet] = stopPct(detail, bear, cfgT.stopMode || "tighter",
                cfgT.stopAtrMult ?? 1.5, cfgT.maxStopPct ?? 12, cfgT.minStopPct ?? 1.5);
              const stop = sPct != null && detail.price
                ? (bear ? detail.price * (1 + sPct / 100) : detail.price * (1 - sPct / 100)) : null;
              const ok = stop != null;
              const gate = tradeGate(detail, dvOk, bear);
              return (
                <div className="mt-3 rounded p-3" style={{ background: C.panel2, border: `1px solid ${C.line}` }}>
                  <div className="flex items-center gap-2 flex-wrap">
                    <span style={{ fontFamily: FONT.display, fontSize: 13 }}>模擬單</span>
                    <div className="flex rounded overflow-hidden" style={{ border: `1px solid ${C.line}` }}>
                      {[["bull", "做多"], ["bear", "做空"]].map(([v, t]) => (
                        <button key={v} onClick={() => setTradeSide(v)}
                          className="px-2.5 py-1"
                          style={{ background: (v === "bear") === bear ? (v === "bear" ? DOWN : UP) : "transparent",
                                   color: (v === "bear") === bear ? C.ink : C.muted, fontSize: 11.5 }}>
                          {t}
                        </button>
                      ))}
                    </div>
                    <span style={{ fontSize: 11, color: C.muted }}>
                      停損 {stop ? fmtPrice(stop) : "—"}
                      {sPct != null && <span>　{sPct.toFixed(1)}%（{sDet.used === "atr" ? "ATR" : "均線"}）</span>}
                    </span>
                  </div>
                  {suggest && (suggest === "bear") !== bear && (
                    <div className="mt-2 px-2.5 py-1.5 rounded" style={{ background: C.panel, border: `1px solid ${C.gold}`, fontSize: 11.5, color: C.gold, lineHeight: 1.7 }}>
                      合約結構其實指向{suggest === "bear" ? "空方" : "多方"}（{dvOk.quadrant}）。
                      <button onClick={() => setTradeSide(suggest)} className="ml-1 underline">切換為{suggest === "bear" ? "做空" : "做多"}</button>
                    </div>
                  )}

                  {gate.blocks.length > 0 && (
                    <div className="mt-2 px-2.5 py-2 rounded" style={{ background: "#241A1A", border: `1px solid ${C.red}`, fontSize: 11.5, color: C.red, lineHeight: 1.8 }}>
                      不建議進場：
                      {gate.blocks.map((b, i) => <div key={i}>· {b}</div>)}
                    </div>
                  )}
                  {gate.blocks.length === 0 && gate.warns.length > 0 && (
                    <div className="mt-2 px-2.5 py-2 rounded" style={{ background: C.panel, border: `1px solid ${C.gold}`, fontSize: 11.5, color: C.gold, lineHeight: 1.8 }}>
                      需留意：
                      {gate.warns.map((w, i) => <div key={i}>· {w}</div>)}
                    </div>
                  )}

                  {!ok ? (
                    <div className="mt-2" style={{ fontSize: 11.5, color: C.gold, lineHeight: 1.7 }}>
                      算不出合理的停損價（{bear ? "空頭計畫尚未成立" : "缺少均線或價格資料"}），不送單。
                      沒有停損的單子不該存在。
                    </div>
                  ) : (
                    <>
                      <button
                        onClick={async () => {
                          // 下單前先確認幣安有這檔永續合約且可交易
                          let chk = null;
                          try {
                            const r = await fetch(tradeBaseRef.current + "/api/trade/check?symbol=" + encodeURIComponent(detail.sym), { cache: "no-store" });
                            chk = await r.json();
                          } catch (e) { /* 交給下面統一報錯 */ }
                          if (!chk || !chk.ok) {
                            setTradeMsg(["err", chk?.message || "合約檢查失敗"]);
                            return;
                          }
                          const j = await tradeCall("open", {
                            symbol: detail.sym, side: bear ? "SHORT" : "LONG",
                            entry: detail.price, stop, stopPct: sPct,
                            note: `${bear ? "空頭" : "多頭"}雷達 ${detail.score == null ? "—" : r1(detail.score)} 分（手動）`,
                          });
                          if (j?.ok) {
                            setTradeMsg(["ok", `${j.symbol} 已送出：${j.qty} 單位，風險 ${j.sizing?.riskAmt?.toFixed(2)} U`]);
                            refreshTrade();
                          } else {
                            setTradeMsg(["err", j?.error || "下單失敗"]);
                          }
                        }}
                        disabled={tradeBusy || gate.blocks.length > 0}
                        className="mt-2 px-3 py-1.5 rounded"
                        style={{ background: gate.blocks.length > 0 ? C.panel : bear ? DOWN : UP,
                                 color: gate.blocks.length > 0 ? C.muted : C.ink, fontSize: 12,
                                 border: gate.blocks.length > 0 ? `1px solid ${C.line}` : "none" }}>
                        {tradeBusy ? "送出中…"
                          : gate.blocks.length > 0 ? "證據不足，已停用"
                          : `送模擬單（${bear ? "做空" : "做多"}）`}
                      </button>
                      {!tgAdmin && trade.adminRequired !== false && (
                        <div className="mt-1.5" style={{ fontSize: 11, color: C.gold, lineHeight: 1.6 }}>
                          尚未填管理金鑰。若伺服器有設 ADMIN_KEY，請先到「模擬單」分頁填入，否則會被拒絕。
                        </div>
                      )}
                      <div className="mt-2" style={{ fontSize: 10.5, color: C.muted, lineHeight: 1.6 }}>
                        送出前會先確認幣安有這檔的 USDT 永續合約、狀態可交易、且數量與名目金額符合下限。
                        部位由停損距離反推，每筆風險固定。
                      </div>
                    </>
                  )}
                  {tradeMsg && (
                    <div className="mt-2" style={{ fontSize: 11.5, color: tradeMsg[0] === "err" ? C.red : C.teal, lineHeight: 1.7 }}>
                      {tradeMsg[1]}
                    </div>
                  )}
                </div>
              );
            })()}

            {!detail.scanned ? (
              <div className="mt-3 px-3 py-3 rounded" style={{ background: C.panel2, fontSize: 12.5, lineHeight: 1.7 }}>
                這一檔還沒掃描過，所以沒有量能基準可以比較。
                <button onClick={() => scan([detail])} disabled={batch.running || source === "demo"}
                  className="ml-2 px-3 py-1 rounded" style={{ background: C.teal, color: C.ink, fontSize: 12 }}>
                  {batch.running ? "掃描中…" : "掃描這一檔"}
                </button>
              </div>
            ) : (
              <>
                <div className="mt-3 px-3 py-2.5 rounded" style={{ background: C.panel2, fontSize: 12.5, lineHeight: 1.75 }}>
                  <div style={{ color: C.gold, marginBottom: 4 }}>現在的情況</div>
                  {STAGES[detail.stage].say}
                </div>

                <div className="mt-3 grid gap-2">
                  {detail.reasons.map((x) => (
                    <div key={x.k} className="px-3 py-2 rounded" style={{ background: C.panel2 }}>
                      <div className="flex items-center gap-2">
                        <span style={{ fontSize: 12, width: 84 }}>{x.k}</span>
                        <div className="flex-1 h-1.5 rounded-sm" style={{ background: C.line }}>
                          <div className="h-1.5 rounded-sm" style={{ width: `${clamp(x.s, 0, 100)}%`, background: C.teal }} />
                        </div>
                        <span style={{ fontFamily: FONT.data, fontSize: 12, color: C.gold, minWidth: 52, textAlign: "right" }}>{x.v}</span>
                      </div>
                      <div style={{ fontSize: 12, color: C.bone, marginTop: 5, lineHeight: 1.7 }}>{x.t}</div>
                    </div>
                  ))}
                </div>

                <div className="mt-3 px-3 py-2.5 rounded" style={{ background: "#241A1A", border: `1px solid ${C.line}` }}>
                  <div style={{ color: C.red, fontSize: 12, marginBottom: 5 }}>風險提示</div>
                  <div className="flex gap-3 mb-2" style={{ fontSize: 11.5 }}>
                    <span>追高 <RiskTag r={detail.riskChase} /></span>
                    <span>暴跌 <RiskTag r={detail.riskCrash} /></span>
                    <span>流動性 <RiskTag r={detail.riskLiq} /></span>
                  </div>
                  {detail.riskNotes.map((t, i) => (
                    <div key={i} style={{ fontSize: 12, lineHeight: 1.7, color: C.bone }}>· {t}</div>
                  ))}
                </div>

                <div className="mt-3 px-3 py-2.5 rounded" style={{ background: C.panel2 }}>
                  <div style={{ fontSize: 12, color: C.muted, marginBottom: 6 }}>90 日區間位置</div>
                  <RangeBar low={detail.l90} high={detail.h90} price={detail.price} w={200} />
                  <div className="mt-2 grid grid-cols-2 gap-2" style={{ fontFamily: FONT.data, fontSize: 12 }}>
                    <div><span style={{ color: C.muted }}>90 日高 </span>{fmtPrice(detail.h90)}</div>
                    <div><span style={{ color: C.muted }}>90 日低 </span>{fmtPrice(detail.l90)}</div>
                    <div><span style={{ color: C.muted }}>平日均量 </span>{fmtUsd(detail.base7)}</div>
                    <div><span style={{ color: C.muted }}>今日成交 </span>{fmtUsd(detail.vol)}</div>
                    <div><span style={{ color: C.muted }}>年化波動 </span>{detail.vola.toFixed(0)}%</div>
                    <div><span style={{ color: C.muted }}>量能百分位 </span>{detail.volPct.toFixed(0)}</div>
                  </div>
                </div>
              </>
            )}



            {detail.scanned && detail.bladeState && detail.bladeState !== "none" && (
              <div className="mt-3 rounded p-3" style={{ background: C.panel2, border: `1px solid ${BLADE[detail.bladeState].color}` }}>
                <div className="flex flex-wrap items-center gap-2 mb-2">
                  <span style={{ fontFamily: FONT.display, fontSize: 15 }}>均線三刀流</span>
                  <span style={{ fontSize: 10.5, color: C.muted }}>60 分 K</span>
                  <BladeTag state={detail.bladeState} tangle={detail.tangle} />
                </div>
                <div style={{ fontSize: 12.5, lineHeight: 1.8, marginBottom: 8 }}>{BLADE[detail.bladeState].say}</div>

                <div className="grid gap-1.5 mb-2">
                  {[["小橘 240MA", detail.ma240, detail.dOrange, detail.slope240, "劉備 · 決定方向", "#E08A3C"],
                    ["小綠 60MA", detail.ma60, detail.dGreen, detail.slope60, "關羽 · 負責進出", C.green],
                    ["小藍 20MA", detail.ma20, detail.dBlue, detail.slope20, "張飛 · 負責收尾", C.teal]].map(([lab, v, d, sl, role, col]) => (
                    <div key={lab} className="flex items-center gap-2" style={{ fontSize: 12 }}>
                      <span style={{ color: col, width: 82 }}>{lab}</span>
                      <span style={{ fontFamily: FONT.data, width: 82 }}>{fmtPrice(v)}</span>
                      <span style={{ fontFamily: FONT.data, color: d > 0 ? UP : DOWN, width: 62 }}>{fmtPct(d)}</span>
                      <span style={{ fontFamily: FONT.data, fontSize: 11, color: sl > 0 ? UP : sl < 0 ? DOWN : C.muted, width: 68 }}>
                        斜率 {sl > 0 ? "正" : sl < 0 ? "負" : "平"}
                      </span>
                      <span style={{ fontSize: 10.5, color: C.muted }}>{role}</span>
                    </div>
                  ))}
                </div>

                {detail.tangle && (
                  <div style={{ fontSize: 11.5, color: C.gold, lineHeight: 1.7, marginBottom: 6 }}>
                    小綠與小橘距離不到 0.6%，均線糾結。這種盤三刀流會反覆假訊號，建議等均線拉開再操作。
                  </div>
                )}

                {detail.bladePlan && (
                  <div className="rounded p-2.5" style={{ background: C.panel, fontSize: 12, lineHeight: 1.9 }}>
                    <div><span style={{ color: C.gold }}>建議動作　</span>{detail.bladePlan.side}　{detail.bladePlan.action}</div>
                    <div><span style={{ color: C.teal }}>進場參考　</span>{detail.bladePlan.entry}</div>
                    <div><span style={{ color: C.gold }}>出場條件　</span>{detail.bladePlan.exit}</div>
                    <div><span style={{ color: C.red }}>停損參考　</span>{detail.bladePlan.stop}</div>
                    {detail.bladePlan.note && (
                      <div style={{ color: C.muted, marginTop: 4 }}>{detail.bladePlan.note}</div>
                    )}
                  </div>
                )}
              </div>
            )}
            {detail.scanned && detail.bearStage !== "none" && detail.bearStage !== "unknown" && (
              <div className="mt-3 rounded p-3" style={{ background: "#1B1416", border: `1px solid ${BEAR_STAGES[detail.bearStage].color}` }}>
                <div className="flex items-center gap-2 mb-2">
                  <BearTag stage={detail.bearStage} />
                  <span style={{ fontSize: 12, color: C.muted }}>空頭評分</span>
                  <span style={{ fontFamily: FONT.data, fontSize: 18, color: C.red }}>{detail.bear.toFixed(0)}</span>
                </div>
                <div style={{ fontSize: 12.5, lineHeight: 1.75, marginBottom: 8 }}>{BEAR_STAGES[detail.bearStage].say}</div>

                <div className="grid grid-cols-2 gap-2 mb-2" style={{ fontFamily: FONT.data, fontSize: 12 }}>
                  <div><span style={{ color: C.muted }}>波段高 </span>{fmtPrice(detail.swingHigh)}</div>
                  <div><span style={{ color: C.muted }}>波段低 </span>{fmtPrice(detail.swingLow)}</div>
                  <div><span style={{ color: C.muted }}>EMA20 </span>{fmtPrice(detail.ema20)}</div>
                  <div><span style={{ color: C.muted }}>EMA50 </span>{fmtPrice(detail.ema50)}</div>
                  <div><span style={{ color: C.muted }}>最近壓力 </span>{fmtPrice(detail.resistance)}</div>
                  <div><span style={{ color: C.muted }}>前低支撐 </span>{fmtPrice(detail.support)}{detail.brokeSupport ? <span style={{ color: C.red }}> 已跌破</span> : null}</div>
                  <div><span style={{ color: C.muted }}>RSI14 </span>{detail.rsi.toFixed(0)}</div>
                  <div><span style={{ color: C.muted }}>ATR14 </span>{detail.atr ? fmtPrice(detail.atr) : "—"}</div>
                </div>

                {detail.entryLo && (
                  <div className="px-2.5 py-2 rounded mb-2" style={{ background: C.panel2, fontSize: 12, lineHeight: 1.8 }}>
                    <div><span style={{ color: C.teal }}>參考入場區　</span>
                      <span style={{ fontFamily: FONT.data }}>{fmtPrice(detail.entryLo)} – {fmtPrice(detail.entryHi)}</span>
                      <span style={{ color: C.muted }}>　{detail.bearStage === "rebound" ? "下跌段 0.5–0.618 回撤帶" : "回抽 EMA20 或前支撐"}{detail.emaInZone && detail.bearStage === "rebound" ? "，且與 EMA20 重疊" : ""}</span>
                    </div>
                    <div><span style={{ color: C.red }}>參考止損　　</span>
                      <span style={{ fontFamily: FONT.data }}>{fmtPrice(detail.stop)}</span>
                      <span style={{ color: C.muted }}>　距入場中值 {detail.stopPct.toFixed(1)}%，已含 0.5 倍 ATR 緩衝</span>
                    </div>
                    <div><span style={{ color: C.muted }}>第一目標　　</span>
                      <span style={{ fontFamily: FONT.data }}>{fmtPrice(detail.target)}</span>
                      <span style={{ color: C.muted }}>　前低位置{detail.rr ? `，報酬風險比約 ${detail.rr.toFixed(2)}` : ""}</span>
                    </div>
                  </div>
                )}

                <div className="grid gap-2">
                  {detail.bearReasons.map((x) => (
                    <div key={x.k} className="px-2.5 py-2 rounded" style={{ background: C.panel2 }}>
                      <div className="flex items-center gap-2">
                        <span style={{ fontSize: 12, width: 88 }}>{x.k}</span>
                        <div className="flex-1 h-1.5 rounded-sm" style={{ background: C.line }}>
                          <div className="h-1.5 rounded-sm" style={{ width: `${clamp(x.s, 0, 100)}%`, background: C.red }} />
                        </div>
                        <span style={{ fontFamily: FONT.data, fontSize: 12, color: C.gold, minWidth: 70, textAlign: "right" }}>{x.v}</span>
                      </div>
                      <div style={{ fontSize: 12, color: C.bone, marginTop: 5, lineHeight: 1.7 }}>{x.t}</div>
                    </div>
                  ))}
                </div>

                <div className="mt-2 px-2.5 py-2 rounded" style={{ background: "#2A1618" }}>
                  <div className="flex items-center gap-3 mb-1.5" style={{ fontSize: 11.5 }}>
                    <span style={{ color: C.red }}>風險提示</span>
                    <span>追空 <RiskTag r={detail.chaseShort} /></span>
                    <span>流動性 <RiskTag r={detail.riskLiq} /></span>
                  </div>
                  {detail.bearWarn.map((t, i) => (
                    <div key={i} style={{ fontSize: 12, lineHeight: 1.7 }}>· {t}</div>
                  ))}
                </div>
              </div>
            )}
            {detail.scanned && detail.bearStage === "none" && side === "bear" && (
              <div className="mt-3 px-3 py-2.5 rounded" style={{ background: C.panel2, fontSize: 12.5, lineHeight: 1.75 }}>
                這一檔的空頭結構不成立：{detail.distEma >= 0 ? "價格還在 EMA20 之上" : "跌幅或反彈位置不符合條件"}，
                現在做空等於在猜頭部，勝率不會好。
              </div>
            )}

            <div className="mt-3 grid grid-cols-3 gap-2" style={{ fontSize: 12 }}>
              {[["1 小時", detail.m1], ["7 日", detail.m7], ["30 日", detail.m30], ["1 年", detail.m365], ["RS vs BTC", detail.rs], ["距 ATH", detail.ath]].map(([k, v]) => (
                <div key={k} className="px-2 py-1.5 rounded" style={{ background: C.panel2 }}>
                  <div style={{ color: C.muted, fontSize: 10.5 }}>{k}</div>
                  <div style={{ fontFamily: FONT.data, color: tone(v) }}>{fmtPct(v)}</div>
                </div>
              ))}
            </div>

            <div className="mt-3">
              <div style={{ fontSize: 12, color: C.muted, marginBottom: 4 }}>7 日走勢</div>
              <Sparkline data={detail.spark} color={(detail.m7 ?? 0) >= 0 ? UP : DOWN} w={320} h={70} />
            </div>

            <a href={`https://www.coingecko.com/en/coins/${detail.id}`} target="_blank" rel="noreferrer"
              className="inline-block mt-3" style={{ color: C.gold, fontSize: 12 }}>在 CoinGecko 查看完整資料 →</a>
          </div>
        </div>
  );
}
