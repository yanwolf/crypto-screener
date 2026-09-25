"""交易所相容性自檢。

這支程式的用途：幣安改 API 時，不要等到真的下單才發現。
開機跑一次（有異常發 Telegram），網頁「模擬單」頁也可以手動跑。
每一項都對應 BINANCE_LESSONS.md 的某一條；那份清單在 crypto-screener、
gold-scalper、pump-dump-hunter 三個專案內容相同，發現新坑就三份一起更新，
並在這裡加一項對應的檢查。

介面差異（相對 pump-dump-hunter 的範本）：
crypto-screener 的下單層是 trader._request(method, path, params, signed)，
回傳 (status, data)、不丟例外，所以這裡每一項都看狀態碼而不是 try/except。
全部是唯讀查詢，不會送出任何會成交或掛單的請求。
"""
import time

import trader as T

VERSION = "2026-09-25r71"        # 與 BINANCE_LESSONS.md 最上面的版本一致
EXTRA = []                         # 其他模組登記的本機檢查：fn(add)（main 的推播管道、設定檔，第 8 條 r40）


def check_order_outcome():
    """第 15 條：市價單「收下 ≠ 成交」。這項沒辦法向交易所驗證（要真的送單），
    這裡只驗程式的判讀本身：用固定的人造回應確認 NEW／成交 0 不會被當成成交。回傳錯誤說明或 None。"""
    cases = [({"status": "NEW", "executedQty": "0", "avgPrice": "0.00", "orderId": 1}, False, 0.0),
             ({"status": "FILLED", "executedQty": "5", "avgPrice": "1.2", "orderId": 2}, True, 5.0),
             ({"status": "EXPIRED", "executedQty": "2", "avgPrice": "1.2", "orderId": 3}, True, 2.0),
             ({"orderId": 4}, False, None)]
    for resp, final, ex in cases:
        o = T.order_outcome(resp)
        if o["final"] != final or o["executed"] != ex:
            return f"{resp} 判讀成 final={o['final']}、成交 {o['executed']}（應為 {final}、{ex}）"
    return None


def _msg(d):
    if isinstance(d, dict):
        return d.get("msg") or d.get("error") or str(d)[:120]
    return str(d)[:120]


def check():
    """回傳 [{item, status, msg, lesson}]。status：ok / warn / fail。"""
    out = []

    def add(item, st, msg="", lesson=None):
        out.append({"item": item, "status": st, "msg": str(msg)[:220], "lesson": lesson})
        return st

    # 第 15 條：市價單回應的判讀（本機、不送單；送單行為靠 tests/test_r42.py）
    try:
        e15 = check_order_outcome()
        add("市價單成交判讀", "fail" if e15 else "ok",
            e15 or "NEW／成交 0 不當成成交；平倉只在 FILLED 且成交量足夠、或確認部位沒了才撤停損", 15)
    except Exception as e:
        add("市價單成交判讀", "fail", f"{type(e).__name__}: {e}", 15)
    for fn in EXTRA:
        try:
            fn(add)
        except Exception as e:
            add("本機檢查", "fail", f"{getattr(fn, '__name__', fn)}：{type(e).__name__}: {e}")

    # 第 4 條：精度過濾器拿不到，下單會噴 -1111
    try:
        T.load_filters()
        f = T._filters.get("BTCUSDT") or {}
        add("精度過濾器", "ok" if f.get("tick") and f.get("step") else "fail",
            f"BTCUSDT tick={f.get('tick')} step={f.get('step')}，共 {len(T._filters)} 個交易對", 4)
    except Exception as e:
        add("精度過濾器", "fail", e, 4)

    if not (T.CFG.get("key") and T.CFG.get("secret")):
        add("API 金鑰", "warn", "未設定，以下需要簽章的項目略過")
        return out
    add("API 金鑰", "ok", "正式網" if T.CFG.get("live") else "模擬網")

    # 簽章前提：本機與幣安的時間差
    try:
        T.sync_time()
        off = T._time_offset[0]
        add("伺服器對時", "ok" if abs(off) < 1000 else "warn", f"時間差 {off} ms")
    except Exception as e:
        add("伺服器對時", "warn", e)

    # 持倉模式：決定停損單帶 reduceOnly 還是 positionSide
    pm = T.position_mode()
    add("持倉模式", "ok" if pm else "fail",
        {"hedge": "雙向（每張單帶 positionSide）", "oneway": "單向（平倉單帶 reduceOnly）"}.get(pm, "查不到"))

    # 第 1 條：條件單在 Algo 端點。查掛單要帶 symbol（權重 1），這裡用 BTCUSDT 探路
    st, d = T._request("GET", "/fapi/v1/openAlgoOrders", {"symbol": "BTCUSDT"}, signed=True)
    if st == 200:
        add("條件單端點", "ok", "Algo 端點可用（openAlgoOrders）", 1)
    else:
        add("條件單端點", "fail", f"HTTP {st}：{_msg(d)}（條件單可能無法掛上）", 1)

    # 錢包餘額
    b, err = T.account_balance(max_age=0)
    add("錢包餘額", "ok" if b else "fail",
        f"錢包 {b['wallet']:.2f} U　可用 {b['avail']:.2f} U" if b else err)

    # 第 5 條：槓桿上限（新子帳戶常被限 5x）
    mx = T.max_leverage("BTCUSDT")
    want = T.CFG.get("leverage")
    if mx is None:
        add("槓桿上限", "warn", "查不到 leverageBracket", 5)
    else:
        add("槓桿上限", "ok" if mx >= want else "warn", f"上限 {mx}x，設定 {want}x"
            + ("" if mx >= want else "（下單時會自動降到上限）"), 5)

    # 第 6 條：positionRisk 權重高，回應慢或 418 代表輪詢太兇
    t0 = time.time()
    st, d = T._request("GET", "/fapi/v2/positionRisk", signed=True)
    ms = (time.time() - t0) * 1000
    if st == 200:
        add("查持倉", "ok" if ms < 2000 else "warn", f"{ms:.0f} ms", 6)
    else:
        add("查持倉", "fail", f"HTTP {st}：{_msg(d)}" + ("（418 = 被限流）" if st == 418 else ""), 6)
    live_syms = set()
    if st == 200 and isinstance(d, list):
        live_syms = {p["symbol"] for p in d if abs(float(p.get("positionAmt") or 0)) > 0}

    # 第 3 條：送了單但沒記到帳
    now = int(time.time() * 1000)
    # 時間戳是 0 或缺值時算成很久以前（逾時）；以前 `or now` 把它當成剛送出，逾時的 pending 被藏起來（第 8 條 r28）
    stale = [s for s, p in (T.STATE.get("pending") or {}).items()
             if not isinstance(p.get("ts"), (int, float)) or now - p["ts"] > 120000]
    add("未認領的送單", "ok" if not stale else "warn",
        "無" if not stale else f"{'、'.join(stale)} 送單超過 2 分鐘仍未記帳", 3)

    # 帳上有、交易所沒有；交易所有、帳上沒有
    if T.STATE.get("loadError"):
        le = T.STATE["loadError"]
        add("持倉紀錄", "fail", f"持倉紀錄還沒載入，無法對帳（{le.get('error')}；壞檔另存於 {le.get('backup') or '—'}）", 8)
    mine = set(T.STATE.get("positions") or {})
    ghost = mine - live_syms if st == 200 else set()
    extra = live_syms - mine if st == 200 else set()
    # 第 2 條 r20：全量表裡沒有，不等於交易所沒有（空清單＝查詢異常）。逐幣確認後再下結論。
    unknown = set()
    for s_ in sorted(ghost):
        pos_ = T.STATE["positions"][s_]
        q = T._live_qty(s_, pos_["side"])
        if q is None:
            unknown.add(s_)
        elif q - float(pos_.get("base") or 0) > 1e-12:
            live_syms.add(s_)                      # 逐幣查到了：其實還在
    ghost -= unknown | live_syms
    if unknown:
        add("帳與交易所不符", "warn", f"查不到：{'、'.join(sorted(unknown))}（全量表沒有、逐幣也查不到，無法確認，不下結論）", 2)
    if ghost:
        add("帳與交易所不符", "warn", f"帳上有但交易所沒有：{'、'.join(sorted(ghost))}（下一輪對帳會記為平倉）", 2)
    if extra:
        add("交易所上的其他部位", "ok" if not T.CFG.get("live") else "warn",
            f"{'、'.join(sorted(extra))}"
            + ("（模擬網共用帳號，多半是其他專案的，不動它）" if not T.CFG.get("live")
               else "（正式網子帳戶應該只有本專案，請確認）"), 7)

    # 第 13 條：沒有部位卻還掛著的條件單（上一筆出場後沒撤乾淨）
    # 不帶 symbol 權重 40，只在自檢時查一次
    st, d = T._request("GET", "/fapi/v1/openAlgoOrders", {}, signed=True)
    if st == 200:
        rows = d.get("orders") if isinstance(d, dict) else d
        rows = rows if isinstance(rows, list) else []
        ours = set()
        for tr in T.STATE.get("trades") or []:
            for o in tr.get("orders") or []:
                if o.get("id"):
                    ours.add(str(o["id"]))
        # 查不到的幣不能判成孤兒（它的停損可能正在保護一個查不到的部位）
        orphan = [o for o in rows if o.get("symbol") not in live_syms and o.get("symbol") not in unknown]
        per_sym = {}
        for o in rows:
            per_sym[o.get("symbol")] = per_sym.get(o.get("symbol"), 0) + 1
        if not orphan:
            add("孤兒條件單", "ok", f"掛單 {len(rows)} 張，都有對應部位："
                + "、".join(f"{k} {v}" for k, v in sorted(per_sym.items())), 13)
        else:
            desc = []
            for o in orphan[:8]:
                tag = "本專案" if str(o.get("algoId")) in ours else "來源不明"
                desc.append(f"{o.get('symbol')} {o.get('orderType') or o.get('type')} #{o.get('algoId')}（{tag}）")
            add("孤兒條件單", "warn",
                f"{len(orphan)} 張沒有對應部位：" + "；".join(desc)
                + "。下次同一個幣進場時，舊的 closePosition 停損會讓新停損被拒", 13)
            out[-1]["orphans"] = [{"symbol": o.get("symbol"), "algoId": str(o.get("algoId")),
                                   "type": o.get("orderType") or o.get("type"),
                                   "price": o.get("triggerPrice") or o.get("activatePrice"),
                                   "ours": str(o.get("algoId")) in ours} for o in orphan]
    else:
        add("孤兒條件單", "warn", f"查不到全部掛單（HTTP {st}）", 13)

    # 第 8 條 r54、r56、r57：帳上成交價 vs 交易所均價。均價有出入的持倉列出來，並說明程式會怎麼處理——
    # 兩個證據（均價不同、而且查到這筆的平倉成交）才判定重開；只有均價不同＝同一筆，照常管理。
    rows_ = []
    for sym, pos in sorted((T.STATE.get("positions") or {}).items()):
        if float(pos.get("base") or 0) > 0 or pos.get("entryUnverified"):
            rows_.append(f"{sym} 不比對（{'有基準部位，均價是合併的' if float(pos.get('base') or 0) > 0 else '帳上進場價是估的'}）")
            continue
        q, avg = T._live_row(sym, pos["side"])
        if q is None:
            rows_.append(f"{sym} 查不到部位")
            continue
        if q - float(pos.get("base") or 0) <= 1e-12 or not T._avg_differs(pos, avg):
            continue
        rc = T.reopen_check(pos, avg)
        how = {"same": "同一筆（無平倉成交），照常管理",
               "reopened": "別人重開（查到平倉成交），下輪對帳結帳",
               "unknown": "判斷不了（成交明細查不到），不送單"}[rc]
        rows_.append(f"{sym} 帳上 {pos.get('entry'):g}／交易所 {avg:g}：{how}")
    n_pos = len(T.STATE.get("positions") or {})
    flagged = [r for r in rows_ if "交易所" in r or "查不到部位" in r]     # 有出入、或查不到
    if flagged:
        add("帳上成交價與交易所均價", "warn", f"{n_pos} 筆持倉，{len(flagged)} 筆要看：" + "；".join(rows_[:8]), 8)
        out[-1]["rows"] = rows_                  # 完整清單（訊息會被截在 220 字）
    else:
        add("帳上成交價與交易所均價", "ok",
            f"{n_pos} 筆持倉都一致" + (f"（{'；'.join(rows_[:8])}）" if rows_ else ""), 8)
        out[-1]["rows"] = rows_

    return out


LAST = {"at": None, "version": VERSION, "results": []}


def run():
    res = check()
    LAST.update(at=int(time.time() * 1000), version=VERSION, results=res)
    return res


def summary_lines(res=None):
    """給 Telegram 的簡短摘要：全過一行，有異常逐項列。"""
    res = res if res is not None else LAST["results"]
    bad = [r for r in res if r["status"] != "ok"]
    if not res:
        return []
    if not bad:
        return [f"自檢　{len(res)} 項通過（{VERSION}）"]
    return [f"自檢　{len(bad)} 項異常（{VERSION}）"] + [
        f"{'✕' if r['status'] == 'fail' else '⚠'} {r['item']}：{r['msg']}" for r in bad]
