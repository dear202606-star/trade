# -*- coding: utf-8 -*-
"""移除一切线下价格文件依赖：兜底改为报错，并删除仓库内的价格文件。"""
import io
import os
import ast

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 1) variant_b：去掉 alpha_bars / frozen_bars 兜底
P = os.path.join(BASE, "variant_b.py")
s = io.open(P, encoding="utf-8").read()
old = '''    b = alpha_bars(ticker) or frozen_bars(ticker)
    if b:
        return b
    return datafeed.load_bars(ticker)'''
new = '''    raise RuntimeError("行情只允许来自在线库（%s 无数据）" % ticker)'''
if old in s:
    s = s.replace(old, new)
    io.open(P, "w", encoding="utf-8").write(s)
    print("variant_b: 文件兜底已移除")
ast.parse(io.open(P, encoding="utf-8").read())

# 2) datafeed：去掉本地 json 兜底
P2 = os.path.join(BASE, "datafeed.py")
s2 = io.open(P2, encoding="utf-8").read()
if "_db_all().get(ticker)" in s2 and "禁止退回本地文件" not in s2:
    s2 = s2.replace('''    _b = _db_all().get(ticker)
    if _b:
        return _b''', '''    _b = _db_all().get(ticker)
    if _b:
        return _b
    if os.environ.get("ALLOW_FILE_FALLBACK") != "1":
        raise RuntimeError("行情只允许来自在线库（%s 无数据），禁止退回本地文件" % ticker)''')
    io.open(P2, "w", encoding="utf-8").write(s2)
    print("datafeed: 文件兜底已限制")
ast.parse(io.open(P2, encoding="utf-8").read())

# 3) 删除仓库内的价格文件
targets = [
    os.path.join(BASE, "static", "prices_unified"),
    os.path.join(BASE, "static", "prices"),
    os.path.join(BASE, "static", "bars_monthly.json"),
    os.path.join(BASE, "static", "prices_manifest.json"),
    os.path.join(BASE, "static", "prices_unified_manifest.json"),
    os.path.join(BASE, "data", "sina2"),
]
import shutil
for t in targets:
    if os.path.isdir(t):
        n = sum(len(f) for _, _, f in os.walk(t))
        shutil.rmtree(t)
        print("已删除目录 %s（%d 个文件）" % (os.path.relpath(t, BASE), n))
    elif os.path.exists(t):
        os.remove(t)
        print("已删除文件", os.path.relpath(t, BASE))
