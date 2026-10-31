# -*- coding: utf-8 -*-
"""修复 HF 数据源的日期解析（timestamp 是 Unix 秒，不是日期字符串）。"""
import io

P = "relay_source.py"
s = io.open(P, encoding="utf-8").read()

old_start = s.find("def fetch_hf(")
old_end = s.find("def sina_local(")
if old_start < 0 or old_end < 0:
    raise SystemExit("定位失败")

new_fn = '''def fetch_hf(sym, key=None):
    """HF 每日 CSV → 月末收盘；时间戳是 Unix 秒。只有拆股复权，无分红"""
    import datetime as _dt
    url = HF % (sym[0].upper(), sym.upper())
    try:
        raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()
    except Exception:
        return {}
    txt = raw.decode("utf-8", "replace").strip().split("\\n")
    if len(txt) < 2:
        return {}
    hdr = [h.strip().lower() for h in txt[0].split(",")]
    try:
        ti = hdr.index("timestamp") if "timestamp" in hdr else hdr.index("date")
        ci = hdr.index("close")
    except ValueError:
        return {}
    monthly = {}
    for line in txt[1:]:
        p = line.split(",")
        if len(p) <= max(ti, ci):
            continue
        tok = p[ti].strip()
        try:
            if tok.isdigit() or (tok[:1].isdigit() and tok.replace(".", "").isdigit()):
                d0 = _dt.datetime.utcfromtimestamp(int(float(tok))).strftime("%Y-%m-%d")
            else:
                d0 = tok[:10]
            ym = d0[:7]
            if not (len(ym) == 7 and ym[4] == "-" and ym[:4].isdigit()):
                continue
            monthly[ym] = float(p[ci])           # 逐日覆盖 → 最终留下月末值
        except Exception:
            continue
    return {m: (v, v) for m, v in monthly.items()}


'''
s = s[:old_start] + new_fn + s[old_end:]
io.open(P, "w", encoding="utf-8").write(s)

import ast
ast.parse(io.open(P, encoding="utf-8").read())
print("HF 日期解析已修:", "utcfromtimestamp" in s)
