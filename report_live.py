# -*- coding: utf-8 -*-
"""report_live.py — 生成报告里的「本期该买什么」动态区块"""


def render(rec):
    if not rec:
        return '<div class="note">暂无信号数据。</div>'
    month = rec.get("month", "")
    trend = rec.get("trend", "")
    exp = rec.get("exposure", 1.0)
    spy = rec.get("spy", 0)
    ma = rec.get("ma", 0)
    official = rec.get("official") or rec.get("picks") or []
    bench = rec.get("bench") or []
    action = rec.get("action", "")

    ok = trend == "满仓"
    color = "#2ecc71" if ok else "#ffab6b"
    state = f"{trend}（SPY {spy:,.0f} {'≥' if ok else '<'} 10 月均线 {ma:,.0f}）"

    rows = []
    for p in official:
        rows.append(
            f"<tr><td class='num'>{p['rank']}</td><td class='tk'>{p['ticker']}</td>"
            f"<td>{p['name']}</td><td>{p['sector']}</td>"
            f"<td class='num' style='color:#2ecc71'>+{p['ret12']*100:.0f}%</td>"
            f"<td class='num'>{p['weight']*100:.0f}%</td>"
            f"<td class='num'><b>{p['weight']*exp*0.30*100:.1f}%</b></td></tr>")

    brow = []
    for p in bench[:6]:
        brow.append(f"<tr><td class='num'>{p['rank']}</td><td class='tk'>{p['ticker']}</td>"
                    f"<td>{p['sector']}</td>"
                    f"<td class='num' style='color:#2ecc71'>+{p['ret12']*100:.0f}%</td></tr>")

    return f"""
<div class="take">数据截至 <b>{month}</b>。大盘状态：<b style="color:{color}">{state}</b>，目标仓位 {exp*100:.0f}%。</div>
<table>
<thead><tr><th class="num" style="width:50px">#</th><th style="width:80px">代码</th><th>公司</th>
<th style="width:150px">行业</th><th class="num" style="width:100px">基准涨幅</th>
<th class="num" style="width:100px">策略内权重</th><th class="num" style="width:100px">占总资产</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<div class="note"><b>本月动作：</b>{action}</div>
<div class="note">正式持仓按 <b>{rec.get('official_month', '')}</b> 的排名锁定，本年度有效；每年 12 月最后一个交易日再按当年排名换仓。下表是当月排名，仅供观察，不据此换仓。</div>
<table>
<thead><tr><th class="num" style="width:50px">#</th><th style="width:80px">代码</th>
<th>行业</th><th class="num" style="width:120px">过去12月涨幅</th></tr></thead>
<tbody>{''.join(brow)}</tbody></table>
"""
