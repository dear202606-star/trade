# -*- coding: utf-8 -*-
"""统一全系统指标命名：远期(全周期) / 中期(年度) / 当下(今日)。

规则：
  全周期类一律带「全周期」前缀；年度类带年份或「当期」；今日类统一叫「今日」。
"""
import io
import glob

REPL = [
    # 策略页 · 当期块
    ('>当期策略收益</div>', '>{Y} 年策略收益</div>'),
    ('>同期标普 500</div>', '>同期标普 500</div>'),
    ('>跑赢标普 500</div>', '>跑赢标普 500</div>'),
    ('>最大回撤</div>', '>年内最大回撤</div>'),
    # 策略页 · 全周期块
    ('>累计收益</div>', '>全周期累计收益</div>'),
    ('>年化收益</div>', '>全周期年化</div>'),
    # 首页成绩卡
    ('>策略累计收益</div>', '>当期策略收益</div>'),
    ('>累计盈亏</div>', '>当期累计盈亏</div>'),
]

for p in ["site_src/strategy.html", "site_src/index.html", "site_src/portfolio.html"]:
    s = io.open(p, encoding="utf-8").read()
    o = s
    for a, b in REPL:
        s = s.replace(a, b)
    if s != o:
        io.open(p, "w", encoding="utf-8").write(s)
        print("命名已统一:", p)

# 策略页：把当期块的标题写成动态年份
p = "site_src/strategy.html"
s = io.open(p, encoding="utf-8").read()
if 'id="curYearRetLabel"' not in s:
    s = s.replace('>{Y} 年策略收益</div>', '<span id="curYearRetLabel">当期策略收益</span></div>')
    io.open(p, "w", encoding="utf-8").write(s)
    print("当期标签占位已加:", 'curYearRetLabel' in s)
print("完成")
