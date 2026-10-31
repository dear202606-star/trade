# -*- coding: utf-8 -*-
"""修正：覆盖闸门排除年份后，曲线要按同一因子归一，否则末点仍是全期累计。"""
import io
import re

P = "variant_b.py"
s = io.open(P, encoding="utf-8").read()

# 1) 在闸门之前记录全期 eq
if "_eq_before_gate" not in s:
    i = s.find("    import statistics as _stat")
    if i < 0:
        raise SystemExit("找不到闸门")
    s = s[:i] + "    _eq_before_gate = eq\n" + s[i:]

# 2) 把旧的归一残留清理掉，重新写归一逻辑
s = re.sub(r"        _excl_factor = 1\.0\n(?:.*\n)*?        for _p in pts:\n            _p\[\"s\"\] = round\(_p\[\"s\"\] / _excl_factor, 6\)\n            _p\[\"b\"\] = round\(\(_p\.get\(\"b\"\) or 1\) / _excl_factor, 6\)\n",
           "", s)

if "_excl_factor = (_eq_before_gate / eq)" not in s:
    anchor = "        eq = _eq\n"
    idx = s.find(anchor)
    if idx < 0:
        raise SystemExit("找不到 eq 重算块")
    ins = (anchor +
           "        # 曲线是按全期链累乘的，排除年份后必须整体除以被排除段的累计因子\n"
           "        _excl_factor = (_eq_before_gate / eq) if eq else 1.0\n"
           "        if _excl_factor and abs(_excl_factor - 1.0) > 1e-12:\n"
           "            for _p in pts:\n"
           "                _p[\"s\"] = round(_p[\"s\"] / _excl_factor, 6)\n"
           "                if _p.get(\"b\"):\n"
           "                    _p[\"b\"] = round(_p[\"b\"] / _excl_factor, 6)\n")
    s = s[:idx] + ins + s[idx + len(anchor):]

io.open(P, "w", encoding="utf-8").write(s)

import ast
ast.parse(io.open(P, encoding="utf-8").read())
print("归一逻辑已重写:", "_excl_factor = (_eq_before_gate / eq)" in s)
