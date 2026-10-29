# -*- coding: utf-8 -*-
"""e2e_check 迁移到单页结构：去掉已删除页面与旧文案断言。"""
import io
import re

P = "e2e_check.py"
s = io.open(P, encoding="utf-8").read()

# 1) 页面断言：只查 index，且改查新文案
s = s.replace("策略累计收益、下一步", "2026 年策略收益、逐年记录")

# 2) 删除「子页面内容过短」的断言（现在是跳转壳）
s = re.sub(r'[ \t]*for\s+page\s+in\s*\([^)]*portfolio\.html[^)]*\):[^\n]*\n(?:[ \t]+.*\n)*', "", s)

# 3) 删除旧链接 404 断言
s = re.sub(r'[ \t]*for\s+old\s+in\s*\([^)]*plan\.html[^)]*\):[^\n]*\n(?:[ \t]+.*\n)*', "", s)

# 4) 兜底：逐行剔除引用已删页面的断言
lines = []
for l in s.split("\n"):
    if any(x in l for x in ("plan.html", "about.html", "nav.html", "内容过短", "旧链接")):
        continue
    lines.append(l)
s = "\n".join(lines)

io.open(P, "w", encoding="utf-8").write(s)

import ast
ast.parse(io.open(P, encoding="utf-8").read())
print("e2e_check 已迁移")
