"""安全取代（BINANCE_LESSONS 第 14 條）。

sub()：單處取代，找不到或命中次數不對就中止。
apply()：一批修改（可跨多個檔、同一個檔多處依序套用）先在記憶體裡全部比對，
         任何一處對不上就中止、**一個檔都不寫**（r26）——不會有「改了一半」要去確認的狀態。
         版本號也放進同一批，避免「清單中止、版本號照改」的不一致。

    python3 scripts/patch.py        # 自我驗證

改寫工具自己也會壞（r66 gold-scalper、r68 pump-dump-hunter）：寫入前的 compile() 只抓語法，抓不到沒匯入的名稱。
所以 apply() 寫入前用 pyflakes 的 API 在記憶體裡掃每個改過的 .py，有 undefined name 就整批中止、一個檔都不寫（不靠改完記得跑）；
pyflakes 沒裝時印警告、不擋。verify.sh 的 pyflakes 步驟另外掃整個 scripts/，排在自我驗證之前。
"""
import hashlib
import json
import os
import sys
import tempfile

# r65 pump-dump-hunter、r66：整批中止後「只重跑一部分」要擋下。中止時把這批每一處的指紋（路徑＋新字串）存成檔案；
# 下一批沒涵蓋全部指紋就中止，整批成功寫入後刪掉。指紋用新字串不用舊字串：改錨點重跑時舊字串會不同、新字串不變。
PENDING_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".patch_pending.json")


def _fp(path, new):
    return hashlib.sha1((os.path.abspath(path) + "\0" + new).encode("utf-8")).hexdigest()[:16]


def clear_pending():
    """放棄上一批（確定不重跑時），或自我驗證裡故意中止的案例之後清掉（r66：不清會擋到後面的案例）。"""
    if os.path.exists(PENDING_FILE):
        os.remove(PENDING_FILE)


def _check_pending(edits):
    if not os.path.exists(PENDING_FILE):
        return
    try:
        pend = json.load(open(PENDING_FILE, encoding="utf-8"))
    except Exception:
        pend = {}
    want = pend.get("fps") or []
    have = {_fp(p, n) for p, _o, n, _c, _l in edits}
    missing = [lbl for fp, lbl in zip(want, pend.get("labels") or [""] * len(want)) if fp not in have]
    if missing:
        raise SystemExit(f"✕ apply 中止（一個檔都沒寫）：上一批中止後只重跑了一部分——缺了 {len(missing)} 處：{'、'.join(missing)}。"
                         f"要用原本的整批清單重跑；確定要放棄上一批就先呼叫 clear_pending()（或刪掉 {PENDING_FILE}）")


def _save_pending(edits):
    with open(PENDING_FILE, "w", encoding="utf-8") as fh:
        json.dump({"fps": [_fp(p, n) for p, _o, n, _c, _l in edits],
                   "labels": [str(l) for _p, _o, _n, _c, l in edits]}, fh, ensure_ascii=False)


def sub(text, old, new, count=1, label=""):
    n = text.count(old)
    if n != count:
        raise SystemExit(f"✕ 取代失敗{f'［{label}］' if label else ''}：預期命中 {count} 次，實際 {n} 次\n---\n{old[:300]}")
    return text.replace(old, new)


def apply(edits):
    """edits：[(路徑, 原文, 新文, 次數, 標籤), ...]。全部比對通過才寫入；回傳改了哪些檔。
    中止（SystemExit）時記下這批的指紋，下一次只帶一部分就擋下（r65、r66）。"""
    _check_pending(edits)
    try:
        return _apply(edits)
    except SystemExit:
        _save_pending(edits)
        raise


def _apply(edits):
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
        i = buf[path].find(old)
        prev = [l for l in buf[path][:i].split("\n")[:-1] if l.strip()][-1:] if i > 0 else []
        defs = lambda s: sum(1 for l in s.split("\n") if l.lstrip().startswith(("def ", "class ", "async def ")))
        if prev and prev[0].strip().startswith("@") and defs(new) > defs(old):
            # r41 gold-scalper：新函式插在 @property 與 def 中間，裝飾器就套到新函式上——語法合法、編譯抓不到
            raise SystemExit(f"✕ apply 中止（一個檔都沒寫）［{label}］{path}：錨點緊接在裝飾器 {prev[0].strip()} 後面，"
                             f"插入的內容會被那個裝飾器套上；錨點要選在裝飾器之前")
        buf[path] = buf[path].replace(old, new)
    for path in order:
        if path.endswith(".py"):
            try:
                compile(buf[path], path, "exec")          # r35：寫入前先編譯，語法錯就一個檔都不寫
            except SyntaxError as e:
                raise SystemExit(f"✕ apply 中止（一個檔都沒寫）：{path} 改完後有語法錯（第 {e.lineno} 行）：{e.msg}")
            undef = _undefined_names(buf[path], path)     # r68：compile() 抓不到沒匯入的名稱
            if undef:
                raise SystemExit(f"✕ apply 中止（一個檔都沒寫）：{path} 改完後有未定義的名稱：{'；'.join(undef[:5])}")
    for path in order:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(buf[path])
    clear_pending()
    return order


def _undefined_names(src, path):
    """用 pyflakes 的 API 在記憶體裡掃一段原始碼，回傳 undefined name 的訊息清單；pyflakes 沒裝時印警告、回空清單（不擋）。"""
    try:
        import io
        from pyflakes import api as _pf_api, reporter as _pf_rep
    except ImportError:
        sys.stderr.write("  ! pyflakes 沒裝，apply() 無法檢查未定義的名稱（pip install pyflakes）\n")
        return []
    out = io.StringIO()
    _pf_api.check(src, path, _pf_rep.Reporter(out, out))
    return [l.strip() for l in out.getvalue().splitlines() if "undefined name" in l]


def exact(path, start, n):
    """從檔案讀出第 start 行起連續 n 行（含結尾換行）當比對字串，不手抄縮排（r31 pump-dump-hunter）。"""
    lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
    return "".join(lines[start - 1:start - 1 + n])


def selftest():
    """自我驗證期間把待重跑檔改指到暫存目錄、結束還原；真的那份在前後比對，變了就拋錯（r68、r69）。"""
    global PENDING_FILE
    real = PENDING_FILE
    real_before = open(real, encoding="utf-8").read() if os.path.exists(real) else None
    d = tempfile.mkdtemp()
    PENDING_FILE = os.path.join(d, ".pending.json")
    try:
        return _selftest(d)
    finally:
        PENDING_FILE = real
        real_after = open(real, encoding="utf-8").read() if os.path.exists(real) else None
        if real_after != real_before:
            raise RuntimeError("自我驗證動到了真的待重跑檔")


def _selftest(d):
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
    # r65、r66：中止後只重跑一部分要擋下；改了錨點但新字串相同的整批要放行；故意中止的案例之後要清掉待重跑批次
    if not os.path.exists(PENDING_FILE):
        return "中止後沒有記下待重跑批次"
    if not PENDING_FILE.startswith(d):
        return "自我驗證寫的不是暫存那份待重跑檔"
    try:
        apply([(b, "丁", "Y", 1, "只重跑第二處（改了錨點）")])
        return "中止後只重跑一部分卻沒有擋下"
    except SystemExit:
        pass
    if open(b, encoding="utf-8").read() != "丁戊己":
        return "只重跑一部分被擋下了，但檔案已經被改了"
    apply([(a, "乙", "X", 1, "第一處"), (b, "丁", "Y", 1, "第二處改了錨點、新字串相同")])
    if open(a, encoding="utf-8").read() != "甲X丙" or open(b, encoding="utf-8").read() != "Y戊己":
        return "整批重跑沒有正確寫入"
    if os.path.exists(PENDING_FILE):
        return "整批成功後待重跑批次沒有清掉"
    try:
        apply([(b, "不存在", "Y", 1, "再故意中止一次")])
    except SystemExit:
        pass
    clear_pending()
    if os.path.exists(PENDING_FILE):
        return "clear_pending() 之後待重跑批次還在"
    open(a, "w", encoding="utf-8").write("甲乙丙")
    open(b, "w", encoding="utf-8").write("丁戊己")
    try:
        apply([(a, "甲乙丙", "甲乙丙\n", 1, "結尾換行不一致")])
        return "結尾換行不一致卻沒有中止"
    except SystemExit:
        clear_pending()                                  # r66：故意中止的案例，之後要清掉，否則擋到後面的案例
    c = os.path.join(d, "c.py")
    open(c, "w", encoding="utf-8").write("x = 1\ny = 2\n")
    try:
        apply([(a, "甲", "甲2", 1, "先改一個非 py 檔"), (c, "y = 2", "y = (2", 1, "改出語法錯")])
        return "改出語法錯卻沒有中止"
    except SystemExit:
        clear_pending()
    if open(c, encoding="utf-8").read() != "x = 1\ny = 2\n" or open(a, encoding="utf-8").read() != "甲乙丙":
        return "改出語法錯中止了，但檔案已經被寫了"
    open(b, "w", encoding="utf-8").write("第一行\n要刪的\n第三行\n")
    apply([(b, "要刪的\n", "", 1, "整段刪除")])
    if open(b, encoding="utf-8").read() != "第一行\n第三行\n":
        return "整段刪除被擋下或刪錯"
    if exact(b, 2, 1) != "第三行\n":
        return "exact() 讀錯行"
    open(c, "w", encoding="utf-8").write("class K:\n    @property\n    def x(self):\n        return 1\n")
    try:
        apply([(c, "    def x(self):\n", "    def y(self):\n        return 2\n\n    def x(self):\n", 1, "插在裝飾器與 def 中間")])
        return "錨點緊接在裝飾器後面、插入新函式卻沒有中止"
    except SystemExit:
        clear_pending()
    apply([(c, "        return 1\n", "        return 3\n", 1, "改裝飾過的函式內容（不是插在中間）")])
    # r68：用了沒匯入的名稱——compile() 過得了，pyflakes 抓得到；中止、檔案沒被改動
    open(c, "w", encoding="utf-8").write("x = 1\n")
    if _undefined_names("import os\nzz_undefined_probe\n", "probe.py"):        # pyflakes 有裝才驗這項
        try:
            apply([(c, "x = 1\n", "x = 1\ny = os.getcwd()\n", 1, "用了沒匯入的 os")])
            return "用了沒匯入的名稱卻沒有中止"
        except SystemExit:
            clear_pending()
        if open(c, encoding="utf-8").read() != "x = 1\n":
            return "未定義名稱中止了，但檔案已經被改了"
        apply([(c, "x = 1\n", "import os\nx = 1\ny = os.getcwd()\n", 1, "有匯入就放行")])
    open(b, "w", encoding="utf-8").write("丁戊己")
    apply([(a, "乙", "X", 1, "一"), (a, "X丙", "XY", 1, "同檔第二處依序套用"), (b, "戊", "Z", 1, "二")])
    if open(a, encoding="utf-8").read() != "甲XY" or open(b, encoding="utf-8").read() != "丁Z己":
        return "全部命中時沒有正確寫入"
    return None


if __name__ == "__main__":
    err = selftest()
    print("✕ patch 自我驗證：" + err if err else "✓ patch 自我驗證：比對不到時一個檔都不寫、結尾換行不一致時中止（整段刪除除外）、改出語法錯時一個檔都不寫、錨點緊接裝飾器時中止、中止後只重跑一部分時擋下（整批重跑放行、成功後忘掉）、自我驗證只碰暫存的待重跑檔、用了沒匯入的名稱時中止、同檔多處依序套用、exact() 讀對行")
    sys.exit(1 if err else 0)
