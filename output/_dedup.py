# -*- coding: utf-8 -*-
"""按用户要求做减法：
1) 首页删除逐只持仓卡（持仓明细只保留在账户页），改为一行业务摘要
2) 分年表补「2026 进行中」与回撤口径说明
3) 历史信号页去掉与首页重复的区块
"""
import io
import re

# ---------- 1) 首页：删除逐只持仓卡 ----------
p = "site_src/index.html"
s = io.open(p, encoding="utf-8").read()

# 生成 rowsHtml 的代码块删掉，改成一行摘要
start = s.find("    const rowsHtml = parts.map((r) =>")
end = s.find("    box.innerHTML =", start)
if start > 0 and end > start:
    s = s[:start] + """    const holdingLine = parts.map((r) => DSH.esc(r.ticker) + ' ' + Math.round(r.weight * 100) + '%').join(' · ');

""" + s[end:]

s = s.replace("      '<div class=\"grid\" style=\"margin-top:14px;gap:10px\">' + rowsHtml + '</div>';",
              "      '<div class=\"sub\" id=\"holdingLine\" style=\"margin-top:12px;font-size:12.5px;color:var(--txt-3)\"></div>';")

# 填充摘要
s = s.replace("    // 数据时间：让用户一眼知道这些数字是哪天的",
              "    const hl = document.getElementById('holdingLine');\n"
              "    if (hl) hl.innerHTML = '持有 ' + holdingLine + ' ｜ <a href=\"portfolio.html\" style=\"color:var(--txt-3)\">明细 →</a>';\n\n"
              "    // 数据时间：让用户一眼知道这些数字是哪天的")

io.open(p, "w", encoding="utf-8").write(s)
print("首页持仓卡已合并为一行:", "holdingLine" in s and "rowsHtml" not in s)

# ---------- 2) 策略页：分年表口径说明 ----------
p = "site_src/strategy.html"
s = io.open(p, encoding="utf-8").read()
if "回撤为年内谷底口径" not in s:
    anchor = '<div id="years"'
    note = ('<div class="sub" style="margin:0 0 10px;font-size:11.5px;color:var(--txt-3)">'
            '已完成年份按收盘复利；回撤为年内谷底口径，全周期最大回撤取其中最深一次；2026 年进行中，见「当期表现」。</div>\n  ')
    i = s.find(anchor)
    if i > 0:
        s = s[:i] + note + s[i:]
        io.open(p, "w", encoding="utf-8").write(s)
print("分年表说明已加:", "回撤为年内谷底口径" in s)
