import { blades } from "./blades.js";
import { BEAR_STAGES, STAGES } from "./constants.js";
import { atrLast, emaLast, pivots, rsiLast, toDaily } from "./ta.js";
import { clamp, fmtPct, fmtPrice, fmtUsd, fmtX, mean, stdev } from "./util.js";

function structure(bars) {
  if (bars.length < 35) return {};
  const c = bars.map((b) => b.c);
  const price = c[c.length - 1];
  const ema20 = emaLast(c, 20), ema50 = emaLast(c, 50);
  const rsi = rsiLast(c), atr = atrLast(bars);
  const { hi, lo } = pivots(bars, 3);

  const h2 = hi.slice(-2), l2 = lo.slice(-2);
  const lowerHigh = h2.length === 2 && bars[h2[1]].h < bars[h2[0]].h;
  const lowerLow = l2.length === 2 && bars[l2[1]].l < bars[l2[0]].l;

  /* 最近一段下跌：近 50 根內的最高點，以及其後的最低點 */
  const win = bars.slice(-50), off = bars.length - win.length;
  let hIdx = 0;
  win.forEach((b, i) => { if (b.h > win[hIdx].h) hIdx = i; });
  let lIdx = hIdx;
  for (let i = hIdx; i < win.length; i++) if (win[i].l < win[lIdx].l) lIdx = i;
  const swingHigh = win[hIdx].h, swingLow = win[lIdx].l;
  const range = swingHigh - swingLow;
  const drop = swingHigh > 0 ? (swingLow / swingHigh - 1) * 100 : 0;
  const bounce = swingLow > 0 ? (price / swingLow - 1) * 100 : 0;
  const retrace = range > 0 ? clamp((price - swingLow) / range, -0.2, 1.5) : 0;
  const fib = (r) => swingLow + r * range;

  /* 下跌段與反彈段的量能對比 */
  const downSeg = win.slice(hIdx, lIdx + 1), upSeg = win.slice(lIdx);
  const downVol = mean(downSeg.map((b) => b.v)) || 1;
  const bounceVol = upSeg.length > 1 ? mean(upSeg.slice(1).map((b) => b.v)) : null;
  const volRatio = bounceVol != null && downVol ? bounceVol / downVol : null;

  /* 反彈段陽線實體是否轉弱 */
  const ups = upSeg.filter((b) => b.c > b.o && b.h > b.l).map((b) => (b.c - b.o) / (b.h - b.l));
  const bodyFade = ups.length >= 4 ? mean(ups.slice(-2)) / (mean(ups.slice(0, 2)) || 1) : null;

  /* 支撐：下跌起點之前最後一個樞紐低點 */
  const prevLows = lo.filter((i) => i < off + hIdx);
  const support = prevLows.length ? bars[prevLows[prevLows.length - 1]].l
    : Math.min(...bars.slice(Math.max(0, bars.length - 60), Math.max(1, bars.length - 20)).map((b) => b.l));

  /* 壓力：現價之上最接近的一個 */
  const cands = [ema20, fib(0.5), fib(0.618), swingHigh, support].filter((v) => v && v > price * 1.002);
  const resistance = cands.length ? Math.min(...cands) : swingHigh;

  const recentVol = mean(bars.slice(-3).map((b) => b.v)) || 0;
  const baseVol = mean(bars.slice(-30, -3).map((b) => b.v)) || 1;

  let downDays = 0;
  for (let i = c.length - 1; i > 0 && c[i] < c[i - 1]; i--) downDays++;

  return {
    ema20, ema50, rsi, atr, lowerHigh, lowerLow,
    swingHigh, swingLow, drop, bounce, retrace,
    fib50: fib(0.5), fib618: fib(0.618), fib786: fib(0.786),
    downVol, bounceVol, volRatio, bodyFade,
    support, brokeSupport: price < support, resistance,
    volExpand: recentVol / baseVol, downDays,
    bounceHigh: Math.max(...upSeg.map((b) => b.h)),
  };
}

/* ════ 從 market_chart 萃取量能特徵 ══════════════════════ */
function extractScan(chart, curVol) {
  const P = (chart.prices || []).map((r) => r[1]).filter(isFinite);
  const V = (chart.total_volumes || []).map((r) => r[1]).filter(isFinite);
  // 歷史不足（多半是剛上市的新幣）：記下時間，讓掃描佇列一段時間內不要重試，
  // 否則它會永遠排在最前面，把後面所有幣卡住。
  if (P.length < 48 || V.length < 48) return { err: true, errWhy: "歷史不足 90 天", ts: Date.now() };
  const n = V.length, perDay = Math.max(1, Math.round(n / 90));
  const at = (arr, daysBack) => arr.slice(Math.max(0, arr.length - daysBack * perDay));
  const now = curVol != null && isFinite(curVol) ? curVol : V[n - 1];

  // 基準量刻意排除最近 24 小時，否則今天的爆量會把自己的基準墊高
  const prior = V.slice(0, Math.max(1, n - perDay));
  const base7 = mean(prior.slice(Math.max(0, prior.length - 7 * perDay))) || 1;
  const base30 = mean(prior.slice(Math.max(0, prior.length - 30 * perDay))) || 1;

  const daily = [];
  for (let i = perDay; i <= n; i += perDay) daily.push(V[i - 1]);
  const d30 = daily.slice(-30);
  const volCV = mean(d30) ? stdev(d30) / mean(d30) : 1;

  const sorted90 = [...V].sort((a, b) => a - b);
  const below = sorted90.filter((v) => v <= now).length;
  const volPct = (below / sorted90.length) * 100;

  const priorP = P.slice(0, Math.max(1, P.length - perDay));
  const hi20 = Math.max(...priorP.slice(Math.max(0, priorP.length - 20 * perDay)));
  const price = P[P.length - 1];

  const dailyP = [];
  for (let i = perDay; i <= P.length; i += perDay) dailyP.push(P[i - 1]);
  const rets = [];
  for (let i = 1; i < dailyP.length; i++) if (dailyP[i - 1] > 0) rets.push(dailyP[i] / dailyP[i - 1] - 1);
  const vola = stdev(rets.slice(-30)) * Math.sqrt(365) * 100;

  return {
    ...structure(toDaily(chart)),
    ...blades(chart),
    base7, base30, rvol7: now / base7, rvol30: now / base30,
    volPct, volCV,
    h90: Math.max(...P), l90: Math.min(...P),
    brk20: price >= hi20, vola,
    volTrend: (mean(at(V, 3)) || 1) / (mean(V.slice(Math.max(0, n - 10 * perDay), Math.max(1, n - 3 * perDay))) || 1),
    ts: Date.now(),
  };
}

/* ════ 雷達評分與白話解讀 ════════════════════════════════ */
function analyze(r, sc) {
  const o = { ...r, scanned: !!(sc && !sc.err) };
  if (!o.scanned) {
    const errAt = sc && sc.err ? (sc.ts || null) : null;
    const why = sc && sc.err ? `無法分析（${sc.errWhy || "資料取得失敗"}）` : "尚未掃描";
    return { ...o, scanErr: !!(sc && sc.err), scanErrAt: errAt, scanTs: null,
             stage: "unknown", bearStage: "unknown", bladeState: "none", radar: null, bear: null, liq: null,
             reasons: [], bearReasons: [], why, bearWhy: why };
  }

  Object.assign(o, sc);   // 量能與趨勢結構欄位一併帶進來
  /* 基準量來自歷史（變化慢、可快取），現量來自每 90 秒更新的行情端點。
     兩者相除即時算出，所以量能倍數不會停在上次深掃的瞬間。 */
  if (sc.base7 && o.vol) {
    o.rvol7 = o.vol / sc.base7;
    o.rvol30 = sc.base30 ? o.vol / sc.base30 : o.rvol30;
  }
  o.scanTs = sc.ts || null;
  o.d90 = o.h90 ? (o.price / o.h90 - 1) * 100 : null;
  o.lowUp90 = o.l90 ? (o.price / o.l90 - 1) * 100 : null;
  o.pos = o.h90 > o.l90 ? (o.price - o.l90) / (o.h90 - o.l90) : 0.5;

  /* 1. 量能倍數 */
  o.sRvol = clamp((Math.log(Math.max(o.rvol7, 0.2)) / Math.log(6)) * 110 + 12, 0, 100);

  /* 2. 成交額市值比：太低沒人玩，太高多半是洗量 */
  const t = o.turn ?? 0;
  let sTurn = clamp(((Math.log10(Math.max(t, 0.05)) - Math.log10(0.3)) / (Math.log10(25) - Math.log10(0.3))) * 100, 0, 100);
  if (t > 60) sTurn = Math.max(25, sTurn - (t - 60) * 0.6);
  o.sTurn = sTurn;

  /* 3. 價量確認 */
  let sPc = 50;
  sPc += o.rvol7 >= 1.5 ? 15 : o.rvol7 >= 1.2 ? 8 : o.rvol7 < 0.8 ? -10 : 0;
  sPc += clamp((o.m24 ?? 0) * 3, -25, 25);
  sPc += clamp((o.m7 ?? 0) * 0.6, -15, 15);
  sPc += o.brk20 ? 15 : 0;
  o.sPc = clamp(sPc, 0, 100);
  o.pcTag =
    o.rvol7 >= 1.4 && (o.m24 ?? 0) > 1.5 ? "價漲量增"
    : o.rvol7 >= 1.4 && (o.m24 ?? 0) < -1.5 ? "價跌量增"
    : o.rvol7 < 0.85 && (o.m24 ?? 0) > 1.5 ? "價漲量縮"
    : o.rvol7 < 0.85 && (o.m24 ?? 0) < -1.5 ? "價跌量縮"
    : "量價平淡";

  /* 4. 流動性質量 */
  const volScore = clamp(((Math.log10(Math.max(o.vol, 1)) - 5) / 3) * 100, 0, 100);
  const stable = clamp(100 - o.volCV * 80, 0, 100);
  const mcapScore = clamp(((Math.log10(Math.max(o.mcap, 1)) - 6.5) / 3.5) * 100, 0, 100);
  let liq = 0.45 * volScore + 0.25 * stable + 0.3 * mcapScore;
  if (t > 150) liq -= 35;
  if (t < 0.3) liq -= 25;
  if (o.vol < 3e5) liq -= 25;
  o.liq = clamp(liq, 0, 100);
  o.sLiq = o.liq;

  /* 5. 啟動階段 */
  const m7 = o.m7 ?? 0, m30 = o.m30 ?? 0, m24 = o.m24 ?? 0;
  o.stage =
    o.d90 > -3 && (m30 > 60 || o.rvol7 > 4 || t > 60) ? "hot"
    : o.rvol7 >= 1.4 && m24 < -1.5 ? "dump"
    : o.rvol7 >= 1.5 && o.pos < 0.55 && m7 > 0 ? "ignite"
    : o.rvol7 >= 1.3 && o.d90 > -12 && m7 > 5 ? "accel"
    : m30 < -15 && o.rvol7 < 1 ? "fade"
    : o.rvol7 < 1.2 && Math.abs(m7) < 5 ? "quiet"
    : "active";
  o.sStage = STAGES[o.stage].score;

  /* 6. 綜合雷達分數 */
  let raw = 0.34 * o.sRvol + 0.14 * o.sTurn + 0.24 * o.sPc + 0.16 * o.sStage + 0.12 * o.sLiq;
  // 放量而價格不配合，是出貨或虛漲，不該因為量大就排到前面
  if (o.pcTag === "價跌量增") raw *= 0.7;
  else if (o.pcTag === "價漲量縮") raw *= 0.88;
  if (o.liq < 30) raw *= 0.6;
  if (o.liq < 15) raw *= 0.6;
  o.radar = clamp(raw, 0, 100);

  /* 7. 風險 */
  o.riskChase = clamp(
    (o.d90 > -5 ? 35 : o.d90 > -12 ? 20 : 0) + clamp(m30 * 0.45, 0, 30) +
    (o.rvol7 > 4 ? 20 : o.rvol7 > 2.5 ? 10 : 0) + clamp(o.vola * 0.12, 0, 20), 0, 100);
  o.riskCrash = clamp(
    clamp(o.vola * 0.28, 0, 35) + clamp(m30 * 0.3, 0, 25) +
    (m24 < -3 && o.rvol7 > 1.5 ? 20 : 0) + (t > 80 ? 15 : 0) + (o.volCV > 0.9 ? 10 : 0), 0, 100);
  o.riskLiq = clamp(100 - o.liq, 0, 100);
  o.riskMax = Math.max(o.riskChase, o.riskCrash, o.riskLiq);

  /* 8. 白話解讀 */
  const R = [];
  R.push({
    k: "量能倍數", v: fmtX(o.rvol7), s: o.sRvol,
    t: o.rvol7 >= 3 ? `成交量衝到平常的 ${o.rvol7.toFixed(1)} 倍，是明顯的異常放量，通常代表有新資金或消息進場。`
      : o.rvol7 >= 1.8 ? `成交量放大到平常的 ${o.rvol7.toFixed(1)} 倍，有人開始積極買賣。`
      : o.rvol7 >= 1.2 ? `成交量比平常多一些（${o.rvol7.toFixed(1)} 倍），還算不上異動。`
      : o.rvol7 >= 0.8 ? "成交量跟平常差不多，沒有特別的資金動作。"
      : `成交量只有平常的 ${o.rvol7.toFixed(1)} 倍，市場對它興趣缺缺。`,
  });
  R.push({
    k: "成交額市值比", v: (t).toFixed(1) + "%", s: o.sTurn,
    t: t > 80 ? `一天成交的金額是整個市值的 ${t.toFixed(0)}%，高得不合常理，要留意是不是刷量。`
      : t > 25 ? `一天換手 ${t.toFixed(0)}%，資金進出非常熱絡。`
      : t > 5 ? `一天換手 ${t.toFixed(1)}%，交易活躍度健康。`
      : `一天只換手 ${t.toFixed(1)}%，籌碼幾乎沒在動。`,
  });
  R.push({
    k: "價量確認", v: o.pcTag, s: o.sPc,
    t: o.pcTag === "價漲量增" ? `價格漲 ${fmtPct(m24)} 且同時放量${o.brk20 ? "，還站上 20 日新高，是有量支撐的突破" : "，漲勢有成交量撐著"}。`
      : o.pcTag === "價跌量增" ? `量放大但價格跌了 ${fmtPct(m24)}，比較像有人趁人多的時候出貨。`
      : o.pcTag === "價漲量縮" ? "價格在漲，成交量卻縮小，買盤不厚，這種漲勢容易回吐。"
      : o.pcTag === "價跌量縮" ? "價跌但量也縮，多半只是沒人接手的自然下滑。"
      : "價格與成交量都沒有明確方向。",
  });
  R.push({
    k: "流動性質量", v: o.liq.toFixed(0), s: o.liq,
    t: o.liq >= 70 ? `一天成交 ${fmtUsd(o.vol)}，量能穩定，一般大小的單子進出不會有什麼滑價。`
      : o.liq >= 45 ? `一天成交 ${fmtUsd(o.vol)}，流動性中等，大單分批下比較安全。`
      : `一天只成交 ${fmtUsd(o.vol)}${o.volCV > 0.8 ? "，而且量忽大忽小" : ""}，價格容易被少數人推動，滑價和被操縱的風險都高。`,
  });
  R.push({
    k: "位置", v: o.d90 != null ? o.d90.toFixed(1) + "%" : "—", s: clamp(o.pos * 100, 0, 100),
    t: o.d90 > -3 ? "現價幾乎就在 90 天最高點，等於買在這三個月最貴的位置。"
      : o.d90 > -12 ? `距離 90 天高點還有 ${Math.abs(o.d90).toFixed(1)}%，位在區間上緣。`
      : o.d90 > -35 ? `距離 90 天高點 ${Math.abs(o.d90).toFixed(0)}%，在區間中段，上面還有空間。`
      : `距離 90 天高點 ${Math.abs(o.d90).toFixed(0)}%，仍在低檔區。`,
  });
  o.reasons = R;

  const risks = [];
  if (o.riskChase >= 60) risks.push(`追高風險 ${o.riskChase.toFixed(0)}：${o.d90 > -5 ? "已經在高點附近" : "近月漲幅偏大"}，現在進場等於接在漲勢末段。`);
  if (o.riskCrash >= 60) risks.push(`暴跌風險 ${o.riskCrash.toFixed(0)}：年化波動約 ${o.vola.toFixed(0)}%，這種波動下單日跌一兩成很正常。`);
  if (o.riskLiq >= 60) risks.push(`流動性風險 ${o.riskLiq.toFixed(0)}：成交太薄，想賣的時候不一定有人接，價格也容易被拉抬。`);
  if (!risks.length) risks.push("三項風險都在中等以下，但加密貨幣本來就沒有低風險標的。");
  o.riskNotes = risks;

  /* 表格用的一句話 */
  const bits = [];
  if (o.rvol7 >= 1.5) bits.push(`量增 ${o.rvol7.toFixed(1)} 倍`);
  else if (o.rvol7 < 0.8) bits.push("量縮");
  bits.push(o.pcTag);
  if (o.brk20) bits.push("破 20 日高");
  if (o.d90 > -3) bits.push("貼近 90 日高");
  if (o.liq < 35) bits.push("流動性偏薄");
  o.why = bits.slice(0, 3).join(" · ");
  o.bladePlan = bladePlan(o);
  return analyzeBear(o);
}



/* 依三刀流狀態產出具體的進出場計畫 */
function bladePlan(o) {
  if (!o.bladeState || o.bladeState === "none") return null;
  const p = o.price, g = o.ma60, r = o.ma240, b = o.ma20;
  const st = o.bladeState;
  const near = (x) => Math.abs((p / x - 1) * 100);

  if (st === "attackLong") {
    return {
      side: "多", action: "續抱／回踩小綠加碼",
      entry: `回踩小綠 ${fmtPrice(g)} 附近不破就進場${near(g) < 1.5 ? "（現價已貼近小綠，是相對好的位置）" : `（現價高出小綠 ${o.dGreen.toFixed(1)}%，追進去成本偏高）`}`,
      exit: `小藍 ${fmtPrice(b)} 斜率翻負就分批下車`,
      stop: `跌破小橘 ${fmtPrice(r)} 代表方向錯了，全部出場`,
      note: near(g) > 4 ? "離小綠太遠，這個位置追多的風險報酬不划算，等回踩。" : null,
    };
  }
  if (st === "takeLong") {
    return { side: "多", action: "停利，不加碼",
      entry: "不再新增多單", exit: `小藍 ${fmtPrice(b)} 已翻負斜率，張飛在收尾，分批獲利了結`,
      stop: `跌破小綠 ${fmtPrice(g)} 剩餘部位出清`,
      note: "小藍轉負只是停利訊號，不是反手做空的訊號。" };
  }
  if (st === "correction") {
    return { side: "觀望", action: "等站回小綠",
      entry: `站回小綠 ${fmtPrice(g)} 之上再進多單`, exit: "—",
      stop: `跌破小橘 ${fmtPrice(r)} 才改看空`,
      note: "破綠不破橘叫修正，方向沒變。這裡反手做空是逆勢，勝率低。" };
  }
  if (st === "rebound") {
    return { side: "觀望", action: "不追多，等表態",
      entry: `站上小橘 ${fmtPrice(r)} 且站穩，才考慮轉多`, exit: "空單先退場觀望",
      stop: "—",
      note: "上綠不上橘叫反彈，大方向還在空方。這裡追多最容易被套在反彈末端。" };
  }
  if (st === "attackShort") {
    return { side: "空", action: "續抱／反彈到小綠加空",
      entry: `反彈到小綠 ${fmtPrice(g)} 附近不站上就進場${near(g) < 1.5 ? "（現價已貼近小綠，位置理想）" : `（現價低於小綠 ${Math.abs(o.dGreen).toFixed(1)}%，追空成本偏高）`}`,
      exit: `小藍 ${fmtPrice(b)} 斜率翻正就分批回補`,
      stop: `站上小橘 ${fmtPrice(r)} 代表方向錯了，全部出場`,
      note: near(g) > 4 ? "離小綠太遠，這個位置追空容易被反彈軋到，等反彈。" : null };
  }
  return { side: "空", action: "回補，不加空",
    entry: "不再新增空單", exit: `小藍 ${fmtPrice(b)} 已翻正斜率，反彈將至，空單回補`,
    stop: `站上小綠 ${fmtPrice(g)} 剩餘空單出清`,
    note: "小藍轉正只是回補訊號，不代表可以直接翻多。" };
}

/* ════ 空頭雷達評分 ══════════════════════════════════════ */
function analyzeBear(o) {
  if (o.ema20 == null || !isFinite(o.ema20)) {
    return Object.assign(o, { bearStage: "unknown", bear: null, bearWhy: "未掃描" });
  }
  const price = o.price;
  o.distEma = (price / o.ema20 - 1) * 100;
  const below = price < o.ema20;
  const trendDown = o.ema20 < o.ema50;
  const struct = (below ? 1 : 0) + (trendDown ? 1 : 0) + (o.lowerHigh ? 1 : 0) + (o.lowerLow ? 1 : 0);
  o.structScore = (struct / 4) * 100;

  /* 追空風險：連續大跌、乖離過大、RSI 超賣時最高 */
  o.chaseShort = clamp(
    (o.rsi < 25 ? 35 : o.rsi < 32 ? 22 : 0) +
    clamp((-o.drop - 25) * 1.2, 0, 30) +
    (o.distEma < -18 ? 25 : o.distEma < -10 ? 12 : 0) +
    (o.downDays >= 4 ? 15 : 0), 0, 100);

  /* 階段判定 */
  const s1 = below && trendDown && (o.lowerLow || o.lowerHigh) && o.drop <= -8 &&
             o.retrace < 0.3 && o.volExpand >= 1.1;
  const s2 = trendDown && o.drop <= -12 && o.retrace >= 0.3 && o.retrace <= 0.78 &&
             (o.volRatio == null || o.volRatio < 0.95) && o.distEma < 6;
  o.bearStage = s1 ? "drop" : s2 ? "rebound" : "none";

  /* 分項評分 */
  const sTrend = o.structScore;
  const sVol = o.bearStage === "drop"
    ? clamp(((o.volExpand - 0.9) / 1.6) * 100, 0, 100)
    : o.volRatio == null ? 50 : clamp(((1 - o.volRatio) / 0.6) * 100, 0, 100);
  const sLoc = o.bearStage === "drop"
    ? clamp((o.brokeSupport ? 85 : 60) - clamp((-o.drop - 20) * 1.5, 0, 40), 0, 100)
    : clamp(100 - (Math.abs(o.retrace - 0.575) / 0.25) * 100, 0, 100);
  o.sBearTrend = sTrend; o.sBearVol = sVol; o.sBearLoc = sLoc;

  let b = 0.25 * sTrend + 0.25 * sVol + 0.25 * sLoc + 0.15 * (o.liq ?? 50) + 0.10 * (100 - o.chaseShort);
  if (o.bearStage === "none") b *= 0.45;
  if ((o.liq ?? 0) < 30) b *= 0.6;
  o.bear = clamp(b, 0, 100);

  /* 入場區、止損、目標 */
  const atr = o.atr || price * 0.03;
  if (o.bearStage === "rebound") {
    o.entryLo = Math.min(o.fib50, o.fib618);
    o.entryHi = Math.max(o.fib50, o.fib618);
    o.emaInZone = o.ema20 >= o.entryLo * 0.97 && o.ema20 <= o.entryHi * 1.03;
    o.stop = Math.max(o.fib786, o.bounceHigh) + 0.5 * atr;
  } else if (o.bearStage === "drop") {
    const above = [o.ema20, o.brokeSupport ? o.support : null].filter((v) => v && v > price);
    o.entryLo = above.length ? Math.min(...above) : price * 1.01;
    o.entryHi = above.length ? Math.max(...above) : price * 1.04;
    if (o.entryHi / o.entryLo < 1.01) { o.entryLo *= 0.995; o.entryHi *= 1.015; }
    o.emaInZone = true;
    o.stop = Math.max(o.entryHi * 1.01, o.bounceHigh) + 0.5 * atr;
  } else { o.entryLo = null; o.entryHi = null; o.stop = null; }

  if (o.entryLo) {
    const mid = (o.entryLo + o.entryHi) / 2;
    o.entryMid = mid;
    o.target = Math.min(o.swingLow, price * 0.97);
    o.stopPct = ((o.stop / mid) - 1) * 100;
    o.rr = o.stop > mid ? (mid - o.target) / (o.stop - mid) : null;
  }

  /* 白話解讀 */
  const R = [];
  R.push({ k: "趨勢結構", v: `${struct}/4`, s: sTrend,
    t: struct >= 3
      ? `價格${below ? "在 EMA20 之下" : "尚未跌破 EMA20"}，${o.lowerHigh ? "高點一個比一個低" : ""}${o.lowerLow ? "、低點也持續破底" : ""}，是明確的空頭排列。`
      : `四個空頭條件只符合 ${struct} 項，趨勢還沒完全轉弱，現在做空等於猜頭。` });
  R.push({ k: "與 EMA20 距離", v: fmtPct(o.distEma), s: clamp(50 - o.distEma * 2, 0, 100),
    t: o.distEma < -15 ? `已經跌到均線下方 ${Math.abs(o.distEma).toFixed(0)}%，乖離很大，隨時可能有反彈，這裡追空最容易被軋。`
      : o.distEma < -3 ? `在 EMA20 下方 ${Math.abs(o.distEma).toFixed(1)}%，均線正壓在頭上。`
      : o.distEma <= 3 ? "剛好貼著 EMA20，這條均線通常是反彈的第一道壓力。"
      : `還在 EMA20 上方 ${o.distEma.toFixed(1)}%，均線尚未轉為壓力。` });
  R.push({ k: "下跌幅度", v: fmtPct(o.drop), s: clamp(-o.drop * 2, 0, 100),
    t: `從近期高點 ${fmtPrice(o.swingHigh)} 跌到 ${fmtPrice(o.swingLow)}，跌了 ${Math.abs(o.drop).toFixed(1)}%。${-o.drop > 35 ? "跌幅已經很大，後面的下跌空間相對有限。" : ""}` });
  R.push({ k: "反彈幅度", v: o.bounce > 0.5 ? `${o.bounce.toFixed(1)}%（回撤 ${(o.retrace * 100).toFixed(0)}%）` : "尚未反彈", s: sLoc,
    t: o.retrace >= 0.45 && o.retrace <= 0.7
      ? `反彈到下跌段的 ${(o.retrace * 100).toFixed(0)}%，正好落在 0.5–0.618 這個常見的壓力帶。`
      : o.retrace < 0.3 ? "還在低檔附近，反彈幅度不足，屬於下跌途中而不是等待做空的位置。"
      : `反彈已經走到 ${(o.retrace * 100).toFixed(0)}%，超過 0.618 之後空頭結構的可信度就下降了。` });
  R.push({ k: "量能對比", v: o.volRatio == null ? "—" : `${o.volRatio.toFixed(2)}x`, s: sVol,
    t: o.bearStage === "drop"
      ? `最近三天的成交量是過去一個月平均的 ${o.volExpand.toFixed(2)} 倍，${o.volExpand >= 1.3 ? "下跌是帶量的，賣壓真實" : "量能沒有明顯放大，跌勢力道一般"}。`
      : o.volRatio == null ? "反彈時間太短，還算不出量能對比。"
      : o.volRatio < 0.7 ? `反彈的成交量只有下跌時的 ${(o.volRatio * 100).toFixed(0)}%，買盤明顯不足，這種反彈通常撐不久。`
      : o.volRatio < 1 ? `反彈量是下跌量的 ${(o.volRatio * 100).toFixed(0)}%，比下跌時小一些。`
      : `反彈量反而比下跌時還大，可能有真的買盤進場，空頭要留意。` });
  if (o.bodyFade != null) {
    R.push({ k: "陽線力道", v: o.bodyFade.toFixed(2) + "x", s: clamp((1.2 - o.bodyFade) * 90, 0, 100),
      t: o.bodyFade < 0.8 ? "反彈後段的陽線實體明顯變小，往上推的力道正在衰竭。"
        : o.bodyFade > 1.2 ? "反彈後段的陽線反而變長，買方還有力氣，先別急著空。"
        : "反彈的陽線力道大致持平，還看不出衰竭。" });
  }
  R.push({ k: "流動性", v: (o.liq ?? 0).toFixed(0), s: o.liq ?? 0,
    t: (o.liq ?? 0) >= 60 ? `一天成交 ${fmtUsd(o.vol)}，做空的滑價與借券成本相對可控。`
      : `一天只成交 ${fmtUsd(o.vol)}，流動性偏薄。空單被軋時不容易平倉，而且這種幣最容易被拉盤清算。` });
  o.bearReasons = R;

  const w = [];
  if (o.chaseShort >= 65) w.push(`追空風險 ${o.chaseShort.toFixed(0)}：已經連續下跌${o.downDays >= 3 ? ` ${o.downDays} 天` : ""}、乖離 ${o.distEma.toFixed(0)}%，RSI ${o.rsi.toFixed(0)}。在這個位置追空，等於在別人準備停損反彈時進場。`);
  else if (o.chaseShort >= 40) w.push(`追空風險 ${o.chaseShort.toFixed(0)}：跌勢已經走了一段，進場點要更嚴格，別看到黑K就追。`);
  if (o.bearStage === "drop") w.push("加速下跌段的特徵是波動放大，回抽往往又快又猛，止損位置要留足夠空間。");
  if (o.bearStage === "rebound" && o.retrace > 0.65) w.push("回撤已接近 0.618 上緣，再往上就要考慮這根本不是反彈，而是趨勢反轉。");
  if ((o.liq ?? 0) < 40) w.push("流動性不足的幣容易被人為拉抬觸發強平，部位要比平常再小一些。");
  if (!w.length) w.push("目前沒有特別突出的風險項，但空頭部位的虧損理論上沒有上限，務必設止損。");
  o.bearWarn = w;

  const bits = [];
  bits.push(BEAR_STAGES[o.bearStage].label);
  if (o.brokeSupport) bits.push("跌破支撐");
  if (o.volRatio != null && o.volRatio < 0.7) bits.push("反彈量縮");
  if (o.retrace >= 0.45 && o.retrace <= 0.7) bits.push("在 0.5–0.618 帶");
  if (o.chaseShort >= 65) bits.push("追空風險高");
  o.bearWhy = bits.slice(0, 3).join(" · ");
  return o;
}

export { analyze, analyzeBear, bladePlan, extractScan, structure };
