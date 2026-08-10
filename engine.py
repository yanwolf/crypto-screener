"""
訊號引擎（Python 版）

這是前端 core.jsx 中 extractScan / analyze / analyzeBear / evaluateSignals 的移植版，
公式逐條對齊，讓伺服器在沒有瀏覽器的情況下也能算出同樣的分數並發出提醒。

修改任何門檻或係數時，兩邊要一起改，否則網頁顯示的分數會和推播的分數對不起來。
"""

import math
import time

# ── 基本工具 ────────────────────────────────────────────────


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def mean(a):
    a = [x for x in a if x is not None and math.isfinite(x)]
    return sum(a) / len(a) if a else None


def stdev(a):
    a = [x for x in a if x is not None and math.isfinite(x)]
    if len(a) < 2:
        return 0.0
    m = sum(a) / len(a)
    return math.sqrt(sum((x - m) ** 2 for x in a) / (len(a) - 1))


def ema_last(arr, n):
    if not arr:
        return None
    k = 2 / (n + 1)
    e = arr[0]
    for v in arr[1:]:
        e = v * k + e * (1 - k)
    return e


def rsi_last(c, n=14):
    if len(c) < n + 2:
        return 50.0
    g = l = 0.0
    for i in range(1, n + 1):
        d = c[i] - c[i - 1]
        if d >= 0:
            g += d
        else:
            l -= d
    g /= n
    l /= n
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]
        g = (g * (n - 1) + max(d, 0)) / n
        l = (l * (n - 1) + max(-d, 0)) / n
    return 100.0 if l == 0 else 100 - 100 / (1 + g / l)


def atr_last(bars, n=14):
    if len(bars) < n + 1:
        return None
    tr = []
    for i in range(1, len(bars)):
        b, p = bars[i], bars[i - 1]["c"]
        tr.append(max(b["h"] - b["l"], abs(b["h"] - p), abs(b["l"] - p)))
    return mean(tr[-n:])


def pivots(bars, k=3):
    hi, lo = [], []
    for i in range(k, len(bars) - k):
        is_h = is_l = True
        for j in range(i - k, i + k + 1):
            if bars[j]["h"] > bars[i]["h"]:
                is_h = False
            if bars[j]["l"] < bars[i]["l"]:
                is_l = False
        if is_h:
            hi.append(i)
        if is_l:
            lo.append(i)
    return hi, lo


def to_daily(chart):
    days = {}
    for t, p in chart.get("prices", []):
        if p is None:
            continue
        d = int(t // 86400000)
        o = days.get(d)
        if not o:
            days[d] = {"d": d, "o": p, "h": p, "l": p, "c": p, "v": 0.0}
        else:
            o["h"] = max(o["h"], p)
            o["l"] = min(o["l"], p)
            o["c"] = p
    for t, v in chart.get("total_volumes", []):
        o = days.get(int(t // 86400000))
        if o and v is not None:
            o["v"] = v
    return [days[k] for k in sorted(days)]


# ── 趨勢結構 ────────────────────────────────────────────────


def structure(bars):
    if len(bars) < 35:
        return {}
    c = [b["c"] for b in bars]
    price = c[-1]
    ema20, ema50 = ema_last(c, 20), ema_last(c, 50)
    hi, lo = pivots(bars, 3)
    h2, l2 = hi[-2:], lo[-2:]
    lower_high = len(h2) == 2 and bars[h2[1]]["h"] < bars[h2[0]]["h"]
    lower_low = len(l2) == 2 and bars[l2[1]]["l"] < bars[l2[0]]["l"]

    win = bars[-50:]
    off = len(bars) - len(win)
    h_idx = max(range(len(win)), key=lambda i: win[i]["h"])
    l_idx = h_idx
    for i in range(h_idx, len(win)):
        if win[i]["l"] < win[l_idx]["l"]:
            l_idx = i
    swing_high, swing_low = win[h_idx]["h"], win[l_idx]["l"]
    rng = swing_high - swing_low
    drop = (swing_low / swing_high - 1) * 100 if swing_high else 0.0
    bounce = (price / swing_low - 1) * 100 if swing_low else 0.0
    retrace = clamp((price - swing_low) / rng, -0.2, 1.5) if rng > 0 else 0.0

    def fib(r):
        return swing_low + r * rng

    down_seg, up_seg = win[h_idx:l_idx + 1], win[l_idx:]
    down_vol = mean([b["v"] for b in down_seg]) or 1.0
    bounce_vol = mean([b["v"] for b in up_seg[1:]]) if len(up_seg) > 1 else None
    vol_ratio = bounce_vol / down_vol if (bounce_vol is not None and down_vol) else None

    ups = [(b["c"] - b["o"]) / (b["h"] - b["l"]) for b in up_seg if b["c"] > b["o"] and b["h"] > b["l"]]
    body_fade = (mean(ups[-2:]) / (mean(ups[:2]) or 1)) if len(ups) >= 4 else None

    prev_lows = [i for i in lo if i < off + h_idx]
    support = bars[prev_lows[-1]]["l"] if prev_lows else min(b["l"] for b in bars[-60:-20] or bars[-20:])

    cands = [v for v in (ema20, fib(0.5), fib(0.618), swing_high, support) if v and v > price * 1.002]
    resistance = min(cands) if cands else swing_high

    recent_vol = mean([b["v"] for b in bars[-3:]]) or 0.0
    base_vol = mean([b["v"] for b in bars[-30:-3]]) or 1.0

    down_days = 0
    i = len(c) - 1
    while i > 0 and c[i] < c[i - 1]:
        down_days += 1
        i -= 1

    return {
        "ema20": ema20, "ema50": ema50, "rsi": rsi_last(c), "atr": atr_last(bars),
        "lowerHigh": lower_high, "lowerLow": lower_low,
        "swingHigh": swing_high, "swingLow": swing_low,
        "drop": drop, "bounce": bounce, "retrace": retrace,
        "fib50": fib(0.5), "fib618": fib(0.618), "fib786": fib(0.786),
        "downVol": down_vol, "bounceVol": bounce_vol, "volRatio": vol_ratio,
        "bodyFade": body_fade, "support": support, "brokeSupport": price < support,
        "resistance": resistance, "volExpand": recent_vol / base_vol, "downDays": down_days,
        "bounceHigh": max(b["h"] for b in up_seg),
    }



# ── 均線三刀流（60 分 K：20／60／240MA） ────────────────────
# 240MA 小橘 決定方向 / 60MA 小綠 負責進出 / 20MA 小藍 負責收尾
BLADE_LABEL = {"attackLong": "多方攻擊", "takeLong": "多單停利", "correction": "修正",
               "rebound": "反彈", "attackShort": "空方攻擊", "coverShort": "空單回補",
               "none": "未掃描"}


def to_hourly(chart):
    m = {}
    for t, p in chart.get("prices", []):
        if p is not None:
            m[int(t // 3600000)] = p
    return [m[k] for k in sorted(m)]


def sma_at(a, n, back=0):
    end = len(a) - back
    if end < n:
        return None
    return sum(a[end - n:end]) / n


def blades(chart):
    H = to_hourly(chart)
    if len(H) < 250:
        return {}
    price = H[-1]
    ma20, ma60, ma240 = sma_at(H, 20), sma_at(H, 60), sma_at(H, 240)
    ma20b, ma60b, ma240b = sma_at(H, 20, 3), sma_at(H, 60, 6), sma_at(H, 240, 12)
    slope20 = ((ma20 - ma20b) / ma20b * 100) if ma20b else 0.0
    slope60 = ((ma60 - ma60b) / ma60b * 100) if ma60b else 0.0
    slope240 = ((ma240 - ma240b) / ma240b * 100) if ma240b else 0.0

    above_orange, above_green = price > ma240, price > ma60
    if above_orange and above_green:
        state = "attackLong" if slope20 >= 0 else "takeLong"
    elif above_orange:
        state = "correction"
    elif above_green:
        state = "rebound"
    else:
        state = "attackShort" if slope20 <= 0 else "coverShort"

    return {
        "ma20": ma20, "ma60": ma60, "ma240": ma240,
        "slope20": slope20, "slope60": slope60, "slope240": slope240,
        "bladeState": state,
        "dGreen": (price / ma60 - 1) * 100,
        "dOrange": (price / ma240 - 1) * 100,
        "dBlue": (price / ma20 - 1) * 100,
        "tangle": abs(ma60 - ma240) / price * 100 < 0.6,
    }


def extract_scan(chart, cur_vol=None):
    P = [p for _, p in chart.get("prices", []) if p is not None]
    V = [v for _, v in chart.get("total_volumes", []) if v is not None]
    if len(P) < 48 or len(V) < 48:
        return {"err": True}
    n = len(V)
    per_day = max(1, round(n / 90))
    now = cur_vol if (cur_vol and math.isfinite(cur_vol)) else V[-1]

    prior = V[:max(1, n - per_day)]          # 基準量排除最近 24 小時
    base7 = mean(prior[-7 * per_day:]) or 1.0
    base30 = mean(prior[-30 * per_day:]) or 1.0

    daily = [V[i - 1] for i in range(per_day, n + 1, per_day)]
    d30 = daily[-30:]
    vol_cv = (stdev(d30) / mean(d30)) if mean(d30) else 1.0

    below = sum(1 for v in V if v <= now)
    vol_pct = below / len(V) * 100

    prior_p = P[:max(1, len(P) - per_day)]
    hi20 = max(prior_p[-20 * per_day:])
    price = P[-1]

    daily_p = [P[i - 1] for i in range(per_day, len(P) + 1, per_day)]
    rets = [daily_p[i] / daily_p[i - 1] - 1 for i in range(1, len(daily_p)) if daily_p[i - 1] > 0]
    vola = stdev(rets[-30:]) * math.sqrt(365) * 100

    out = dict(structure(to_daily(chart)))
    out.update(blades(chart))
    out.update({
        "base7": base7, "base30": base30, "rvol7": now / base7, "rvol30": now / base30,
        "volPct": vol_pct, "volCV": vol_cv, "h90": max(P), "l90": min(P),
        "brk20": price >= hi20, "vola": vola, "ts": time.time() * 1000,
    })
    return out


# ── 多頭評分 ────────────────────────────────────────────────

STAGE_SCORE = {"quiet": 25, "ignite": 100, "accel": 85, "hot": 45,
               "dump": 12, "fade": 10, "active": 55}
STAGE_LABEL = {"quiet": "沉寂整理", "ignite": "剛啟動", "accel": "加速上漲", "hot": "高位過熱",
               "dump": "放量下跌", "fade": "退潮低迷", "active": "溫和活躍", "unknown": "未掃描"}
BEAR_LABEL = {"drop": "加速下跌", "rebound": "反彈等待", "none": "空頭不成立", "unknown": "未掃描"}


def analyze(r, sc):
    o = dict(r)
    if not sc or sc.get("err"):
        o.update({"scanned": False, "stage": "unknown", "bearStage": "unknown",
                  "radar": None, "bear": None, "liq": None})
        return o
    o.update(sc)
    o["scanned"] = True

    vol, mcap = o.get("vol") or 0, o.get("mcap") or 0
    if sc.get("base7") and vol:
        o["rvol7"] = vol / sc["base7"]
        o["rvol30"] = vol / sc["base30"] if sc.get("base30") else o.get("rvol30")

    price = o["price"]
    o["d90"] = (price / o["h90"] - 1) * 100 if o.get("h90") else None
    o["pos"] = (price - o["l90"]) / (o["h90"] - o["l90"]) if o.get("h90", 0) > o.get("l90", 0) else 0.5

    rvol = o.get("rvol7") or 0
    o["sRvol"] = clamp(math.log(max(rvol, 0.2)) / math.log(6) * 110 + 12, 0, 100)

    t = o.get("turn") or 0
    s_turn = clamp((math.log10(max(t, 0.05)) - math.log10(0.3)) /
                   (math.log10(25) - math.log10(0.3)) * 100, 0, 100)
    if t > 60:
        s_turn = max(25, s_turn - (t - 60) * 0.6)
    o["sTurn"] = s_turn

    m24, m7, m30 = o.get("m24") or 0, o.get("m7") or 0, o.get("m30") or 0
    s_pc = 50
    s_pc += 15 if rvol >= 1.5 else 8 if rvol >= 1.2 else (-10 if rvol < 0.8 else 0)
    s_pc += clamp(m24 * 3, -25, 25) + clamp(m7 * 0.6, -15, 15)
    s_pc += 15 if o.get("brk20") else 0
    o["sPc"] = clamp(s_pc, 0, 100)
    o["pcTag"] = ("價漲量增" if rvol >= 1.4 and m24 > 1.5 else
                  "價跌量增" if rvol >= 1.4 and m24 < -1.5 else
                  "價漲量縮" if rvol < 0.85 and m24 > 1.5 else
                  "價跌量縮" if rvol < 0.85 and m24 < -1.5 else "量價平淡")

    vol_score = clamp((math.log10(max(vol, 1)) - 5) / 3 * 100, 0, 100)
    stable = clamp(100 - (o.get("volCV") or 1) * 80, 0, 100)
    mcap_score = clamp((math.log10(max(mcap, 1)) - 6.5) / 3.5 * 100, 0, 100)
    liq = 0.45 * vol_score + 0.25 * stable + 0.3 * mcap_score
    if t > 150:
        liq -= 35
    if t < 0.3:
        liq -= 25
    if vol < 3e5:
        liq -= 25
    o["liq"] = clamp(liq, 0, 100)

    d90 = o["d90"] if o["d90"] is not None else -99
    o["stage"] = ("hot" if d90 > -3 and (m30 > 60 or rvol > 4 or t > 60) else
                  "dump" if rvol >= 1.4 and m24 < -3 else
                  "ignite" if rvol >= 1.5 and o["pos"] < 0.55 and m7 > 0 else
                  "accel" if rvol >= 1.3 and d90 > -12 and m7 > 5 else
                  "fade" if m30 < -15 and rvol < 1 else
                  "quiet" if rvol < 1.2 and abs(m7) < 5 else "active")

    raw = (0.34 * o["sRvol"] + 0.14 * o["sTurn"] + 0.24 * o["sPc"] +
           0.16 * STAGE_SCORE[o["stage"]] + 0.12 * o["liq"])
    if o["pcTag"] == "價跌量增":
        raw *= 0.7
    elif o["pcTag"] == "價漲量縮":
        raw *= 0.88
    if o["liq"] < 30:
        raw *= 0.6
    if o["liq"] < 15:
        raw *= 0.6
    o["radar"] = clamp(raw, 0, 100)

    vola = o.get("vola") or 0
    o["riskChase"] = clamp(
        (35 if d90 > -5 else 20 if d90 > -12 else 0) + clamp(m30 * 0.45, 0, 30) +
        (20 if rvol > 4 else 10 if rvol > 2.5 else 0) + clamp(vola * 0.12, 0, 20), 0, 100)
    o["riskLiq"] = clamp(100 - o["liq"], 0, 100)
    return analyze_bear(o)


# ── 空頭評分 ────────────────────────────────────────────────


def analyze_bear(o):
    if not o.get("ema20"):
        o.update({"bearStage": "unknown", "bear": None})
        return o
    price = o["price"]
    o["distEma"] = (price / o["ema20"] - 1) * 100
    below = price < o["ema20"]
    trend_down = o["ema20"] < o["ema50"]
    struct = sum([below, trend_down, bool(o.get("lowerHigh")), bool(o.get("lowerLow"))])
    o["structScore"] = struct / 4 * 100

    rsi, drop, vola = o.get("rsi", 50), o.get("drop", 0), o.get("vola") or 0
    o["chaseShort"] = clamp(
        (35 if rsi < 25 else 22 if rsi < 32 else 0) +
        clamp((-drop - 25) * 1.2, 0, 30) +
        (25 if o["distEma"] < -18 else 12 if o["distEma"] < -10 else 0) +
        (15 if o.get("downDays", 0) >= 4 else 0), 0, 100)

    retrace, vr = o.get("retrace", 0), o.get("volRatio")
    s1 = (below and trend_down and (o.get("lowerLow") or o.get("lowerHigh")) and
          drop <= -8 and retrace < 0.3 and o.get("volExpand", 0) >= 1.1)
    s2 = (trend_down and drop <= -12 and 0.3 <= retrace <= 0.78 and
          (vr is None or vr < 0.95) and o["distEma"] < 6)
    o["bearStage"] = "drop" if s1 else "rebound" if s2 else "none"

    s_vol = (clamp((o.get("volExpand", 0) - 0.9) / 1.6 * 100, 0, 100) if o["bearStage"] == "drop"
             else 50 if vr is None else clamp((1 - vr) / 0.6 * 100, 0, 100))
    s_loc = (clamp((85 if o.get("brokeSupport") else 60) - clamp((-drop - 20) * 1.5, 0, 40), 0, 100)
             if o["bearStage"] == "drop"
             else clamp(100 - abs(retrace - 0.575) / 0.25 * 100, 0, 100))

    b = (0.25 * o["structScore"] + 0.25 * s_vol + 0.25 * s_loc +
         0.15 * (o.get("liq") if o.get("liq") is not None else 50) +
         0.10 * (100 - o["chaseShort"]))
    if o["bearStage"] == "none":
        b *= 0.45
    if (o.get("liq") or 0) < 30:
        b *= 0.6
    o["bear"] = clamp(b, 0, 100)

    atr = o.get("atr") or price * 0.03
    if o["bearStage"] == "rebound":
        o["entryLo"], o["entryHi"] = min(o["fib50"], o["fib618"]), max(o["fib50"], o["fib618"])
        o["stop"] = max(o["fib786"], o["bounceHigh"]) + 0.5 * atr
    elif o["bearStage"] == "drop":
        above = [v for v in (o["ema20"], o["support"] if o.get("brokeSupport") else None) if v and v > price]
        o["entryLo"] = min(above) if above else price * 1.01
        o["entryHi"] = max(above) if above else price * 1.04
        if o["entryHi"] / o["entryLo"] < 1.01:
            o["entryLo"] *= 0.995
            o["entryHi"] *= 1.015
        o["stop"] = max(o["entryHi"] * 1.01, o["bounceHigh"]) + 0.5 * atr
    else:
        o["entryLo"] = o["entryHi"] = o["stop"] = None

    if o.get("entryLo"):
        mid = (o["entryLo"] + o["entryHi"]) / 2
        o["entryMid"] = mid
        o["target"] = min(o["swingLow"], price * 0.97)
        o["stopPct"] = (o["stop"] / mid - 1) * 100
        o["rr"] = (mid - o["target"]) / (o["stop"] - mid) if o["stop"] > mid else None
    return o


# ── 提醒條件 ────────────────────────────────────────────────


def bull_gate(r, c):
    rvol = r.get("rvol7") or 0
    checks = [
        ("量能放大", rvol >= c["minRvol"], f"量能 {rvol:.2f}x，門檻 {c['minRvol']}x"),
        ("價格走強", (r.get("m24") or 0) > 0 and (r.get("pcTag") == "價漲量增" or r.get("brk20")),
         f"{r.get('pcTag')}{' · 站上 20 日新高' if r.get('brk20') else ''}"),
        ("流動性合格", (r.get("liq") or 0) >= c["minLiq"], f"流動性 {(r.get('liq') or 0):.0f}，門檻 {c['minLiq']}"),
        ("追高風險可控", (r.get("riskChase") if r.get("riskChase") is not None else 100) <= c["maxChase"],
         f"追高風險 {(r.get('riskChase') or 0):.0f}，上限 {c['maxChase']}"),
        ("雷達分數達標", (r.get("radar") or 0) >= c["minRadar"],
         f"雷達分數 {(r.get('radar') or 0):.0f}，門檻 {c['minRadar']}"),
    ]
    if c.get("requireBlade"):
        checks.append(("三刀流站上綠橘",
                       r.get("bladeState") in ("attackLong", "takeLong"),
                       f"三刀流 {BLADE_LABEL.get(r.get('bladeState'), '未知')}"))
    return all(x[1] for x in checks), checks, (r.get("radar") or 0)


def bear_gate(r, c):
    vr = r.get("volRatio")
    reb_fail = (vr is not None and vr < c["maxVolRatio"]) if r.get("bearStage") == "rebound" \
        else (r.get("bearStage") == "drop" and (r.get("volExpand") or 0) >= 1.1)
    stop_ok = r.get("stop") is not None and r.get("stopPct") is not None and r["stopPct"] <= c["maxStopPct"]
    checks = [
        ("趨勢向下", (r.get("structScore") or 0) >= c["minStruct"], f"空頭結構 {(r.get('structScore') or 0):.0f}/100"),
        ("反彈失敗或賣壓延續", reb_fail,
         f"反彈量僅下跌量的 {(vr * 100):.0f}%" if r.get("bearStage") == "rebound" and vr is not None
         else f"下跌量能 {(r.get('volExpand') or 0):.2f} 倍"),
        ("流動性合格", (r.get("liq") or 0) >= c["minLiq"], f"流動性 {(r.get('liq') or 0):.0f}，門檻 {c['minLiq']}"),
        ("止損位置清晰", stop_ok,
         f"止損距入場 {r['stopPct']:.1f}%，上限 {c['maxStopPct']}%" if r.get("stopPct") is not None else "無有效止損位"),
        ("空頭評分達標", (r.get("bear") or 0) >= c["minBear"], f"空頭評分 {(r.get('bear') or 0):.0f}，門檻 {c['minBear']}"),
    ]
    if c.get("requireBlade"):
        checks.append(("三刀流跌破綠橘",
                       r.get("bladeState") in ("attackShort", "coverShort"),
                       f"三刀流 {BLADE_LABEL.get(r.get('bladeState'), '未知')}"))
    return all(x[1] for x in checks), checks, (r.get("bear") or 0)


EVENT_LABEL = {"first": "首次觸發", "upgrade": "評分提高",
               "breakout": "完成突破", "restart": "回調後再啟動"}


def evaluate(rows, cfg, states, now_ms):
    """回傳 (events, next_states)。去重規則與前端狀態機一致。"""
    events = []
    nxt = dict(states)
    for r in rows:
        if not r.get("scanned"):
            continue
        for side in ("bull", "bear"):
            ok, checks, score = (bull_gate(r, cfg["bull"]) if side == "bull"
                                 else bear_gate(r, cfg["bear"]))
            key = f"{r['id']}:{side}"
            st = nxt.get(key)
            price = r["price"]

            if not ok:
                if st:
                    nxt[key] = {**st, "cooled": True}
                continue

            cool = st and (now_ms - st["lastTs"] < cfg["cooldownMin"] * 60000)
            typ = None
            if not st:
                typ = "first"
            elif st.get("cooled"):
                typ = "restart"
            elif score >= st["peakScore"] + cfg["minDelta"]:
                typ = "upgrade"
            elif side == "bull" and price > st["refPrice"] * (1 + cfg["breakoutPct"] / 100) and r.get("brk20"):
                typ = "breakout"
            elif side == "bear" and price < st["refPrice"] * (1 - cfg["breakoutPct"] / 100) and r.get("brokeSupport"):
                typ = "breakout"

            if not typ or (cool and typ != "restart"):
                if st:
                    nxt[key] = {**st, "peakScore": max(st["peakScore"], score)}
                continue

            nxt[key] = {
                "firstTs": st["firstTs"] if st else now_ms,
                "firstPrice": st["firstPrice"] if st else price,
                "firstScore": st["firstScore"] if st else score,
                "lastTs": now_ms, "refPrice": price,
                "peakScore": max(st["peakScore"] if st else 0, score), "cooled": False,
            }
            events.append({
                "id": r["id"], "sym": r["sym"], "name": r.get("name", ""), "side": side,
                "type": typ, "ts": now_ms, "price": price, "score": score,
                "stage": STAGE_LABEL.get(r.get("stage"), "") if side == "bull"
                         else BEAR_LABEL.get(r.get("bearStage"), ""),
                "checks": [c[2] for c in checks],
                "blade": ({"state": r.get("bladeState"), "ma20": r.get("ma20"),
                           "ma60": r.get("ma60"), "ma240": r.get("ma240"),
                           "dGreen": r.get("dGreen"), "slope20": r.get("slope20")}
                          if r.get("bladeState") else None),
                "entryLo": r.get("entryLo"), "entryHi": r.get("entryHi"), "stop": r.get("stop"),
                "firstTs": nxt[key]["firstTs"], "firstPrice": nxt[key]["firstPrice"],
                "firstScore": nxt[key]["firstScore"],
                "maxUp": 0, "maxDown": 0, "verdict": "open", "source": "server",
            })
    return events, nxt


def fmt_price(v):
    if v is None:
        return "—"
    if v >= 1000:
        return f"${v:,.0f}"
    if v >= 1:
        return f"${v:.2f}"
    if v >= 0.01:
        return f"${v:.4f}"
    return f"${v:.3g}"


def alert_text(e):
    dirn = "多頭" if e["side"] == "bull" else "空頭"
    title = f"{e['sym']} {dirn}訊號 · {EVENT_LABEL[e['type']]}"
    lines = [
        f"{e['name']}（{e['sym']}）　{fmt_price(e['price'])}",
        f"階段：{e['stage']}　評分：{e['score']:.0f}",
        *["· " + c for c in e["checks"]],
    ]
    if e.get("blade"):
        b = e["blade"]
        lines.append(f"三刀流：{BLADE_LABEL.get(b['state'], '')}　"
                     f"小綠 {fmt_price(b['ma60'])}　小橘 {fmt_price(b['ma240'])}　小藍 {fmt_price(b['ma20'])}")
        lines.append(f"　距小綠 {b['dGreen']:+.1f}%　小藍斜率{'正' if b['slope20'] >= 0 else '負'}")
    if e.get("entryLo"):
        lines.append(f"參考區間：{fmt_price(e['entryLo'])}–{fmt_price(e['entryHi'])}　止損：{fmt_price(e['stop'])}")
    if e["type"] != "first":
        lines.append(f"首次觸發時 {fmt_price(e['firstPrice'])} / {e['firstScore']:.0f} 分")
    lines.append("由伺服器背景監控發出，僅供技術分析參考，不構成投資建議。")
    return title, "\n".join(lines)
