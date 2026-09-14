import React from "react";
import { C } from "../theme.js";
import { store } from "../util.js";

/* 由 App.jsx 抽出。共用狀態透過 s 傳入，避免逐一列 props。 */
export default function ConnPanel({ s }) {
  const { apiKey, detectProxy, diag, diagAbort, diagBusy, loadDemo, loadMarkets, loadRef, local, pages, pro, proxy, proxyForce, proxyMode, runDiag, setApiKey, setDiagBusy, setPro, setProxy, setProxyForce, setProxyMode } = s;
  return (
<div className="w-full rounded p-3 grid gap-2.5" style={{ background: C.panel, border: `1px solid ${C.line}`, fontSize: 12 }}>
            <div className="flex flex-wrap items-center gap-2">
              <input value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="CoinGecko API Key（免費 Demo Key，30 次/分）"
                className="px-2 py-1.5 rounded flex-1" style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.bone, minWidth: 240 }} />
              <label className="flex items-center gap-1.5 cursor-pointer">
                <input type="checkbox" checked={pro} onChange={(e) => setPro(e.target.checked)} style={{ accentColor: C.gold }} />Pro 金鑰
              </label>
              <span className="px-2 py-1.5 rounded" style={{ fontSize: 11.5, color: local ? C.teal : C.muted, border: `1px solid ${local ? C.teal : C.line}` }}>
                {proxyMode === "detecting" ? "偵測連線方式…" : local ? "已連上伺服器代理" : "直連 CoinGecko"}
              </span>
              <button onClick={() => detectProxy({ ignoreForce: true })} className="px-2.5 py-1.5 rounded"
                style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.muted, fontSize: 12 }}>重新偵測</button>
              <label className="flex items-center gap-1.5 cursor-pointer" style={{ fontSize: 12, color: proxyForce ? C.teal : C.bone }}>
                <input type="checkbox" checked={proxyForce} style={{ accentColor: C.teal }}
                  onChange={(e) => {
                    setProxyForce(e.target.checked);
                    store.set("proxyForce", e.target.checked);
                    if (e.target.checked) { setProxyMode("server"); store.set("proxyMode", "server"); }
                    else detectProxy({ ignoreForce: true });
                    store.set("proxyTried", false);
                    setTimeout(() => loadRef.current(pages), 200);
                  }} />
                強制走伺服器
              </label>
              <button onClick={runDiag} disabled={diagBusy} className="px-3 py-1.5 rounded"
                style={{ background: diagBusy ? C.panel2 : C.teal, color: diagBusy ? C.muted : C.ink }}>
                {diagBusy ? "檢測中…" : "執行連線診斷"}
              </button>
            </div>
            <input value={proxy} onChange={(e) => setProxy(e.target.value)} placeholder="代理前綴（選填，例如自架 Cloudflare Worker）"
              className="px-2 py-1.5 rounded" style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.bone }} />
            {diag.length > 0 && (
              <div className="grid gap-1">
                {diag.map((d, i) => (
                  <div key={i} style={{ color: d.ok === true ? C.teal : d.ok === false ? C.red : d.ok === "busy" ? C.gold : C.muted, lineHeight: 1.6 }}>
                    {d.ok === true ? "✓ " : d.ok === false ? "✕ " : d.ok === "busy" ? "⋯ " : "· "}{d.t}
                  </div>
                ))}
                {diagBusy && (
                  <button onClick={() => { diagAbort.current = true; setDiagBusy(false); }}
                    className="px-2.5 py-1 rounded self-start"
                    style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.muted, fontSize: 11.5 }}>
                    中止檢測
                  </button>
                )}
              </div>
            )}
            <div className="flex flex-wrap gap-2">
              <button onClick={() => loadMarkets()} className="px-3 py-1.5 rounded" style={{ background: C.panel2, border: `1px solid ${C.gold}`, color: C.gold }}>重新嘗試即時資料</button>
              <button onClick={loadDemo} className="px-3 py-1.5 rounded" style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.muted }}>載入示範資料</button>
            </div>
          </div>
  );
}
