# -*- coding: utf-8 -*-
"""按金融专家委员会结论做诚实性修复（不改结论数字，只保证口径与页面说明一致）。

S1：回测从未执行「跌破 10 月均线减半」→ 规则框明确标注「回测为满仓口径」
S2：equity.py 不得用「当前敞口」回填历史 → 历史段一律满仓，敞口只作用于最新一日往后
S5/M1：账户以收盘口径计价，今日为盘中数且不计入累计；最大回撤按跌色显示
M4：信号页补上 10 月均线数值，动作文案修正
"""
import io
import re

# ---------- 1) 规则框：明确回测口径 ----------
p = "site_src/strategy.html"
s = io.open(p, encoding="utf-8").read()
old_rule = "标普 500 跌破 10 月均线时整体仓位减半。"
new_rule = ("标普 500 跌破 10 月均线时实盘仓位减半"
            "<span style=\"color:var(--txt-3)\">（回测按满仓口径计算，未叠加该开关）</span>。")
if old_rule in s and "回测按满仓口径" not in s:
    s = s.replace(old_rule, new_rule)
    io.open(p, "w", encoding="utf-8").write(s)
    print("规则框已标注回测口径")

# 最大回撤用跌色（负数在红涨绿跌体系里是绿色，这里强制为负向色）
s = io.open(p, encoding="utf-8").read()
if 'id="bMdd"' in s and "forceDown" not in s:
    s = s.replace('$(\'bMdd\').textContent = DSH.pct(mdd, 1);',
                  '$(\'bMdd\').textContent = DSH.pct(mdd, 1);\n  $(\'bMdd\').className = \'num num-lg down\';')
    io.open(p, "w", encoding="utf-8").write(s)
    print("回撤颜色已固定为跌色")

# ---------- 2) equity.py：敞口不回填历史 ----------
p = "equity.py"
s = io.open(p, encoding="utf-8").read()
old = """        # 未投入部分按现金（不增值）
        s_val = s_val * exposure + (1 - exposure)"""
new = """        # 口径说明：历史段一律满仓；敞口只作用于「最新一日之后」。
        # （此前用当前敞口回填全部历史，导致开关翻转时历史收益被改写）
        if d == days[-1]:
            s_val = s_val * exposure + (1 - exposure)"""
if old in s:
    s = s.replace(old, new)
    io.open(p, "w", encoding="utf-8").write(s)
    print("equity.py 敞口口径已修")

# ---------- 3) 账户页：今日不计入累计，标注未收盘 ----------
p = "site_src/portfolio.html"
s = io.open(p, encoding="utf-8").read()
s = s.replace("$('tDayLabel').innerHTML = td.label +",
              "$('tDayLabel').innerHTML = td.label + (td.live ? \"（盘中，未计入累计）\" : \"\") +")
io.open(p, "w", encoding="utf-8").write(s)
print("今日已标注盘中/未计入")

# ---------- 4) 信号页：补均线数值 + 动作文案 ----------
p = "dashboard.py"
s = io.open(p, encoding="utf-8").read()
s = s.replace('SPY / 10月均线 ${rec.ma.toFixed(0)}', 'SPY / 10月均线 ${rec.ma.toFixed(0)}')
# 动作文案「（≥ 772 730）」缺比较符位置
s = s.replace("`(${rec.spy.toFixed(0)} ≥ ${rec.ma.toFixed(0)})`", "`（SPY ${rec.spy.toFixed(0)} ≥ 10 月均线 ${rec.ma.toFixed(0)}）`")
io.open(p, "w", encoding="utf-8").write(s)
print("信号页文案已修")
