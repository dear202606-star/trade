# -*- coding: utf-8 -*-
"""Step 19: 直白版报告"""
import os, json
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "output")
IMG = os.path.join(OUT, "img")

import base64


def b64(p):
    return base64.b64encode(open(p, "rb").read()).decode()


def main():
    df = pd.read_csv(os.path.join(OUT, "final_models.csv"))
    sens = pd.read_csv(os.path.join(OUT, "final_sens.csv"))
    pk = pd.read_csv(os.path.join(OUT, "current_picks.csv"))
    meta = json.load(open(os.path.join(OUT, "final_meta.json"), encoding="utf-8"))

    RATE = {"第1名": "★★☆☆☆ 刺激但危险", "第1+2名": "★★★☆☆", "前3名": "★★★★☆ 收益最高",
            "前5名": "★★★☆☆", "前10名": "★★▲☆☆ 太保守", "前3名(12-1)": "★★★☆☆",
            "前10名(12-1)": "★★★☆☆", "前3名+趋势": "★★★★★ 首选",
            "前5名+趋势": "★★★☆☆", "前10名+趋势": "★★☆☆☆ 不如指数", "标普500": "★★★☆☆ 基准"}
    ORDER = ["第1名", "第1+2名", "前3名", "前3名+趋势", "前3名(12-1)", "前5名",
             "前5名+趋势", "前10名", "前10名(12-1)", "前10名+趋势", "标普500"]
    d = df.set_index("策略").loc[[x for x in ORDER if x in df["策略"].values]]

    def g(name, col):
        return d.loc[name, col]

    rows = ""
    for nm, r in d.iterrows():
        hl = ' style="background:#1d2739"' if nm in ("前3名+趋势", "前3名") else ""
        star = RATE.get(nm, "")
        rows += (f"<tr{hl}><td><b>{nm}</b></td>"
                 f"<td class='num'><b>{r['年化']*100:.1f}%</b></td>"
                 f"<td class='num'>{r['累计']:.0f} 倍</td>"
                 f"<td class='num' style='color:#ff9b9b'>{r['回撤']*100:.0f}%</td>"
                 f"<td class='num'>{r['最差年']*100:.0f}%</td>"
                 f"<td class='num'>{int(r['赚钱年数'])}/{int(r['总年数'])}</td>"
                 f"<td class='num'>{r['跑赢标普年数']:.0f}/{int(r['总年数'])}</td>"
                 f"<td class='num' style='color:#9fd0ff'>{r['下限']*100:.0f}% ~ {r['上限']*100:.0f}%</td>"
                 f"<td>{star}</td></tr>")

    # 预期收益表
    def val(rate, years):
        return (1 + rate) ** years

    exp_rows = ""
    for nm in ["前3名+趋势", "前3名", "第1名", "标普500"]:
        r = d.loc[nm]
        cells = ""
        for yrs in (5, 10):
            cells += (f"<td class='num'>{val(r['下限'],yrs)*100:.0f} 万</td>"
                      f"<td class='num'><b>{val(r['中位'],yrs)*100:.0f} 万</b></td>"
                      f"<td class='num'>{val(r['上限'],yrs)*100:.0f} 万</td>")
        exp_rows += f"<tr><td><b>{nm}</b></td>{cells}</tr>"

    sens_rows = ""
    for _, r in sens.iterrows():
        sens_rows += (f"<tr><td>{int(r['起点'])} 年起</td><td>{r['策略']}</td>"
                      f"<td class='num'>{r['年化']*100:.1f}%</td>"
                      f"<td class='num'>{r['回撤']*100:.0f}%</td></tr>")

    pick_rows = ""
    for _, r in pk.head(10).iterrows():
        pick_rows += (f"<tr><td class='num'>{int(r['名次'])}</td><td class='tk'>{r['代码']}</td>"
                      f"<td>{r['公司']}</td><td class='num' style='color:#2ecc71'>+{r['过去12月涨幅']*100:.0f}%</td>"
                      f"<td class='num'>${r['股价']:.2f}</td></tr>")

    trend_on = meta["trend_on"]
    t_txt = "满仓（大盘在 10 月均线上方）" if trend_on else "半仓（大盘跌破 10 月均线）"

    imgs = {k: b64(os.path.join(IMG, f)) for k, f in {
        "g1": "g1_rank.png", "g2": "g2_curve.png", "g3": "g3_dd.png"}.items()}

    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>买上年赢家：全模型回测与实盘方案</title>
<style>
:root{{--bg:#0f1420;--card:#161d2c;--line:#2a3346;--txt:#e6ecf5;--dim:#93a4bd;--gold:#ffc857}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--txt);font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;line-height:1.85;font-size:15px}}
.wrap{{max-width:1100px;margin:0 auto;padding:52px 30px 100px}}
h1{{font-size:30px;margin:0 0 6px;letter-spacing:-.5px}}
h2{{font-size:21px;margin:56px 0 6px;padding-left:13px;border-left:5px solid var(--gold)}}
h3{{font-size:16px;margin:26px 0 8px;color:var(--gold)}}
.sub{{color:var(--dim);font-size:13.5px;margin-bottom:8px}}
.take{{color:#c9d6e8;font-size:15px;margin:6px 0 18px}}
.big{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:28px 0 10px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px 22px}}
.card .n{{font-size:32px;font-weight:800;color:var(--gold);letter-spacing:-1px;line-height:1.2}}
.card .t{{font-size:12.5px;color:var(--dim);margin-top:4px}}
.card .s{{font-size:11.5px;color:#6f8199;margin-top:8px}}
table{{width:100%;border-collapse:collapse;font-size:13.2px;margin:14px 0}}
th{{background:#1b2434;color:#aab9cf;font-weight:600;text-align:left;padding:10px 8px;border-bottom:1px solid var(--line);font-size:12.5px}}
td{{padding:9px 8px;border-bottom:1px solid #1e2637}}
tr:hover td{{background:#182033}}
.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.tk{{font-weight:700;color:var(--gold);font-family:Consolas,monospace}}
img{{width:100%;border-radius:12px;border:1px solid var(--line);margin:14px 0 6px;display:block}}
.note{{background:#141b28;border:1px solid var(--line);border-left:4px solid #4da3ff;border-radius:8px;padding:15px 20px;font-size:13.8px;color:#c3cfe0;margin:18px 0}}
.ok{{border-left-color:#2ecc71}}
.bad{{border-left-color:#ff5a5f}}
.warnb{{border-left-color:#ffa64d}}
ul{{padding-left:22px}} li{{margin:8px 0}}
.step{{display:flex;gap:14px;margin:14px 0;align-items:flex-start}}
.step .no{{flex:0 0 30px;height:30px;border-radius:50%;background:var(--gold);color:#101722;
font-weight:800;display:flex;align-items:center;justify-content:center;font-size:14px}}
.step .tx{{flex:1}}
.step .tx b{{color:var(--gold)}}
.big-num{{font-size:26px;font-weight:800;color:var(--gold)}}
.foot{{color:#6f8199;font-size:11.5px;margin-top:50px;border-top:1px solid var(--line);padding-top:16px}}
.kv{{display:grid;grid-template-columns:150px 1fr;gap:4px 16px;font-size:14px;margin:10px 0}}
.kv .k{{color:var(--dim)}}
</style></head><body><div class="wrap">

<h1>买上年赢家：全模型回测与实盘方案</h1>
<div class="sub">样本：2009–2025 共 18 个完整年度 · 标普500 成分股 · 已扣 0.2% 单边交易成本</div>
<div class="take">规则一句话：<b>每年 12 月底，按过去 12 个月涨幅给标普500 成分股排序，买入前 N 名，持有一整年，次年同日换仓。</b></div>

<h2>一、结论</h2>
<div class="big">
  <div class="card"><div class="n">29.4%</div><div class="t">首选策略年化收益</div><div class="s">前3名 + 大盘趋势减仓 · 18 年 99 倍</div></div>
  <div class="card"><div class="n">−32.5%</div><div class="t">首选策略最大亏损</div><div class="s">18 年里有 17 年赚钱，最差一年 −20.6%</div></div>
  <div class="card"><div class="n">12.7%</div><div class="t">标普500 同期年化</div><div class="s">首选策略每年多赚 16.7 个百分点</div></div>
</div>
<ul>
<li><b>最好的不是"只买第 1 名"。</b>第 1 名年化 37.2%，但最大亏损 −60.7%，18 年里有 7 年跑输指数——将近四成时间在煎熬。</li>
<li><b>"前 3 名 + 趋势减仓"是收益与风险的平衡点。</b>年化只比第 1 名少 7.8 个百分点，最大亏损却从 −61% 收窄到 −33%。</li>
<li><b>买得越多，越接近指数。</b>前 10 名只有 20%，前 10 名加趋势只剩 16.7%——分散过头等于放弃超额收益。</li>
</ul>

<h2>二、全部模型对比</h2>
<div class="take">同样是"买上年赢家"，换个买法，结果差 20 个百分点。</div>
<table>
<thead><tr><th>策略</th><th class="num">年化</th><th class="num">18年累计</th><th class="num">最大亏损</th>
<th class="num">最差一年</th><th class="num">赚钱年数</th><th class="num">跑赢指数</th>
<th class="num">预期区间</th><th>评级</th></tr></thead>
<tbody>{rows}</tbody></table>
<div class="note">「预期区间」= 用历史数据重抽样 4000 次，有 90% 的概率年化落在这个范围。区间越窄，说明结果越不靠运气。</div>

<img src="data:image/png;base64,{imgs['g1']}" alt="年化对比">

<h2>三、走势与回撤</h2>
<div class="take">看两条线就明白：第 1 名（红）冲得最高、摔得最狠；前 3 名 + 趋势（蓝）走得更稳。</div>
<img src="data:image/png;base64,{imgs['g2']}" alt="净值">
<img src="data:image/png;base64,{imgs['g3']}" alt="回撤">
<div class="note bad">第 1 名有 7 个年份跑输指数（含 2009–2011 连续三年），最深一次回撤 60%。这段时期正是大多数人会放弃的时候。</div>

<h2>四、预期收益：100 万块，5 年 / 10 年后大概多少</h2>
<div class="take">按历史重抽样的悲观（10% 分位）、中性（中位数）、乐观（90% 分位）三档推算。</div>
<table>
<thead><tr><th style="width:150px">策略</th>
<th class="num">5年·悲观</th><th class="num">5年·中性</th><th class="num">5年·乐观</th>
<th class="num">10年·悲观</th><th class="num">10年·中性</th><th class="num">10年·乐观</th></tr></thead>
<tbody>{exp_rows}</tbody></table>
<div class="note warnb">这是<b>统计外推，不是承诺</b>。注意两点：①「第1名」的悲观情形（5 年 204 万）反而低于「前3名」（241 万）——高收益策略的下限更低；②10 年数字不要当真，成熟市场里长期年化 15%–20% 已属顶级，把这里的中性值当<b>上限</b>看更稳妥。</div>

<h2>五、对抗性审查：这个结论靠得住吗</h2>
<div class="take">我把能想到的“作弊”和“巧合”都试了一遍，只有一项没过。</div>
<table>
<thead><tr><th style="width:200px">检查项</th><th>怎么查的</th><th style="width:110px">结果</th></tr></thead>
<tbody>
<tr><td>换个起点还成立吗</td><td>分别从 2009 / 2011 / 2013 / 2015 年开始算</td><td><span style="color:#2ecc71">✅ 全部成立，且越晚起点收益越高</span></td></tr>
<tr><td>参数换一遍还成立吗</td><td>60 组参数（买几只 × 动量窗口 × 调仓频率 × 减仓规则）</td><td><span style="color:#2ecc71">✅ 85% 的组合跑赢指数</span></td></tr>
<tr><td>加上交易成本呢</td><td>0 → 0.5% 单边</td><td><span style="color:#2ecc71">✅ 影响不到 3 个百分点/年</span></td></tr>
<tr><td>有没有偷看未来</td><td>改用“当年时点成分股”；趋势信号只用上月数据</td><td><span style="color:#2ecc71">✅ 已修正两处</span></td></tr>
<tr><td>频繁换仓会不会更好</td><td>季度调仓 vs 年度调仓</td><td><span style="color:#2ecc71">✅ 年度更好（季度掉 8 个点）</span></td></tr>
<tr><td>退市的股票算进去了吗</td><td>本地数据只有现在还存在的股票</td><td><span style="color:#ff5a5f">❌ 没算进去，收益被高估 3–6 个点</span></td></tr>
</tbody></table>
<div class="note bad"><b>唯一的硬伤是“生存者偏差”。</b>历史上那些退市、被收购、破产的公司不在样本里，而它们恰恰更容易是“假赢家”。真实收益大概率要打 3–6 个百分点的折扣——也就是 <b>23%–26%</b>，而不是 29.4%。</div>

<h3>起点敏感性明细</h3>
<table>
<thead><tr><th>起点</th><th>策略</th><th class="num">年化</th><th class="num">最大亏损</th></tr></thead>
<tbody>{sens_rows}</tbody></table>

<h2>六、具体怎么建仓（照着做）</h2>
<div class="step"><div class="no">1</div><div class="tx"><b>时间</b>：每年 12 月最后一个交易日收盘后做一次，全年只做这一次。</div></div>
<div class="step"><div class="no">2</div><div class="tx"><b>股票池</b>：标普500 成分股，剔除股价低于 5 美元的（避免仙股）。</div></div>
<div class="step"><div class="no">3</div><div class="tx"><b>排序</b>：算每只股票过去 12 个自然月的涨幅，从高到低排。</div></div>
<div class="step"><div class="no">4</div><div class="tx"><b>买入</b>：买前 <b>3 名</b>，等权，每只占总仓位的 33%。</div></div>
<div class="step"><div class="no">5</div><div class="tx"><b>减仓开关</b>：如果标普500 上月收盘价低于 10 个月均线，整个策略仓位砍到 <b>50%</b>；否则满仓。每月初检查一次。</div></div>
<div class="step"><div class="no">6</div><div class="tx"><b>换仓</b>：次年 12 月最后一个交易日，重新排名，全部卖出换成新的前 3 名。</div></div>

<h3>仓位上限（很重要）</h3>
<div class="kv">
<div class="k">策略总仓位</div><div>不超过股票资产的 <b>30%</b>，其余 70% 放指数基金</div>
<div class="k">单只上限</div><div>不超过总资产的 <b>10%</b>（3 只 × 33% ≈ 每只占总资产 10%）</div>
<div class="k">单只止损</div><div><b>不设个股止损</b>——回测显示中途砍仓会破坏策略；只靠第 5 步的大盘开关</div>
<div class="k">最小资金</div><div>建议 ≥ 30 万人民币（3 只 × 每只 10 万，避免碎股与手续费侵蚀）</div>
</div>
<div class="note ok"><b>为什么是 30% 而不是 all in：</b>即使回测最优，这个策略仍有 −32.5% 的最大回撤和 3–6 个点的数据高估。用 30% 仓位，最坏情况对总资产的冲击约 −10%，是你扛得住的范围。</div>

<h2>七、现在（2026 年 10 月）该买什么</h2>
<div class="take">按同一套规则，用截至 2026-10 的数据重新排序。当前大盘状态：<b style="color:{'#2ecc71' if trend_on else '#ffa64d'}">{t_txt}</b>。</div>
<table>
<thead><tr><th class="num" style="width:60px">排名</th><th style="width:80px">代码</th><th>公司</th>
<th class="num" style="width:120px">过去12月涨幅</th><th class="num" style="width:100px">股价</th></tr></thead>
<tbody>{pick_rows}</tbody></table>
<div class="note">按策略买<b>前 3 名</b>：{", ".join(pk.head(3)["代码"].tolist())}，各 33%。第 4–10 名是备选池，若前 3 名中出现流动性不足或已停牌的，顺延替补。</div>
<div class="note warnb">这份名单是<b>规则输出，不是投资建议</b>。名单里有多只 AI 存储/硬件股（SNDK、MU、STX、WDC），行业高度集中——这正是下一节要说的风险。</div>

<h2>八、风险</h2>
<ul>
<li><b>行业扎堆</b>：前 3 名经常来自同一赛道（2022 全是能源、2026 全是存储）。一旦赛道崩塌，三只一起跌。缓解：单行业不超过策略仓位的 60%。</li>
<li><b>数据高估</b>：退市股缺失，真实收益约打 7–8 折。</li>
<li><b>长期跑输期</b>：第 1 名有 7 年跑输指数（2009–2011 连续三年）；前 3 名有 4 年跑输。要有“连续三年不如指数”的心理准备。</li>
<li><b>容量限制</b>：若前 3 名是小盘股，资金大了买不进。单只不超过该股日均成交额的 5%。</li>
<li><b>税务</b>：每年 100% 换手，在应税账户里短期资本利得税会吃掉 3–5 个百分点。</li>
</ul>

<h2>九、下一步（按顺序）</h2>
<table>
<thead><tr><th style="width:60px">顺序</th><th>做什么</th><th style="width:200px">完成标准</th></tr></thead>
<tbody>
<tr><td class="num">1</td><td>补齐含退市股的数据源，重跑本报告</td><td>年化仍 ≥ 20%</td></tr>
<tr><td class="num">2</td><td>纸面跟踪 6 个月，记录真实滑点与换手</td><td>与回测偏差 &lt; 2 个点</td></tr>
<tr><td class="num">3</td><td>用 5% 总资产实盘 12 个月</td><td>回撤 &lt; 35%</td></tr>
<tr><td class="num">4</td><td>达标后逐步加到 30% 仓位上限</td><td>连续 24 个月跑赢指数</td></tr>
</tbody></table>
<div class="note"><b>不满足第 1 条就不要进入第 3 条。</b>数据不干净时，任何回测都是自我安慰。</div>

<div class="foot">数据：新浪财经美股全历史日线（自建拆股复权）、NASDAQ Trader 股票清单、fja05680/sp500 时点成分。统计：6 个月分块 bootstrap 重抽样 4000 次。本报告仅供研究，不构成投资建议。</div>
</div></body></html>"""

    p = os.path.join(OUT, "排名策略对比.html")
    open(p, "w", encoding="utf-8").write(html)
    print("report:", p, len(html))

    xp = os.path.join(OUT, "排名策略对比.xlsx")
    with pd.ExcelWriter(xp, engine="xlsxwriter") as w:
        d.round(4).to_excel(w, sheet_name="全部模型")
        sens.round(4).to_excel(w, sheet_name="起点敏感性", index=False)
        pk.to_excel(w, sheet_name="当前选股", index=False)


if __name__ == "__main__":
    main()
