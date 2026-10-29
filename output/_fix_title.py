# -*- coding: utf-8 -*-
"""修复被 PowerShell 吃掉的 $() 调用，并设置页面标题文案。"""
import io

P = "site_src/portfolio.html"
lines = io.open(P, encoding="utf-8").read().split("\n")

TITLE_LINE = ("    document.getElementById('pageTitle').textContent = "
              "y ? (y + ' 年当期策略表现') : '当期策略表现';")
SUB_LINE = ("    document.getElementById('acctSub').textContent = "
            "(y ? (y + ' 年度持仓 · ') : '') + '按建仓日收盘价买入，与「策略表现」同一口径';")

for i, l in enumerate(lines):
    t = l.strip()
    if t.startswith("\\pageTitle.textContent"):
        lines[i] = TITLE_LINE
    elif "acctSub" in l and "innerHTML" in l:
        lines[i] = SUB_LINE
    elif "acctSub" in l and "textContent = y ?" in l:
        lines[i] = SUB_LINE

io.open(P, "w", encoding="utf-8").write("\n".join(lines))
print("已修复：")
for i, l in enumerate(io.open(P, encoding="utf-8").read().split("\n")):
    if "pageTitle" in l or "acctSub" in l:
        print(" ", i + 1, l.strip()[:110])
