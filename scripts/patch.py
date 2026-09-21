"""安全取代（BINANCE_LESSONS 第 14 條）。

sub()：單處取代，找不到或命中次數不對就中止。
apply()：一批修改（可跨多個檔、同一個檔多處依序套用）先在記憶體裡全部比對，
         任何一處對不上就中止、**一個檔都不寫**（r26）——不會有「改了一半」要去確認的狀態。
         版本號也放進同一批，避免「清單中止、版本號照改」的不一致。

    python3 scripts/patch.py        # 自我驗證
"""
import os
import sys
import tempfile


def sub(text, old, new, count=1, label=""):
    n = text.count(old)
    if n != count:
        raise SystemExit(f"✕ 取代失敗{f'［{label}］' if label else ''}：預期命中 {count} 次，實際 {n} 次\n---\n{old[:300]}")
    return text.replace(old, new)


def apply(edits):
    """edits：[(路徑, 原文, 新文, 次數, 標籤), ...]。全部比對通過才寫入；回傳改了哪些檔。"""
    buf, order = {}, []
    for path, old, new, count, label in edits:
        if path not in buf:
            buf[path] = open(path, encoding="utf-8").read()
            order.append(path)
        n = buf[path].count(old)
        if n != count:
            raise SystemExit(f"✕ apply 中止（一個檔都沒寫）［{label}］{path}：預期命中 {count} 次，實際 {n} 次\n---\n{old[:300]}")
        if new != "" and old.endswith("\n") != new.endswith("\n"):
            # 整段刪除（替換內容是空的）不檢查——以前會被這條擋下（r32 gold-scalper）
            # r26、r28：從檔案擷取的原文結尾有換行、替換內容沒有，下一行就會黏上來
            raise SystemExit(f"✕ apply 中止（一個檔都沒寫）［{label}］{path}：原文與替換內容的結尾換行不一致")
        buf[path] = buf[path].replace(old, new)
    for path in order:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(buf[path])
    return order


def exact(path, start, n):
    """從檔案讀出第 start 行起連續 n 行（含結尾換行）當比對字串，不手抄縮排（r31 pump-dump-hunter）。"""
    lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
    return "".join(lines[start - 1:start - 1 + n])


def selftest():
    d = tempfile.mkdtemp()
    a, b = os.path.join(d, "a.txt"), os.path.join(d, "b.txt")
    open(a, "w", encoding="utf-8").write("甲乙丙")
    open(b, "w", encoding="utf-8").write("丁戊己")
    try:
        apply([(a, "乙", "X", 1, "第一處"), (b, "不存在", "Y", 1, "第二處比對不到")])
        return "第二處比對不到卻沒有中止"
    except SystemExit:
        pass
    if open(a, encoding="utf-8").read() != "甲乙丙":
        return "中止了，但第一個檔已經被改了"
    try:
        apply([(a, "甲乙丙", "甲乙丙\n", 1, "結尾換行不一致")])
        return "結尾換行不一致卻沒有中止"
    except SystemExit:
        pass
    open(b, "w", encoding="utf-8").write("第一行\n要刪的\n第三行\n")
    apply([(b, "要刪的\n", "", 1, "整段刪除")])
    if open(b, encoding="utf-8").read() != "第一行\n第三行\n":
        return "整段刪除被擋下或刪錯"
    if exact(b, 2, 1) != "第三行\n":
        return "exact() 讀錯行"
    open(b, "w", encoding="utf-8").write("丁戊己")
    apply([(a, "乙", "X", 1, "一"), (a, "X丙", "XY", 1, "同檔第二處依序套用"), (b, "戊", "Z", 1, "二")])
    if open(a, encoding="utf-8").read() != "甲XY" or open(b, encoding="utf-8").read() != "丁Z己":
        return "全部命中時沒有正確寫入"
    return None


if __name__ == "__main__":
    err = selftest()
    print("✕ patch 自我驗證：" + err if err else "✓ patch 自我驗證：比對不到時一個檔都不寫、結尾換行不一致時中止（整段刪除除外）、同檔多處依序套用、exact() 讀對行")
    sys.exit(1 if err else 0)
