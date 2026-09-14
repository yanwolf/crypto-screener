import { STAGES } from "./constants.js";
import { clamp, pctRankOf, r1 } from "./util.js";

function tradeGate(detail, dv, bear) {
  const blocks = [], warns = [];
  const score = detail.score;

  // 衍生品方向與下單方向相反，是最強的反證
  if (dv && dv.quadrant && dv.bias != null) {
    const want = bear ? -1 : 1;
    if (dv.bias * want < -0.5) {
      blocks.push(`合約結構顯示「${dv.quadrant}」，與${bear ? "做空" : "做多"}方向相反`);
    } else if (dv.bias * want < 0) {
      warns.push(`合約結構偏「${dv.quadrant}」，與下單方向不一致`);
    }
  }

  // 雷達分數過低代表現在沒有值得進場的資金動作
  if (score != null) {
    if (score < 45) blocks.push(`雷達分數只有 ${r1(score)}，目前沒有明顯的資金動作`);
    else if (score < 58) warns.push(`雷達分數 ${r1(score)} 偏低，訊號不算強`);
  }

  // 沉寂整理或退潮低迷時進場，等於在沒有動能的地方押注
  // stage 是代碼（quiet/accel…），顯示時再轉中文；比對必須用代碼
  const stageLabel = (STAGES[detail.stage] || {}).label || detail.stage;
  if (!bear && ["quiet", "fade"].includes(detail.stage)) {
    blocks.push(`目前階段是「${stageLabel}」，量能與價格都沒有啟動`);
  }
  if (bear && ["accel", "ignite"].includes(detail.stage)) {
    blocks.push(`目前階段是「${stageLabel}」，逆勢做空風險高`);
  }

  // 量能沒有放大，多方進場缺少基本條件
  if (!bear && detail.rvol7 != null && detail.rvol7 < 1.3) {
    warns.push(`量能只有平常的 ${detail.rvol7.toFixed(2)} 倍，沒有放量`);
  }

  // 同方向過度擁擠
  if (dv && (dv.crowd ?? 0) > 70) {
    const fuel = dv.fuel || 0;
    const want = bear ? -1 : 1;
    if (fuel * want < 0) warns.push(`同方向部位擁擠度 ${dv.crowd}，反轉風險偏高`);
  }
  return { blocks, warns };
}


/* 停損距離（%）。與 engine.py 的 stop_pct 對齊；修改時兩邊同步。 */
function stopPct(row, bear, mode, atrMult, maxPct, minPct) {
  const price = row.price;
  const detail = { ma: null, atr: null, used: null, mode };
  if (!price || price <= 0) return [null, detail];
  let maPct = null;
  if (bear) {
    const s = row.stop;
    if (s && s > price) maPct = (s - price) / price * 100;
  } else {
    const m = row.ma60;
    if (m && m * 0.995 < price) maPct = (price - m * 0.995) / price * 100;
  }
  const atrPct = row.atr && row.atr > 0 ? atrMult * row.atr / price * 100 : null;
  detail.ma = maPct; detail.atr = atrPct;
  let cands = [];
  if (mode === "ma" && maPct != null) cands = [["ma", maPct]];
  else if (mode === "atr" && atrPct != null) cands = [["atr", atrPct]];
  else {
    cands = [["ma", maPct], ["atr", atrPct]].filter((x) => x[1] != null);
    if (cands.length) cands = [cands.reduce((a, b) => (a[1] <= b[1] ? a : b))];
  }
  if (!cands.length) return [null, detail];
  const [used, raw] = cands[0];
  detail.used = used;
  return [Math.max(minPct, Math.min(maxPct, raw)), detail];
}


/* ── 衍生品指標（Binance USDT 永續合約）──────────────────────
   資料來源皆為免金鑰公開端點。這裡不做熱力圖那種二維結構，
   改用能量化成分數的時間序列。
   修改門檻時，engine.py 的 deriv_analyze 必須同步。 */


function derivAnalyze(d) {
  const num = (a) => (a || []).filter((x) => x != null && Number.isFinite(x));
  const oi = num(d.oi), ls = num(d.ls), tk = num(d.taker), fh = num(d.fundingHist);
  const fund = d.funding, chg24 = d.chg24;

  const out = {
    oiChg4h: null, oiChg24h: null, oiNow: oi.length ? oi[oi.length - 1] : null,
    funding: fund, fundingApr: null, fundingRank: null,
    lsRatio: ls.length ? ls[ls.length - 1] : null, lsChg: null,
    taker: tk.length ? tk[tk.length - 1] : null,
    quadrant: null, crowd: null, fuel: null, note: null, bias: 0,
  };

  if (oi.length >= 5 && oi[oi.length - 5] > 0)
    out.oiChg4h = (100 * (oi[oi.length - 1] - oi[oi.length - 5])) / oi[oi.length - 5];
  if (oi.length >= 25 && oi[oi.length - 25] > 0)
    out.oiChg24h = (100 * (oi[oi.length - 1] - oi[oi.length - 25])) / oi[oi.length - 25];
  if (ls.length >= 25 && ls[ls.length - 25] > 0)
    out.lsChg = (100 * (ls[ls.length - 1] - ls[ls.length - 25])) / ls[ls.length - 25];

  if (fund != null && Number.isFinite(fund)) {
    out.fundingApr = fund * 3 * 365 * 100;         // 每 8 小時結算一次
    out.fundingRank = pctRankOf(fh, fund);
  }

  const oc = out.oiChg24h;
  /* 價格 × 未平倉量 四象限：同樣的漲跌，資金性質完全不同 */
  if (oc != null && chg24 != null) {
    const rising = chg24 > 0.5, falling = chg24 < -0.5;
    const oiUp = oc > 1.5, oiDn = oc < -1.5;
    if (rising && oiUp) {
      out.quadrant = "多方新倉"; out.bias = 1;
      out.note = "價漲且未平倉量增加，是新資金做多，趨勢有底氣。";
    } else if (rising && oiDn) {
      out.quadrant = "空單回補"; out.bias = 0.3;
      out.note = "價漲但未平倉量減少，是空單認賠推升，軋空行情續航力較弱。";
    } else if (falling && oiUp) {
      out.quadrant = "空方新倉"; out.bias = -1;
      out.note = "價跌且未平倉量增加，是新資金做空，下跌有延續性。";
    } else if (falling && oiDn) {
      out.quadrant = "多單出場"; out.bias = -0.3;
      out.note = "價跌但未平倉量減少，是多單平倉或被清算，賣壓可能接近尾聲。";
    } else {
      out.quadrant = "盤整"; out.bias = 0;
      out.note = "價格與未平倉量都沒有明確方向。";
    }
  }

  /* 擁擠度：某一方是否過度集中（0–100） */
  let crowd = 0;
  if (out.fundingApr != null) crowd += clamp(Math.abs(out.fundingApr) / 50, 0, 1) * 45;
  if (out.fundingRank != null) crowd += clamp(Math.abs(out.fundingRank - 50) / 50, 0, 1) * 25;
  if (out.lsRatio != null) crowd += clamp(Math.abs(out.lsRatio - 1) / 1.2, 0, 1) * 30;
  out.crowd = r1(clamp(crowd, 0, 100));

  /* 軋空／軋多燃料：正值＝空方擁擠（軋空燃料），負值＝多方擁擠 */
  if (fund != null && Number.isFinite(fund)) {
    const side = fund > 0 ? -1 : 1;               // 費率為正＝多方付錢＝多方擁擠
    let mag = clamp(Math.abs(out.fundingApr || 0) / 60, 0, 1);
    if (oc != null) mag *= clamp(0.5 + oc / 20, 0.3, 1.5);
    out.fuel = r1(clamp(side * mag * 100, -100, 100));
  }
  return out;
}

/* 把衍生品結論回饋到雷達分數。刻意做成小幅調整而非主導：
   衍生品是輔助證據，主結構仍由量價與均線決定。 */
function derivScoreAdjust(base, dv, side) {
  if (!dv || dv.quadrant == null) return [base, null];
  const bias = dv.bias || 0, crowd = dv.crowd || 0;
  let adj = 0; const why = [];
  const want = side === "bull" ? 1 : -1;

  if (bias * want > 0) { adj += 6 * Math.abs(bias); why.push(`${dv.quadrant}，與方向一致`); }
  else if (bias * want < 0) { adj -= 8 * Math.abs(bias); why.push(`${dv.quadrant}，與方向相反`); }

  if (crowd > 55) {
    const fuel = dv.fuel || 0;
    if (fuel * want < 0) {
      adj -= clamp((crowd - 55) / 45, 0, 1) * 16;
      why.push("同方向部位過度擁擠，反轉風險升高");
    } else {
      adj += clamp((crowd - 55) / 45, 0, 1) * 8;
      why.push("對手方擁擠，具備軋倉燃料");
    }
  }
  return [r1(clamp(base + adj, 0, 100)), why.length ? why.join("；") : null];
}

export { derivAnalyze, derivScoreAdjust, stopPct, tradeGate };
