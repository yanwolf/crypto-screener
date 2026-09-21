"""安全取代：找不到就報錯，不靜靜略過（BINANCE_LESSONS 第 14 條的延伸）。

整段改寫時，若比對字串的縮排或內容跟檔案不符，str.replace 會什麼都不做、也不報錯，
改寫就這樣「看似成功、實際沒生效」。一律用這個函式：每一處都必須恰好命中指定次數。
"""


def sub(text, old, new, count=1, label=""):
    n = text.count(old)
    if n != count:
        raise SystemExit(f"✕ 取代失敗{f'［{label}］' if label else ''}：預期命中 {count} 次，實際 {n} 次\n---\n{old[:300]}")
    return text.replace(old, new)
