import ConnPanel from "./pages/ConnPanel.jsx";
import RadarPage from "./pages/RadarPage.jsx";
import WatchPage from "./pages/WatchPage.jsx";
import ChainPage from "./pages/ChainPage.jsx";
import TradePage from "./pages/TradePage.jsx";
import SignalsPage from "./pages/SignalsPage.jsx";
import AlertsPage from "./pages/AlertsPage.jsx";
import ChainDetail from "./pages/ChainDetail.jsx";
import DetailModal from "./pages/DetailModal.jsx";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DEFAULT_ALERT, EVENT, alertText, evaluateSignals } from "./alerts.js";
import { cgFetch, errText, fetchDeriv, gate, lastStale, sendNotify } from "./api.js";
import { BLADE } from "./blades.js";
import { BUCKETS, NETWORKS, bucketOf, chainFetch, makeChainDemo, parsePool, pctNum, securityScore } from "./chain.js";
import { ChainCard, CoinCard, WatchCard, useNarrow } from "./components/Cards.jsx";
import { BearMap, BearTag, BladeTag, RadarMap, RangeBar, RiskTag, RvolBar, ScoreCell, Sparkline, StageTag, Stat } from "./components/Small.jsx";
import { BEAR_STAGES, METRICS, PRESETS, PRESET_DIRS, QUICK_BEAR, QUICK_BULL, STABLE, STAGES, WRAPPED, ZERO_D, ZERO_W } from "./constants.js";
import { makeDemo } from "./demo.js";
import { analyze, extractScan } from "./engine.js";
import { C, FONT } from "./theme.js";
import { derivScoreAdjust, stopPct, tradeGate } from "./trade.js";
import { clamp, fmtPct, fmtPrice, fmtUsd, pctRank, r1, sleep, store } from "./util.js";

function CryptoScreener() {
  const narrow = useNarrow();
  const [raw, setRaw] = useState([]);
  const [scans, setScans] = useState(() => store.get("scans", {}));
  const [demoScans, setDemoScans] = useState(null);
  const [saveState, setSaveState] = useState(store.ok ? "ok" : "nostore");
  const [scanTTL, setScanTTL] = useState(() => store.get("scanTTL", 12));
  const [bgScan, setBgScan] = useState(() => store.get("bgScan", true));
  useEffect(() => { store.set("bgScan", bgScan); }, [bgScan]);
  const [serverTs, setServerTs] = useState({});          // 伺服器各檔深度資料的時間戳
  const [loading, setLoading] = useState(false);
  const [loadSec, setLoadSec] = useState(0);
  const [staleAge, setStaleAge] = useState(null);
  const [srvRefresh, setSrvRefresh] = useState(null);   // 伺服器滾動補抓狀態

  const [error, setError] = useState(null);
  const [updated, setUpdated] = useState(null);
  const [source, setSource] = useState(null);
  const [pages, setPages] = useState(1);
  const [apiKey, setApiKey] = useState("");
  const [pro, setPro] = useState(false);
  const [proxy, setProxy] = useState("");
  /* 同源是否有代理伺服器，開頁時自動探測一次，使用者不必自己勾 */
  const [proxyMode, setProxyMode] = useState(() => store.get("proxyMode", "detecting"));
  const [proxyForce, setProxyForce] = useState(() => store.get("proxyForce", false));
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [showConn, setShowConn] = useState(false);
  const [showWeights, setShowWeights] = useState(false);
  const [diag, setDiag] = useState([]);

  const [twColor, setTwColor] = useState(true);
  const [weights, setWeights] = useState({ ...ZERO_W, ...PRESETS["量能優先"] });
  const [dirs, setDirs] = useState({ ...ZERO_D });
  const [activePreset, setActivePreset] = useState("量能優先");

  const [q, setQ] = useState("");
  const [minMcap, setMinMcap] = useState(1e7);
  const [minVol, setMinVol] = useState(1e6);
  const [noStable, setNoStable] = useState(true);
  const [noWrapped, setNoWrapped] = useState(true);
  const [minLiq, setMinLiq] = useState(0);
  const [quick, setQuick] = useState(null);
  const [side, setSide] = useState("bull");
  const [page, setPage] = useState("radar");
  const [watch, setWatch] = useState(() => store.get("watch", []));
  const [alertCfg, setAlertCfg] = useState(() => ({ ...DEFAULT_ALERT, ...store.get("alertCfg", {}) }));
  const [history, setHistory] = useState(() => store.get("history", []));
  const [sigStates, setSigStates] = useState(() => store.get("sigStates", {}));
  const [autoScan, setAutoScan] = useState(false);
  const [scanEvery, setScanEvery] = useState(15);
  const [notifyLog, setNotifyLog] = useState([]);
  const [tg, setTg] = useState(null);            // 伺服器端的 Telegram 狀態
  const [tgToken, setTgToken] = useState("");
  const [tgAdmin, setTgAdmin] = useState(() => store.get("adminKey", ""));
  const [tgMsg, setTgMsg] = useState(null);
  const [pair, setPair] = useState(null);        // {code, link, state, left}
  const [mon, setMon] = useState(null);          // 伺服器背景監控狀態
  const [monMsg, setMonMsg] = useState(null);
  const [monScope, setMonScope] = useState(() => store.get("monScope", "watch"));
  const [monTopN, setMonTopN] = useState(() => store.get("monTopN", 100));
  const [srvHistory, setSrvHistory] = useState([]);
  const [lastEval, setLastEval] = useState(null);

  const [sortKey, setSortKey] = useState("radar");
  const [sortDir, setSortDir] = useState(-1);
  const [detail, setDetail] = useState(null);
  const [deriv, setDeriv] = useState({});          // id → 衍生品分析結果
  const [derivBusy, setDerivBusy] = useState(false);
  const [tradeSide, setTradeSide] = useState(null);   // null = 跟著分頁
  const [autoEdit, setAutoEdit] = useState(false);
  const [tradesOpen, setTradesOpen] = useState(false);   // 交易明細預設收折
  const [liveCheck, setLiveCheck] = useState(null);       // 上線就緒清單（在模擬單頁直接查）
  const [targetEdit, setTargetEdit] = useState(false);
  const [liveBusy, setLiveBusy] = useState(false);
  const [tradesLimit, setTradesLimit] = useState(10);
  const [view, setView] = useState("radar");

  const [batch, setBatch] = useState({ running: false, done: 0, total: 0, now: "" });
  const cancelRef = useRef(false);
  const [batchN, setBatchN] = useState(30);

  const rawRef = useRef([]);
  useEffect(() => { rawRef.current = raw; }, [raw]);
  useEffect(() => { store.set("watch", watch); }, [watch]);
  useEffect(() => { store.set("scanTTL", scanTTL); }, [scanTTL]);
  useEffect(() => { store.set("adminKey", tgAdmin); }, [tgAdmin]);
  useEffect(() => { store.set("monScope", monScope); }, [monScope]);
  useEffect(() => { store.set("monTopN", monTopN); }, [monTopN]);
  const saveTimer = useRef(null);
  const scansRef = useRef(scans);
  const srcRef = useRef(source);
  useEffect(() => { scansRef.current = scans; srcRef.current = source; });

  const flushScans = useCallback(() => {
    if (srcRef.current === "demo") return;          // 示範資料不落地
    const r = store.setScans(scansRef.current);
    setSaveState(r);
  }, []);

  useEffect(() => {                                  // 節流寫入，避免每掃一檔就序列化整包
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(flushScans, 1200);
    return () => clearTimeout(saveTimer.current);
  }, [scans, flushScans]);

  /* 關閉分頁、切到背景、重新整理之前先立刻存檔，
     不然掃到一半離開就會少掉最後那幾檔 */
  useEffect(() => {
    const h = () => { if (document.visibilityState === "hidden") flushScans(); };
    document.addEventListener("visibilitychange", h);
    window.addEventListener("pagehide", flushScans);
    return () => {
      document.removeEventListener("visibilitychange", h);
      window.removeEventListener("pagehide", flushScans);
      flushScans();
    };
  }, [flushScans]);
  useEffect(() => { store.set("alertCfg", alertCfg); }, [alertCfg]);
  useEffect(() => { store.set("history", history.slice(0, 400)); }, [history]);
  useEffect(() => { store.set("sigStates", sigStates); }, [sigStates]);

  /* 開啟個股詳情時才抓衍生品資料：多數幣沒有永續合約，
     全表掃描會白白浪費請求。結果快取 5 分鐘。 */
  useEffect(() => {
    if (!detail) return;
    setTradeSide(null);              // 換一檔就重設方向
    const id = detail.id;
    const hit = deriv[id];
    if (hit && Date.now() - (hit.updated || 0) < 300000) return;
    let dead = false;
    setDerivBusy(true);
    fetchDeriv({ symbol: detail.sym, price_change_percentage_24h: detail.m24 }, cfgRef.current)
      .then((d) => { if (!dead) setDeriv((m) => ({ ...m, [id]: d || { none: true, updated: Date.now() } })); })
      .catch(() => { if (!dead) setDeriv((m) => ({ ...m, [id]: { none: true, updated: Date.now() } })); })
      .finally(() => { if (!dead) setDerivBusy(false); });
    return () => { dead = true; };
  }, [detail]);

  const toggleWatch = useCallback((id) => {
    setWatch((w) => (w.includes(id) ? w.filter((x) => x !== id) : [...w, id]));
  }, []);
  const local = proxyMode === "server";
  const loadRef = useRef(null);
  useEffect(() => {
    if (!local) return;              // 伺服器滾動補抓狀態，每分鐘讀一次
    let dead = false;
    const poll = async () => {
      try {
        const r = await fetch("/api/health", { cache: "no-store" });
        const j = await r.json();
        if (!dead && j.refresh) setSrvRefresh(j.refresh);
      } catch (e) { /* 伺服器不在就不顯示 */ }
    };
    poll();
    const t = setInterval(poll, 60000);
    return () => { dead = true; clearInterval(t); };
  }, [local]);

  useEffect(() => {
    if (!local) { setServerTs({}); return; }
    let dead = false;
    const poll = async () => {
      try {
        const r = await fetch("/api/deep/ts", { cache: "no-store" });
        const j = await r.json();
        if (!dead && j && typeof j === "object") setServerTs(j);
      } catch (e) { /* 忽略 */ }
    };
    poll();
    const t = setInterval(poll, 120000);
    return () => { dead = true; clearInterval(t); };
  }, [local]);          // 供 loadMarkets 內部自我重試使用
  const detectRef = useRef(null);        // 同上：detectProxy 定義在後面，透過 ref 取用
  const cfg = useMemo(() => ({ key: apiKey.trim(), pro, proxy: proxy.trim(), local }), [apiKey, pro, proxy, local]);
  const cfgRef = useRef(cfg);
  cfgRef.current = cfg;

  const UP = twColor ? C.red : C.green;
  const DOWN = twColor ? C.green : C.red;
  const tone = (v) => (v == null ? C.muted : v > 0 ? UP : v < 0 ? DOWN : C.bone);

  /* 示範資料的掃描結果單獨存放。它用的是真實幣種 id，
     若寫進 scans 會把使用者實際掃出來的快取整包蓋掉。 */
  const loadDemo = useCallback(() => {
    const { coins, scans: s } = makeDemo();
    setRaw(coins); setDemoScans(s); setSource("demo"); setUpdated(new Date());
  }, []);

  const loadMarkets = useCallback(async (p = pages) => {
    setLoading(true); setError(null);
    try {
      const all = [];
      for (let i = 1; i <= p; i++) {
        const d = await cgFetch(
          `/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=${i}` +
          `&sparkline=true&price_change_percentage=1h,24h,7d,30d,1y`, cfg);
        if (!Array.isArray(d)) throw new Error("BADDATA");
        all.push(...d);
      }
      setRaw(all); setSource("live"); setUpdated(new Date());
    } catch (e) {
      // 直連被網路環境擋掉時，先試試同源代理再放棄
      if (e.message === "BLOCKED" && !cfg.local && !store.get("proxyTried", false)) {
        store.set("proxyTried", true);
        const m = detectRef.current ? await detectRef.current({ ignoreForce: true }) : "direct";
        if (m === "server") {
          setError("直連被這個網路擋下，已自動改走伺服器代理，正在重新載入…");
          setTimeout(() => loadRef.current(p), 300);
          setLoading(false);
          return;
        }
      }
      setError(errText(e));
      if (!rawRef.current.length) loadDemo();
    } finally { setLoading(false); setStaleAge(lastStale.age); }
  }, [pages, cfg, loadDemo]);

  loadRef.current = loadMarkets;

  const detectProxy = useCallback(async (opts) => {
    if (store.get("proxyForce", false) && !(opts && opts.ignoreForce)) {
      setProxyMode("server");                       // 使用者手動指定，不再自動判定
      return "server";
    }
    let mode = "direct";
    try {
      if (location.protocol.startsWith("http")) {
        // 健康檢查不依賴上游，伺服器就算連不到 CoinGecko 也會回 200
        const r = await fetch("/api/health", { cache: "no-store" });
        if (r.ok && r.headers.get("X-Screener-Server")) mode = "server";
        else {
          const r2 = await fetch("/api/v3/ping", { cache: "no-store" });
          if (r2.ok && r2.headers.get("X-Cache")) mode = "server";
        }
      }
    } catch (e) { /* 兩個都失敗才視為沒有代理 */ }
    setProxyMode(mode);
    store.set("proxyMode", mode);
    return mode;
  }, []);
  detectRef.current = detectProxy;

  useEffect(() => {
    let dead = false;
    (async () => {
      await detectProxy();
      if (!dead) loadRef.current(1);
    })();
    return () => { dead = true; };
    /* eslint-disable-next-line */
  }, []);
  useEffect(() => {
    if (!loading) { setLoadSec(0); return; }
    const t0 = Date.now();
    const t = setInterval(() => setLoadSec(Math.floor((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(t);
  }, [loading]);

  useEffect(() => {
    if (!autoRefresh) return;
    const t = setInterval(() => { if (!loading && !batch.running) loadMarkets(); }, 90000);
    return () => clearInterval(t);
  }, [autoRefresh, loading, batch.running, loadMarkets]);

  const [diagBusy, setDiagBusy] = useState(false);
  const diagAbort = useRef(false);

  const runDiag = useCallback(async () => {
    diagAbort.current = false;
    setDiagBusy(true); setShowConn(true);
    const out = [];
    const push = (t, ok) => { out.push({ t, ok }); setDiag([...out]); };
    const step = (t) => setDiag([...out, { t, ok: "busy" }]);

    /* 每個步驟都有超時，避免節流佇列或重試把畫面卡住 */
    const withTimeout = (p, ms, label) => Promise.race([
      p,
      new Promise((_, rej) => setTimeout(() => rej(new Error("TIMEOUT:" + label)), ms)),
    ]);

    push(cfg.local ? "連線路徑：同源代理 /api/v3" : "連線路徑：瀏覽器直連 api.coingecko.com", null);

    /* 1. 網頁伺服器本身 */
    if (cfg.local) {
      step("檢查伺服器是否在線…");
      try {
        const t0 = Date.now();
        const r = await withTimeout(fetch("/api/health?probe=1", { cache: "no-store" }), 20000, "health");
        const j = await r.json();
        if (j.dataSource && j.dataSource !== "direct")
          push(`資料來源：CoinGecko 請求轉發至 ${j.dataSource}（共用它的快取與額度）`, null);
        if (j.keyless) push("目前為無金鑰降級模式：Demo 月額度已用盡，改走公開端點。每 6 小時會自動試著切回金鑰。", null);
        push(`伺服器在線（${Date.now() - t0}ms）　金鑰${j.hasKey ? `已設定（${j.keyLen} 字元${j.pro ? " · Pro" : ""}）` : "未設定"}　`
          + `記憶體快取 ${j.cached} 筆　磁碟快取 ${j.disk ?? "?"} 筆　已運行 ${Math.floor((j.uptime || 0) / 60)} 分`, true);

        if (j.egressIp) push(`伺服器出口 IP：${j.egressIp}　← 填入幣安 API 金鑰的 IP 白名單，金鑰外洩也無法從別處使用`, null);

        /* 上線就緒度：模擬網階段就能看，不必真的切換 */
        if (Array.isArray(j.liveChecklist)) {
          const done = j.liveChecklist.filter((x) => x.ok).length;
          push(`${j.live ? "正式網運作中" : "模擬網"}　接入正式網的準備 ${done}/${j.liveChecklist.length}（流程見專案內 GO_LIVE.md）`,
               j.live ? true : null);
          j.liveChecklist.forEach((x) => push(`　${x.ok ? "✓" : "○"} ${x.item}`, x.ok ? true : null));
        }

        /* 伺服器自己連往上游的結果：這一步能直接分辨是誰連不上 */
        (j.probe || []).forEach((p) => {
          if (p.ok) push(`伺服器 → ${p.name}：通過（HTTP ${p.code}，${p.ms}ms）`, true);
          else {
            const hint = p.code === 403 && /allowlist|egress/i.test(p.detail || "")
              ? "Zeabur 的網路出口限制擋掉了這個網域，需要在服務設定裡放行。"
              : /10006|calls limit/i.test(p.detail || "")
                ? "每月 10,000 次總額度已用盡，等一分鐘沒有用，要等下個月重置或升級方案。"
              : p.code === 429 ? "每分鐘速率上限，等一分鐘後會自行恢復。"
              : p.code === 401 || p.code === 403 ? "金鑰被拒絕，確認 CG_API_KEY 是否正確、有沒有多餘空白。"
              : p.code === 0 ? "連線層失敗（DNS 或逾時），伺服器出不了網。"
              : `HTTP ${p.code}`;
            push(`伺服器 → ${p.name}：失敗（${p.ms}ms）　${hint}`, false);
            if (p.detail) push(`　　上游回應：${String(p.detail).slice(0, 160)}`, null);
          }
        });
        if ((j.probe || []).some((p) => p.name === "CoinGecko" && !p.ok)) {
          push("結論：問題在伺服器連往 CoinGecko 這一段，不是你的手機網路。後續步驟略過。", null);
          setDiagBusy(false); return;
        }
      } catch (e) {
        push(String(e.message).startsWith("TIMEOUT")
          ? "伺服器沒有回應（8 秒逾時）。Zeabur 可能正在重新部署或休眠，稍後再試。"
          : "連不到伺服器。你的網路可能連這個網址也擋掉了，或服務已停止。", false);
        setDiagBusy(false); return;
      }
    }
    if (diagAbort.current) { setDiagBusy(false); return; }

    /* 2. 伺服器（或瀏覽器）能不能連到 CoinGecko */
    step(cfg.local ? "測試伺服器連往 CoinGecko…（額度滿時會等待重試，最多 40 秒）"
      : "測試瀏覽器連往 CoinGecko…（最多 40 秒）");
    try {
      const t0 = Date.now();
      await withTimeout(cgFetch("/ping", cfg), 40000, "ping");
      push(`CoinGecko 可連線（${Date.now() - t0}ms）`, true);
    } catch (e) {
      const to = String(e.message).startsWith("TIMEOUT");
      push(to ? (cfg.local
          ? "40 秒內沒有回應。多半是免費額度用盡後在排隊重試，填入 API Key 可大幅改善。"
          : "40 秒內沒有回應，可能被網路攔截或額度用盡。")
        : "CoinGecko 連線失敗 — " + errText(e), false);
      setDiagBusy(false); return;
    }
    if (diagAbort.current) { setDiagBusy(false); return; }

    /* 3. 行情端點 */
    step("測試行情端點…");
    try {
      const t0 = Date.now();
      const d = await withTimeout(cgFetch("/coins/markets?vs_currency=usd&per_page=1&page=1", cfg), 40000, "markets");
      push(`行情端點正常（取回 ${d.length} 筆，${Date.now() - t0}ms）`, true);
    } catch (e) {
      push(String(e.message).startsWith("TIMEOUT") ? "行情端點 40 秒內沒有回應，多半是額度排隊。"
        : "行情端點失敗 — " + errText(e), false);
    }

    push(cfg.key ? (cfg.pro ? "金鑰：Pro" : "金鑰：Demo，每分鐘 30 次")
      : cfg.local ? "金鑰：瀏覽器未填。若已在 Zeabur 設定 CG_API_KEY，走代理時仍會生效。"
      : "金鑰：未填，每分鐘僅 5–15 次", cfg.key ? true : null);
    setDiagBusy(false);
  }, [cfg]);

  


/* 進場前的證據檢查：按鈕方向來自分頁，但方向對不對要看證據。
   回傳 { blocks, warns }。blocks 非空就不該送單。 */

/* ── 雷達掃描 ── */
  const scan = useCallback(async (list) => {
    if (source === "demo") return;
    cancelRef.current = false;
    setBatch({ running: true, done: 0, total: list.length, now: "" });
    for (let i = 0; i < list.length; i++) {
      if (cancelRef.current) break;
      const c = list[i];
      setBatch((b) => ({ ...b, now: c.sym }));
      try {
        const chart = await cgFetch(`/coins/${c.id}/market_chart?vs_currency=usd&days=90`, cfg);
        setScans((prev) => ({ ...prev, [c.id]: extractScan(chart, c.vol) }));
      } catch (e) {
        if (e.message === "RATE" || e.message === "BLOCKED" || e.message === "NOPROXY") { setError(errText(e)); break; }
        setScans((prev) => ({ ...prev, [c.id]: { err: true, errWhy: errText(e).slice(0, 40), ts: Date.now() } }));
      }
      setBatch((b) => ({ ...b, done: i + 1 }));
    }
    setBatch({ running: false, done: 0, total: 0, now: "" });
  }, [cfg, source]);

  /* ── 衍生資料 ── */
  const btc30 = useMemo(() => raw.find((c) => c.id === "bitcoin")?.price_change_percentage_30d_in_currency ?? 0, [raw]);

  const base = useMemo(() => raw.map((c) => {
    const m30 = c.price_change_percentage_30d_in_currency ?? null;
    return {
      id: c.id, sym: (c.symbol || "").toUpperCase(), name: c.name, img: c.image,
      rank: c.market_cap_rank, price: c.current_price,
      m1: c.price_change_percentage_1h_in_currency ?? null,
      m24: c.price_change_percentage_24h_in_currency ?? null,
      m7: c.price_change_percentage_7d_in_currency ?? null,
      m30, m365: c.price_change_percentage_1y_in_currency ?? null,
      rs: m30 == null ? null : m30 - btc30,
      vol: c.total_volume ?? 0, mcap: c.market_cap ?? 0,
      turn: c.market_cap ? (c.total_volume / c.market_cap) * 100 : null,
      ath: c.ath_change_percentage ?? null,
      spark: c.sparkline_in_7d?.price || null,
    };
  }), [raw, btc30]);

  /* 預篩：只用行情端點就有的欄位排序深掃佇列，把額度先花在最可能有異動的幣上 */
  const preScored = useMemo(() => {
    if (!base.length) return base;
    const t = pctRank(base.map((r) => r.turn));
    const v = pctRank(base.map((r) => r.vol));
    const m = pctRank(base.map((r) => Math.abs(r.m24 ?? 0)));
    const w = pctRank(base.map((r) => Math.abs(r.m7 ?? 0)));
    return base.map((r, i) => ({ ...r, pre: 0.4 * t[i] + 0.2 * v[i] + 0.25 * m[i] + 0.15 * w[i] }));
  }, [base]);

  const activeScans = source === "demo" ? (demoScans || {}) : scans;
  const rows = useMemo(() => {
    const cut = Date.now() - scanTTL * 3600000;
    const followServer = local && Object.keys(serverTs).length > 0;
    return preScored.map((r) => {
      const o = analyze(r, activeScans[r.id]);
      if (followServer) {
        // 伺服器模式：只有當伺服器的資料比我上次計算更新時，才需要重算。
        // 網頁自己的 12 小時計時器在這裡沒有意義——資料沒變，重算也是同樣結果。
        const sts = serverTs[r.id];
        o.stale = o.scanTs != null && sts != null && sts > o.scanTs + 60000;
      } else {
        o.stale = o.scanTs != null && o.scanTs < cut;
      }
      return o;
    });
  }, [preScored, activeScans, scanTTL, local, serverTs]);




  /* ── Telegram 配對 ── */
  const tgCall = useCallback(async (path, body) => {
    const r = await fetch(path, {
      method: body ? "POST" : "GET",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    return r.json();
  }, []);

  const tgRefresh = useCallback(async () => {
    try { setTg(await tgCall("/api/tg/status")); } catch (e) { setTg(null); }
  }, [tgCall]);

  useEffect(() => { if (local) tgRefresh(); }, [local, tgRefresh]);

  const tgSetup = useCallback(async () => {
    setTgMsg(null);
    const j = await tgCall("/api/tg/setup", { token: tgToken.trim(), admin: tgAdmin.trim() });
    if (j.ok) { setTgToken(""); setTgMsg(["ok", `已連上機器人 @${j.bot}`]); tgRefresh(); }
    else setTgMsg(["err", j.error === "invalid_token" ? "這組 Token 無效，請確認是從 @BotFather 複製的完整字串"
      : j.error === "admin_key_required" ? "需要管理金鑰才能修改" : "設定失敗：" + (j.detail || j.error)]);
  }, [tgToken, tgAdmin, tgCall, tgRefresh]);

  const startPair = useCallback(async () => {
    setTgMsg(null);
    const j = await tgCall("/api/tg/pair", {});
    if (j.error) { setTgMsg(["err", "請先設定 Bot Token"]); return; }
    setPair({ ...j, state: "waiting" });
  }, [tgCall]);

  useEffect(() => {
    if (!pair || pair.state !== "waiting") return;
    const t = setInterval(async () => {
      try {
        const j = await tgCall("/api/tg/pair/status", { code: pair.code });
        if (j.state === "paired") {
          setPair({ ...pair, state: "paired", chat: j.chat });
          tgRefresh();
        } else if (j.state === "expired") setPair({ ...pair, state: "expired" });
        else setPair((p) => (p ? { ...p, left: j.left } : p));
      } catch (e) { /* 忽略單次失敗 */ }
    }, 3000);
    return () => clearInterval(t);
  }, [pair, tgCall, tgRefresh]);

  /* ── 伺服器背景監控 ── */
  const monRefresh = useCallback(async () => {
    try { setMon(await tgCall("/api/monitor/status")); } catch (e) { setMon(null); }
  }, [tgCall]);
  const monHistory = useCallback(async () => {
    try { const j = await tgCall("/api/monitor/history"); setSrvHistory(j.history || []); }
    catch (e) { setSrvHistory([]); }
  }, [tgCall]);

  useEffect(() => {
    if (!local) return;
    monRefresh(); monHistory();
    const t = setInterval(() => { monRefresh(); monHistory(); }, 60000);
    return () => clearInterval(t);
  }, [local, monRefresh, monHistory]);

  const monSync = useCallback(async (on) => {
    setMonMsg(null);
    const j = await tgCall("/api/monitor/config", {
      watch, scope: monScope, topN: monTopN,
      cfg: { bull: alertCfg.bull, bear: alertCfg.bear, cooldownMin: alertCfg.cooldownMin,
        minDelta: alertCfg.minDelta, breakoutPct: alertCfg.breakoutPct },
      on, admin: tgAdmin.trim(),
    });
    if (j.ok) {
      setMonMsg(["ok", on
        ? `已同步並啟動，監控範圍：${j.scope === "top" ? `市值前 ${j.topN} 檔` : `觀察清單 ${j.watch} 檔`}`
        : "已停止背景監控"]);
      monRefresh();
    }
    else setMonMsg(["err", j.error === "admin_key_required"
      ? "管理金鑰不正確或未填。請在下方欄位輸入 Zeabur 環境變數 ADMIN_KEY 的值。"
      : "同步失敗：" + j.error]);
  }, [watch, alertCfg, tgAdmin, tgCall, monRefresh, monScope, monTopN]);

  /* ── 訊號評估：資料一更新就跑，狀態機負責去重 ── */
  const engineRef = useRef({});
  engineRef.current = { rows, alertCfg, sigStates, watch, local };

  const runEngine = useCallback(async (manual) => {
    const { rows, alertCfg, sigStates, watch } = engineRef.current;
    if (!alertCfg.on && !manual) return;
    const pool = alertCfg.scope === "watch" ? rows.filter((r) => watch.includes(r.id)) : rows.filter((r) => r.scanned);
    if (!pool.length) { setLastEval({ ts: Date.now(), n: 0, fired: 0 }); return; }
    const { events, next } = evaluateSignals(pool, alertCfg, sigStates, Date.now());
    setSigStates(next);
    setLastEval({ ts: Date.now(), n: pool.length, fired: events.length });
    if (!events.length) return;
    setHistory((h) => [...events, ...h].slice(0, 400));
    for (const e of events) {
      const res = await sendNotify({ ...alertCfg.ch, useServer: engineRef.current.local },
        { ...alertText(e), event: e, row: pool.find((r) => r.id === e.id) || null });
      setNotifyLog((l) => [{ ts: Date.now(), sym: e.sym, type: e.type, res }, ...l].slice(0, 20));
    }
  }, []);

  useEffect(() => { if (alertCfg.on) runEngine(false); }, [rows, alertCfg.on, runEngine]);

  /* ── 追蹤每則提醒之後的表現，用來回頭檢驗有效性 ── */
  useEffect(() => {
    if (!history.length || !base.length) return;
    const now = Date.now();
    const price = Object.fromEntries(base.map((r) => [r.id, r.price]));
    let changed = false;
    const upd = history.map((e) => {
      if (e.verdict !== "open" || !price[e.id]) return e;
      if (now - e.ts > 72 * 3600000) { changed = true; return { ...e, verdict: e.maxUp >= 5 ? "win" : "expired" }; }
      const ret = (price[e.id] / e.price - 1) * 100;
      const fav = e.side === "bull" ? ret : -ret;          // 對該方向有利的幅度
      const adv = e.side === "bull" ? -ret : ret;
      const maxUp = Math.max(e.maxUp, fav), maxDown = Math.max(e.maxDown, adv);
      let verdict = "open", hitTs = e.hitTs;
      if (fav >= 5 && !e.hitTs) { verdict = "win"; hitTs = now; }
      else if (adv >= 5) { verdict = "lose"; hitTs = now; }
      if (maxUp !== e.maxUp || maxDown !== e.maxDown || verdict !== "open") changed = true;
      return { ...e, maxUp, maxDown, verdict: verdict === "open" ? e.verdict : verdict, hitTs, last: ret };
    });
    if (changed) setHistory(upd);
  }, [base]);   // eslint-disable-line

  /* ── 自動掃描觀察清單 ── */
  const autoRef = useRef({});
  autoRef.current = { rows, watch, loading, running: batch.running, source, scan };
  useEffect(() => {
    if (!autoScan) return;
    const t = setInterval(() => {
      const a = autoRef.current;
      if (a.loading || a.running || a.source === "demo") return;
      const list = a.rows.filter((r) => a.watch.includes(r.id));
      if (list.length) a.scan(list);
    }, scanEvery * 60000);
    return () => clearInterval(t);
  }, [autoScan, scanEvery]);


  /* ── 鏈上雷達 ── */
  const [net, setNet] = useState("eth");
  const [poolMode, setPoolMode] = useState("new");
  const [chainTokens, setChainTokens] = useState([]);
  const [chainBusy, setChainBusy] = useState(null);
  const [chainErr, setChainErr] = useState(null);
  const [chainAt, setChainAt] = useState(null);
  const [minSafe, setMinSafe] = useState(60);
  const [holderHist, setHolderHist] = useState(() => store.get("holderHist", {}));
  const [smartRaw, setSmartRaw] = useState(() => store.get("smart", ""));
  const [chainDetail, setChainDetail] = useState(null);
  const [trade, setTrade] = useState({ positions: [], perf: { count: 0 } });
  /* 交易頁可以指向不同的服務（正式網／模擬網各一個部署）。
     每個目標有自己的網址與管理金鑰；預設只有「本站」。 */
  const [tradeTargets, setTradeTargets] = useState(() => store.get("tradeTargets", [{ label: "本站", url: "", key: "" }]));
  const [tradeIdx, setTradeIdx] = useState(() => store.get("tradeIdx", 0));
  useEffect(() => { store.set("tradeTargets", tradeTargets); }, [tradeTargets]);
  useEffect(() => { store.set("tradeIdx", tradeIdx); }, [tradeIdx]);
  const target = tradeTargets[tradeIdx] || tradeTargets[0];
  const tradeBase = (target.url || "").replace(/\/$/, "");
  const adminRef = useRef("");
  adminRef.current = tradeBase ? (target.key || "") : tgAdmin;   // 本站沿用提醒設定的金鑰
  const tradeBaseRef = useRef("");
  tradeBaseRef.current = tradeBase;
  const [tradeMsg, setTradeMsg] = useState(null);
  const [tradeBusy, setTradeBusy] = useState(false);

  const tradeCall = useCallback(async (ep, payload) => {
    setTradeBusy(true);
    try {
      const body = payload ? { ...payload, adminKey: adminRef.current } : null;
      const r = await fetch(tradeBaseRef.current + "/api/trade/" + ep, body
        ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
        : { cache: "no-store" });
      const j = await r.json();
      if (j && j.error === "admin_key_required") {
        setTradeMsg(["err", adminRef.current
          ? "管理金鑰不正確，與伺服器的 ADMIN_KEY 環境變數不符。"
          : "這個動作需要管理金鑰。到「模擬單」分頁最上方填入伺服器設定的 ADMIN_KEY。"]);
      }
      return j;
    } catch (e) {
      setTradeMsg(["err", "連不到伺服器：" + e.message]);
      return null;
    } finally { setTradeBusy(false); }
  }, []);

  const refreshTrade = useCallback(async () => {
    const j = await tradeCall("status");
    if (j && !j.error) setTrade(j);
    else if (j && j.error) setTradeMsg(["err", "模擬單模組未載入，確認 trader.py 有一起部署"]);
  }, [tradeCall]);

  useEffect(() => {
    if (page !== "trade") return;
    setLiveCheck(null);
    refreshTrade();
    // 持倉損益要跟得上行情，這頁開著時每 15 秒更新一次
    const t = setInterval(refreshTrade, 15000);
    return () => clearInterval(t);
  }, [page, refreshTrade, tradeIdx]);
  const [deepN, setDeepN] = useState(8);

  useEffect(() => { store.set("holderHist", holderHist); }, [holderHist]);
  useEffect(() => { store.set("smart", smartRaw); }, [smartRaw]);
  const smartSet = useMemo(() =>
    new Set(smartRaw.split(/[\s,;]+/).map((x) => x.trim().toLowerCase()).filter((x) => x.length > 20)), [smartRaw]);

  const scanChain = useCallback(async () => {
    setChainErr(null); setChainBusy("讀取池子清單…");
    const netCfg = NETWORKS.find((n) => n.id === net);
    try {
      const j = await chainFetch("gt",
        `/networks/${net}/${poolMode === "new" ? "new_pools" : "trending_pools"}?include=base_token&page=1`, cfg);
      const tokens = {};
      (j.included || []).forEach((x) => { tokens[x.id] = x.attributes || {}; });
      let list = (j.data || []).map((d) => parsePool(d, tokens)).filter((p) => p.addr && p.liq > 1000);
      if (!list.length) { setChainErr("這條鏈目前沒有符合條件的池子。"); setChainBusy(null); return; }

      /* 合約安全：GoPlus 支援一次查多個地址，20 個一批 */
      const isSol = net === "solana";
      const secMap = {};
      for (let i = 0; i < list.length; i += 20) {
        const batch = list.slice(i, i + 20);
        setChainBusy(`合約安全檢查 ${Math.min(i + 20, list.length)}/${list.length}…`);
        try {
          const path = isSol
            ? `/solana/token_security?contract_addresses=${batch.map((b) => b.addr).join(",")}`
            : `/token_security/${netCfg.gp}?contract_addresses=${batch.map((b) => b.addr).join(",")}`;
          const g = await chainFetch("gp", path, cfg);
          Object.entries(g.result || {}).forEach(([k, v]) => { secMap[k.toLowerCase()] = v; });
        } catch (e) { /* 這批查不到就留空，之後標記為無法驗證 */ }
      }

      const now = Date.now();
      const hist = { ...holderHist };
      list = list.map((p) => {
        const g = secMap[p.addr];
        const s = securityScore(g, isSol);
        const key = net + ":" + p.addr;
        if (s.holders) {
          const arr = (hist[key] || []).filter((x) => now - x[0] < 30 * 86400000);
          if (!arr.length || now - arr[arr.length - 1][0] > 20 * 60000) arr.push([now, s.holders]);
          hist[key] = arr.slice(-60);
        }
        const arr = hist[key] || [];
        const first = arr[0];
        const growth = first && arr.length > 1 && first[1] ? ((s.holders - first[1]) / first[1]) * 100 : null;
        return { ...p, sec: s.score, flags: s.flags, top10: s.top10, top1: s.top1,
          holders: s.holders || null, lockedPct: s.lockedPct, holderGrowth: growth,
          histLen: arr.length, histFrom: first ? first[0] : null, raw: g || null };
      });
      setHolderHist(hist);

      /* 深度分析：抓最近成交，算大額買入與聰明錢 */
      const deep = [...list].sort((a, b) => b.heat - a.heat).slice(0, deepN);
      for (let i = 0; i < deep.length; i++) {
        setChainBusy(`大額交易分析 ${i + 1}/${deep.length}…`);
        try {
          const tr = await chainFetch("gt", `/networks/${net}/pools/${deep[i].pool}/trades?trade_volume_in_usd_greater_than=500`, cfg);
          const rows = (tr.data || []).map((x) => x.attributes || {});
          const buys = rows.filter((x) => x.kind === "buy");
          const sells = rows.filter((x) => x.kind === "sell");
          const sum = (a) => a.reduce((s, x) => s + parseFloat(x.volume_in_usd || 0), 0);
          const big = buys.filter((x) => parseFloat(x.volume_in_usd || 0) >= 5000);
          const smart = rows.filter((x) => smartSet.has((x.tx_from_address || "").toLowerCase()));
          deep[i].bigBuys = big.length;
          deep[i].bigBuyUsd = sum(big);
          deep[i].netUsd = sum(buys) - sum(sells);
          deep[i].uniqBuyers = new Set(buys.map((x) => x.tx_from_address)).size;
          deep[i].smartBuys = smart.filter((x) => x.kind === "buy").length;
          deep[i].smartSells = smart.filter((x) => x.kind === "sell").length;
          deep[i].deep = true;
        } catch (e) { /* 略過 */ }
      }
      const byPool = Object.fromEntries(deep.map((d) => [d.pool, d]));
      setChainTokens(list.map((p) => byPool[p.pool] || p));
      setChainAt(new Date());
    } catch (e) {
      setChainErr(e.message === "BLOCKED"
        ? "瀏覽器擋下了對 GeckoTerminal 或 GoPlus 的請求。若你有部署伺服器版本，按連線設定裡的「重新偵測」讓它改走同源代理。"
        : e.message === "RATE" ? "鏈上資料來源額度已滿，等一分鐘再試。"
        : `鏈上資料讀取失敗（${e.message}）。`);
    } finally { setChainBusy(null); }
  }, [net, poolMode, cfg, deepN, holderHist, smartSet]);

  const chainBuckets = useMemo(() => {
    const g = { opportunity: [], watchlist: [], risk: [], unverified: [] };
    chainTokens.forEach((t) => g[bucketOf(t, minSafe)].push(t));
    Object.values(g).forEach((a) => a.sort((x, y) => y.heat - x.heat));
    return g;
  }, [chainTokens, minSafe]);

  /* 網頁與伺服器的紀錄合併呈現，同一檔同一秒視為同一則 */
  const allHistory = useMemo(() => {
    const seen = new Set(history.map((e) => e.id + ":" + e.side + ":" + Math.round(e.ts / 60000)));
    const extra = srvHistory.filter((e) => !seen.has(e.id + ":" + e.side + ":" + Math.round(e.ts / 60000)));
    return [...history, ...extra].sort((a, b) => b.ts - a.ts);
  }, [history, srvHistory]);

  const stats = useMemo(() => {
    const n = history.length;
    const win = history.filter((e) => e.verdict === "win").length;
    const lose = history.filter((e) => e.verdict === "lose").length;
    const open = history.filter((e) => e.verdict === "open").length;
    return { n, win, lose, open, rate: win + lose ? (win / (win + lose)) * 100 : null };
  }, [history]);

  const universe = useMemo(() => {
    const kw = q.trim().toLowerCase();
    const qk = quick ? [...QUICK_BULL, ...QUICK_BEAR].find((x) => x.id === quick) : null;
    return rows.filter((r) => {
      if (noStable && STABLE.has(r.sym.toLowerCase())) return false;
      if (noWrapped && WRAPPED.has(r.sym.toLowerCase())) return false;
      if (minMcap && r.mcap < minMcap) return false;
      if (minVol && r.vol < minVol) return false;
      if (minLiq && (r.liq == null || r.liq < minLiq)) return false;
      if (kw && !(r.sym.toLowerCase().includes(kw) || r.name.toLowerCase().includes(kw) || r.id.includes(kw))) return false;
      if (qk && !(r.scanned && qk.test(r))) return false;
      return true;
    });
  }, [rows, q, minMcap, minVol, minLiq, noStable, noWrapped, quick]);

  const scored = useMemo(() => {
    if (!universe.length) return [];
    const maps = {};
    METRICS.forEach((m) => { maps[m.key] = pctRank(universe.map((r) => r[m.key])); });
    return universe.map((r, i) => {
      let num = 0, den = 0; const parts = {};
      METRICS.forEach((m) => {
        const w = weights[m.key]; if (!w) return;
        const p = maps[m.key][i]; if (p == null) return;
        const v = dirs[m.key] === -1 ? 100 - p : p;
        parts[m.key] = v; num += w * v; den += w;
      });
      return { ...r, score: den ? num / den : null, parts };
    });
  }, [universe, weights, dirs]);

  const sorted = useMemo(() => {
    const arr = [...scored];
    arr.sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      return (av - bv) * sortDir;
    });
    return arr;
  }, [scored, sortKey, sortDir]);

  const scannedCount = useMemo(() => Object.values(activeScans).filter((s) => s && !s.err).length, [activeScans]);

  /* 待掃佇列：先未掃、再過期，兩者都依預篩分數排序 */
  const ERR_RETRY_MS = 6 * 3600000;    // 失敗的幣 6 小時後才再試（新幣要等歷史累積）
  const queue = useMemo(() => {
    const now = Date.now();
    const recentlyFailed = (r) => r.scanErr && r.scanErrAt && now - r.scanErrAt < ERR_RETRY_MS;
    const un = universe.filter((r) => !r.scanned && !recentlyFailed(r)).sort((a, b) => (b.pre ?? 0) - (a.pre ?? 0));
    const st = universe.filter((r) => r.scanned && r.stale).sort((a, b) => (b.pre ?? 0) - (a.pre ?? 0));
    return [...un, ...st];
  }, [universe]);

  const coverage = useMemo(() => {
    const n = universe.length || 1;
    // 拆成三種狀態：新鮮（保鮮期內）、過期（掃過但超過保鮮期）、從未掃描
    const fresh = universe.filter((r) => r.scanned && !r.stale).length;
    const stale = universe.filter((r) => r.scanned && r.stale).length;
    const failed = universe.filter((r) => r.scanErr).length;
    const never = universe.filter((r) => !r.scanned && !r.scanErr).length;
    const gap = cfg.local ? 0.25 : cfg.key ? 0.9 : 2.4;   // 秒
    return { done: fresh, fresh, stale, never, failed, n, pct: (fresh / n) * 100,
             todo: queue.length, etaMin: (queue.length * gap) / 60 };
  }, [universe, queue, cfg]);

  /* 背景漸進掃描：一次一檔，關掉分頁也會停，重開後從斷點續掃 */
  const bgRef = useRef({});
  bgRef.current = { queue, running: batch.running, source, scan, loading };
  useEffect(() => {
    if (!bgScan) return;
    let stop = false;
    (async () => {
      while (!stop) {
        const b = bgRef.current;
        if (b.source === "demo") break;
        if (!b.running && !b.loading && b.queue.length) {
          await b.scan(b.queue.slice(0, 1));
        } else {
          await sleep(3000);
        }
        await sleep(200);
      }
    })();
    return () => { stop = true; };
  }, [bgScan]);

  const kpi = useMemo(() => {
    if (!universe.length) return null;
    const sc = universe.filter((r) => r.scanned);
    const surge = sc.filter((r) => r.rvol7 >= 2).length;
    const up = universe.filter((r) => (r.m24 ?? 0) > 0).length;
    const igniting = sc.filter((r) => r.stage === "ignite" || r.stage === "accel").length;
    const hot = sc.filter((r) => r.stage === "hot" || r.riskChase >= 70).length;
    const dropping = sc.filter((r) => r.bearStage === "drop").length;
    const waiting = sc.filter((r) => r.bearStage === "rebound").length;
    const oversold = sc.filter((r) => r.chaseShort >= 65).length;
    return { n: universe.length, surge, upRatio: (up / universe.length) * 100, igniting, hot, scanned: sc.length, dropping, waiting, oversold };
  }, [universe]);

  /* ── 一鍵篩選 ── */
  const applyQuick = (qk) => {
    if (quick === qk.id) { setQuick(null); return; }
    setQuick(qk.id);
    setSortKey(qk.sort); setSortDir(-1);
    if (qk.id === "smallcap") { setMinMcap(0); setMinVol(1e6); }
  };

  const setPreset = (name) => {
    setWeights({ ...ZERO_W, ...PRESETS[name] });
    setDirs({ ...ZERO_D, ...PRESET_DIRS[name] });
    setActivePreset(name);
  };
  const sortBy = (k) => {
    if (sortKey === k) setSortDir((d) => -d);
    else { setSortKey(k); setSortDir(-1); }
  };

  const COLS_RADAR = [
    { k: "star", t: "★" }, { k: "rank", t: "#" }, { k: "name", t: "幣種" }, { k: "price", t: "價格" },
    { k: "m24", t: "24h" }, { k: "rvol7", t: "量能倍數" }, { k: "turn", t: "額/市值" },
    { k: "pcTag", t: "價量" }, { k: "stage", t: "階段" }, { k: "bladeState", t: "三刀流" }, { k: "d90", t: "90 日位置" },
    { k: "liq", t: "流動性" }, { k: "riskMax", t: "風險" }, { k: "radar", t: "雷達分數" },
    { k: "why", t: "白話解讀" },
  ];
  const COLS_FULL = [
    { k: "star", t: "★" }, { k: "rank", t: "#" }, { k: "name", t: "幣種" }, { k: "price", t: "價格" },
    { k: "m24", t: "24h" }, { k: "m7", t: "7d" }, { k: "m30", t: "30d" }, { k: "rs", t: "RS" },
    { k: "rvol7", t: "量能倍數" }, { k: "turn", t: "額/市值" }, { k: "vol", t: "成交量" },
    { k: "mcap", t: "市值" }, { k: "d90", t: "90 日位置" }, { k: "liq", t: "流動性" },
    { k: "bladeState", t: "三刀流" }, { k: "dGreen", t: "距小綠" },
    { k: "spark", t: "7 日" }, { k: "radar", t: "雷達分數" }, { k: "score", t: "加權分數" },
  ];
  const COLS_BEAR = [
    { k: "star", t: "★" }, { k: "rank", t: "#" }, { k: "name", t: "幣種" }, { k: "price", t: "價格" },
    { k: "m24", t: "24h" }, { k: "bearStage", t: "階段" }, { k: "bladeState", t: "三刀流" }, { k: "distEma", t: "距 EMA20" },
    { k: "drop", t: "最近跌幅" }, { k: "bounce", t: "反彈幅度" }, { k: "volRatio", t: "彈量/跌量" },
    { k: "resistance", t: "壓力位" }, { k: "entryMid", t: "入場區" }, { k: "stop", t: "止損" },
    { k: "rr", t: "報酬風險比" }, { k: "liq", t: "流動性" }, { k: "chaseShort", t: "追空風險" },
    { k: "bear", t: "空頭評分" }, { k: "bearWhy", t: "白話解讀" },
  ];
  const COLS = side === "bear" ? COLS_BEAR : view === "radar" ? COLS_RADAR : COLS_FULL;
  const noSort = new Set(["star", "name", "spark", "why", "pcTag", "stage", "bearStage", "bearWhy", "resistance", "bladeState"]);

  const cell = (r, k) => {
    switch (k) {
      case "star": return (
        <button onClick={(ev) => { ev.stopPropagation(); toggleWatch(r.id); }}
          style={{ color: watch.includes(r.id) ? C.gold : C.line, fontSize: 15, lineHeight: 1 }}
          title={watch.includes(r.id) ? "移出觀察清單" : "加入觀察清單"}>★</button>);
      case "rank": return <span style={{ fontFamily: FONT.data, color: C.muted }}>{r.rank ?? "—"}</span>;
      case "name": return (
        <div className="flex items-center gap-2">
          {r.img && <img src={r.img} alt="" width={16} height={16} style={{ borderRadius: 3 }} />}
          <span>{r.sym}</span><span style={{ color: C.muted, fontSize: 11 }}>{r.name.slice(0, 12)}</span>
        </div>);
      case "price": return <span style={{ fontFamily: FONT.data }}>{fmtPrice(r.price)}</span>;
      case "m24": case "m7": case "m30": case "rs":
        return <span style={{ fontFamily: FONT.data, color: tone(r[k]) }}>{fmtPct(r[k])}</span>;
      case "rvol7": return <RvolBar v={r.rvol7} />;
      case "turn": return <span style={{ fontFamily: FONT.data, color: (r.turn ?? 0) > 60 ? C.gold : C.bone }}>{r.turn == null ? "—" : r.turn.toFixed(1) + "%"}</span>;
      case "pcTag": return <span style={{ fontSize: 11.5, color: r.pcTag === "價漲量增" ? UP : r.pcTag === "價跌量增" ? DOWN : C.muted }}>{r.scanned ? r.pcTag : "—"}</span>;
      case "stage": return <StageTag stage={r.stage} />;
      case "bladeState": return <BladeTag state={r.bladeState || "none"} tangle={r.tangle} />;
      case "dGreen": return <span style={{ fontFamily: FONT.data, color: r.dGreen == null ? C.muted : r.dGreen > 0 ? UP : DOWN }}>{r.dGreen == null ? "—" : fmtPct(r.dGreen)}</span>;
      case "d90": return <RangeBar low={r.l90} high={r.h90} price={r.price} />;
      case "liq": return <ScoreCell score={r.liq} color={C.teal} />;
      case "riskMax": return <RiskTag r={r.riskMax} />;
      case "radar": return <ScoreCell score={r.radar} color={C.gold} />;
      case "score": return <ScoreCell score={r.score} color={C.violet} />;
      case "vol": case "mcap": return <span style={{ fontFamily: FONT.data }}>{fmtUsd(r[k])}</span>;
      case "spark": return <Sparkline data={r.spark} color={(r.m7 ?? 0) >= 0 ? UP : DOWN} />;
      case "bearStage": return <BearTag stage={r.bearStage} />;
      case "distEma": return <span style={{ fontFamily: FONT.data, color: r.distEma == null ? C.muted : r.distEma < 0 ? DOWN : C.bone }}>{r.distEma == null ? "—" : fmtPct(r.distEma)}</span>;
      case "drop": return <span style={{ fontFamily: FONT.data, color: DOWN }}>{r.drop == null ? "—" : fmtPct(r.drop)}</span>;
      case "bounce": return <span style={{ fontFamily: FONT.data, color: r.bounce > 1 ? UP : C.muted }}>{r.bounce == null ? "—" : fmtPct(r.bounce)}{r.retrace != null && r.bounce > 1 ? <span style={{ color: C.muted }}> ({(r.retrace * 100).toFixed(0)}%)</span> : null}</span>;
      case "volRatio": return <span style={{ fontFamily: FONT.data, color: r.volRatio == null ? C.muted : r.volRatio < 0.7 ? C.gold : C.bone }}>{r.volRatio == null ? "—" : r.volRatio.toFixed(2) + "x"}</span>;
      case "resistance": return <span style={{ fontFamily: FONT.data, color: C.muted }}>{r.resistance ? fmtPrice(r.resistance) : "—"}</span>;
      case "entryMid": return <span style={{ fontFamily: FONT.data, color: r.entryLo ? C.teal : C.muted }}>{r.entryLo ? `${fmtPrice(r.entryLo)}–${fmtPrice(r.entryHi)}` : "—"}</span>;
      case "stop": return <span style={{ fontFamily: FONT.data, color: r.stop ? C.red : C.muted }}>{r.stop ? fmtPrice(r.stop) : "—"}</span>;
      case "rr": return <span style={{ fontFamily: FONT.data, color: (r.rr ?? 0) >= 1.5 ? C.teal : C.bone }}>{r.rr ? r.rr.toFixed(2) : "—"}</span>;
      case "chaseShort": return <RiskTag r={r.chaseShort} />;
      case "bear": return <ScoreCell score={r.bear} color={C.red} />;
      case "bearWhy": return <span style={{ fontSize: 11.5, color: r.scanned ? C.bone : C.muted, whiteSpace: "nowrap" }}>{r.bearWhy}</span>;
      case "why": return <span style={{ fontSize: 11.5, color: r.scanned ? C.bone : C.muted, whiteSpace: "nowrap" }}>{r.why}</span>;
      default: return null;
    }
  };

  /* 分頁元件共用的狀態袋 */
  const S = { COLS, DOWN, UP, activePreset, alertCfg, allHistory, apiKey, applyQuick, autoEdit, autoRefresh, autoScan, batch, batchN, bgScan, cancelRef, cell, cfg, chainAt, chainBuckets, chainBusy, chainDetail, chainErr, chainTokens, coverage, deepN, deriv, derivBusy, detail, detectProxy, diag, diagAbort, diagBusy, dirs, history, kpi, liveBusy, liveCheck, loadDemo, loadMarkets, loadRef, loading, local, minLiq, minMcap, minSafe, minVol, mon, monHistory, monMsg, monRefresh, monScope, monSync, monTopN, narrow, net, noSort, noStable, noWrapped, notifyLog, pages, pair, poolMode, pro, proxy, proxyForce, proxyMode, q, queue, quick, refreshTrade, rows, runDiag, runEngine, saveState, scan, scanChain, scanEvery, scanTTL, scannedCount, serverTs, setActivePreset, setAlertCfg, setApiKey, setAutoEdit, setAutoScan, setBatchN, setBgScan, setChainAt, setChainDetail, setChainErr, setChainTokens, setDeepN, setDetail, setDiagBusy, setDirs, setHistory, setLiveBusy, setLiveCheck, setMinLiq, setMinMcap, setMinSafe, setMinVol, setMonMsg, setMonScope, setMonTopN, setNet, setNoStable, setNoWrapped, setNotifyLog, setPair, setPoolMode, setPreset, setPro, setProxy, setProxyForce, setProxyMode, setQ, setScanEvery, setScanTTL, setShowWeights, setSigStates, setSmartRaw, setSortDir, setSortKey, setTargetEdit, setTgAdmin, setTgToken, setTrade, setTradeIdx, setTradeMsg, setTradeSide, setTradeTargets, setTradesLimit, setTradesOpen, setView, setWeights, showWeights, side, sigStates, smartRaw, sortBy, sortDir, sortKey, sorted, source, srvHistory, srvRefresh, startPair, stats, target, targetEdit, tg, tgAdmin, tgCall, tgMsg, tgRefresh, tgSetup, tgToken, toggleWatch, tone, trade, tradeBaseRef, tradeBusy, tradeCall, tradeIdx, tradeMsg, tradeSide, tradeTargets, tradesLimit, tradesOpen, universe, view, watch, weights };

  return (
    <div style={{ background: C.ink, color: C.bone, fontFamily: FONT.body, minHeight: "100vh" }}>
      <header className="px-4 md:px-6 py-4 flex flex-wrap items-end gap-x-6 gap-y-3" style={{ borderBottom: `1px solid ${C.line}` }}>
        <div>
          <div style={{ fontSize: 10.5, letterSpacing: "0.28em", color: side === "bull" ? C.gold : C.red }}>
            {side === "bull" ? "VOLUME ANOMALY RADAR" : "BEARISH STRUCTURE RADAR"}
          </div>
          <h1 style={{ fontFamily: FONT.display, fontSize: 27, lineHeight: 1.15, marginTop: 2 }}>
            {side === "bull" ? "加密貨幣量能異動雷達" : "加密貨幣空頭結構雷達"}
          </h1>
        </div>
        <div className="flex rounded overflow-hidden self-center" style={{ border: `1px solid ${C.line}` }}>
          {[["bull", "多頭雷達"], ["bear", "空頭雷達"]].map(([v, t]) => (
            <button key={v} onClick={() => {
                setSide(v); setQuick(null);
                setSortKey(v === "bull" ? "radar" : "bear"); setSortDir(-1);
              }}
              style={{
                fontSize: 12.5, padding: "7px 14px",
                background: side === v ? (v === "bull" ? UP : DOWN) : C.panel,
                color: side === v ? C.ink : C.muted,
              }}>{t}</button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2 ml-auto" style={{ fontSize: 12 }}>
          <select value={pages} onChange={(e) => { const p = +e.target.value; setPages(p); loadMarkets(p); }}
            className="px-2 py-1.5 rounded" style={{ background: C.panel, border: `1px solid ${C.line}`, color: C.bone }}>
            <option value={1}>市值前 250</option><option value={2}>前 500</option>
            <option value={3}>前 750</option><option value={4}>前 1000</option>
          </select>
          <button onClick={() => loadMarkets()} disabled={loading} className="px-3 py-1.5 rounded"
            style={{ background: loading ? C.panel : C.gold, color: loading ? C.muted : C.ink, border: `1px solid ${C.gold}` }}>
            {loading ? `讀取中 ${loadSec}s` : "重新整理"}
          </button>
          <button onClick={() => setAutoRefresh((v) => !v)} className="px-2.5 py-1.5 rounded"
            style={{ background: autoRefresh ? C.panel2 : C.panel, border: `1px solid ${autoRefresh ? C.teal : C.line}`, color: autoRefresh ? C.teal : C.muted }}>
            自動更新
          </button>
          <button onClick={() => setTwColor((v) => !v)} className="px-2.5 py-1.5 rounded"
            style={{ background: C.panel, border: `1px solid ${C.line}`, color: C.muted }}>{twColor ? "紅漲綠跌" : "綠漲紅跌"}</button>
          <button onClick={() => setShowConn((v) => !v)} className="px-2.5 py-1.5 rounded"
            style={{ background: C.panel, border: `1px solid ${C.line}`, color: C.muted }}>連線設定</button>
        </div>
        <div className="w-full flex flex-wrap items-center gap-x-3 gap-y-1" style={{ fontSize: 11, color: C.muted, fontFamily: FONT.data }}>
          <span>更新 {updated ? updated.toLocaleTimeString("zh-TW") : "—"}</span>
          <span>· 收錄 {raw.length} · 篩選後 {universe.length} · 已掃描 {scannedCount}</span>
          <span style={{ color: source === "live" ? C.teal : C.gold }}>
            · {source === "live" ? "即時資料" : source === "demo" ? "示範資料（非真實行情）" : "尚未載入"}
          </span>
          <span style={{ color: C.muted }}>· {local ? "伺服器代理" : "瀏覽器直連"}</span>
          {staleAge != null && staleAge > 60 && (
            <span style={{ color: C.gold }}>
              · 行情為 {staleAge < 120 ? "約 1 分鐘" : Math.round(staleAge / 60) + " 分鐘"}前的快取，背景更新中
            </span>
          )}
        </div>

        {showConn && <ConnPanel s={S} />}
      </header>

      {error && (
        <div className="mx-4 md:mx-6 mt-4 px-3 py-2.5 rounded flex flex-wrap items-center gap-3"
          style={{ background: "#2A1618", border: `1px solid ${C.red}`, fontSize: 12.5, lineHeight: 1.6 }}>
          <span className="flex-1" style={{ minWidth: 240 }}>{error}</span>
          <button onClick={runDiag} className="px-2.5 py-1 rounded" style={{ background: C.panel2, border: `1px solid ${C.line}`, color: C.bone, fontSize: 12 }}>執行診斷</button>
          <button onClick={() => setError(null)} className="px-2.5 py-1 rounded" style={{ background: "transparent", color: C.muted, fontSize: 12 }}>關閉</button>
        </div>
      )}

      <div className="px-4 md:px-6 pt-3 flex flex-wrap gap-1.5" style={{ borderBottom: `1px solid ${C.line}` }}>
        {[["radar", "雷達"], ["watch", `觀察清單 ${watch.length ? "· " + watch.length : ""}`],
          ["chain", "鏈上雷達"],
          ["signals", `訊號紀錄 ${history.length ? "· " + history.length : ""}`],
          ["trade", `模擬單 ${trade.positions?.length ? "· " + trade.positions.length : ""}`],
          ["alerts", "提醒設定"]].map(([v, t]) => (
          <button key={v} onClick={() => setPage(v)} className="px-3 py-2 rounded-t"
            style={{
              fontSize: 12.5, background: page === v ? C.panel : "transparent",
              color: page === v ? C.bone : C.muted,
              borderTop: `1px solid ${page === v ? C.line : "transparent"}`,
              borderLeft: `1px solid ${page === v ? C.line : "transparent"}`,
              borderRight: `1px solid ${page === v ? C.line : "transparent"}`,
            }}>{t}</button>
        ))}
        {alertCfg.on && (
          <span className="ml-auto self-center px-2 py-1 rounded" style={{ fontSize: 11, color: C.teal, border: `1px solid ${C.teal}` }}>
            提醒監控中{lastEval ? ` · 上次檢查 ${new Date(lastEval.ts).toLocaleTimeString("zh-TW")}` : ""}
          </span>
        )}
      </div>

      <div className="px-4 md:px-6 py-4 grid gap-4">
        {page === "radar" && <RadarPage s={S} />}


        {/* ══ 觀察清單 ══ */}
        {page === "watch" && <WatchPage s={S} />}

        {/* ══ 鏈上異動雷達 ══ */}
        {page === "chain" && <ChainPage s={S} />}

        {/* ══ 訊號紀錄 ══ */}
        {page === "trade" && <TradePage s={S} />}

        {page === "signals" && <SignalsPage s={S} />}

        {/* ══ 提醒設定 ══ */}
        {page === "alerts" && <AlertsPage s={S} />}

        <p style={{ fontSize: 11, color: C.muted, lineHeight: 1.7 }}>
          量能倍數以該幣自己過去 7 日的平均 24 小時成交額為基準，且刻意排除最近 24 小時，避免今天的爆量墊高自己的基準。
          成交額與市值資料來自 CoinGecko 匯總的各交易所數據，部分小幣可能含有刷量成分，流動性評分與成交額市值比就是用來過濾這類標的。
          雷達分數是相對指標，只說明「現在有沒有異常資金動作」，不預測漲跌。
          空頭雷達的 EMA20、EMA50、RSI、ATR 與樞紐高低點，都是用逐時價格重建的日線資料計算；成交量採 CoinGecko 的 24 小時滾動量，
          與交易所的單日成交量定義略有差異，量能對比看的是相對變化而非絕對值。
          本工具所有內容僅供市場觀察與技術分析參考，不構成投資建議，也不保證任何價位會被觸及。
        </p>
      </div>

      {/* 細節面板 */}

      {chainDetail && <ChainDetail s={S} />}
      {detail && <DetailModal s={S} />}
    </div>
  );
}

export default CryptoScreener;
