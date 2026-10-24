# -*- coding: utf-8 -*-
"""Step 14: 排名策略对比报告（图表 + HTML）"""
import os, json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "output")
IMG = os.path.join(OUT, "img")
os.makedirs(IMG, exist_ok=True)

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 150
plt.rcParams["savefig.bbox"] = "tight"

C_BG, C_GRID, C_TXT = "#0f1420", "#2a3346", "#e6ecf5"
COL = {"M1 只买第1名": "#ff5a5f", "M4 第1+第2": "#ffc857", "M5 第1+第2+第3": "#2ecc71",
       "M2 只买第2名": "#ff7ab6", "M3 只买第3名": "#b07cff", "M6 前10名等权": "#4da3ff",
       "SPY": "#8899aa"}
NAMES = {"M1 只买第1名": "只买第1名", "M2 只买第2名": "只买第2名", "M3 只买第3名": "只买第3名",
         "M4 第1+第2": "第1+第2名", "M5 第1+第2+第3": "第1+2+3名", "M6 前10名等权": "前10名等权",
         "SPY": "标普500"}


def style(ax, title=None, ylab=None):
    ax.set_facecolor(C_BG)
    ax.grid(True, color=C_GRID, lw=0.6, alpha=0.8)
    ax.set_axisbelow(True)
    for s in ax.spines.values():
        s.set_color(C_GRID)
    ax.tick_params(colors=C_TXT, labelsize=9)
    if title:
        ax.set_title(title, color=C_TXT, fontsize=12, pad=10, weight="bold")
    if ylab:
        ax.set_ylabel(ylab, color=C_TXT, fontsize=9)


def save(fig, name):
    p = os.path.join(IMG, name)
    fig.savefig(p, facecolor=C_BG)
    plt.close(fig)
    return p


def b64(p):
    import base64
    return base64.b64encode(open(p, "rb").read()).decode()


def main():
    tb = pd.read_csv(os.path.join(OUT, "monthly_summary_full.csv"))
    tb26 = pd.read_csv(os.path.join(OUT, "monthly_summary.csv"))
    seg = pd.read_csv(os.path.join(OUT, "segment_summary.csv"))
    ann = pd.read_csv(os.path.join(OUT, "compare_annual.csv"))
    curves = np.load(os.path.join(OUT, "curves.npy"), allow_pickle=True).item()
    months = json.load(open(os.path.join(OUT, "months.json")))["months"]
    cut = [i for i, x in enumerate(months) if x <= "2025-12"]

    order = ["M1 只买第1名", "M4 第1+第2", "M5 第1+第2+第3", "M2 只买第2名",
             "M6 前10名等权", "M3 只买第3名", "SPY"]

    # ---- chart 1: equity ----
    fig, ax = plt.subplots(figsize=(11.5, 5.6))
    xs = np.arange(len(cut))
    for k in order:
        v = np.array(curves[k])[cut]
        eq = np.cumprod(1 + v)
        ax.plot(xs, eq, color=COL[k], lw=2.6 if k.startswith(("M1", "M4")) else 1.9,
                label=f"{NAMES[k]}  {eq[-1]:.0f}×")
    ax.set_yscale("log")
    tk = [i for i, x in enumerate(months[:len(cut)]) if x.endswith("-01") and int(x[:4]) % 2 == 1]
    ax.set_xticks(tk)
    ax.set_xticklabels([months[i][:4] for i in tk])
    style(ax, "七个模型净值曲线（2009 年初 = 1，对数刻度）", "净值（倍）")
    ax.legend(facecolor=C_BG, edgecolor=C_GRID, labelcolor=C_TXT, fontsize=9, ncol=2, loc="upper left")
    save(fig, "r1_equity.png")

    # ---- chart 2: segment ----
    fig, ax = plt.subplots(figsize=(11, 4.6))
    segs = seg["区间"].tolist()
    w = 0.8 / len(order)
    for i, k in enumerate(order):
        vals = [seg.loc[seg["区间"] == s, k].iloc[0] * 100 for s in segs]
        pos = np.arange(len(segs)) + (i - len(order) / 2 + 0.5) * w
        ax.bar(pos, vals, w, color=COL[k], label=NAMES[k])
        for p_, v in zip(pos, vals):
            ax.text(p_, v + 1.5, f"{v:.0f}", ha="center", color=C_TXT, fontsize=7)
    ax.set_xticks(np.arange(len(segs)))
    ax.set_xticklabels(segs)
    ax.axhline(0, color=C_TXT, lw=0.8, alpha=0.5)
    style(ax, "分段年化收益：前十年 vs 后八年（%）", "年化 %")
    ax.legend(facecolor=C_BG, edgecolor=C_GRID, labelcolor=C_TXT, fontsize=8, ncol=4)
    save(fig, "r2_segment.png")

    # ---- chart 3: drawdown ----
    fig, ax = plt.subplots(figsize=(11.5, 4.4))
    for k in ["M1 只买第1名", "M4 第1+第2", "M6 前10名等权", "SPY"]:
        v = np.array(curves[k])[cut]
        eq = np.cumprod(1 + v)
        peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
        ax.plot(xs, (eq / peak - 1) * 100, color=COL[k], lw=1.6, label=NAMES[k])
    ax.set_xticks(tk)
    ax.set_xticklabels([months[i][:4] for i in tk])
    ax.fill_between(xs, (np.cumprod(1 + np.array(curves["M1 只买第1名"])[cut]) /
                         np.maximum.accumulate(np.concatenate(
                             [[1.0], np.cumprod(1 + np.array(curves["M1 只买第1名"])[cut])]))[1:] - 1) * 100,
                    0, color=COL["M1 只买第1名"], alpha=0.12)
    style(ax, "水下曲线（相对历史高点回撤 %）", "回撤 %")
    ax.legend(facecolor=C_BG, edgecolor=C_GRID, labelcolor=C_TXT, fontsize=9)
    save(fig, "r3_dd.png")

    imgs = {k: b64(os.path.join(IMG, f)) for k, f in {
        "a": "r1_equity.png", "b": "r2_segment.png", "c": "r3_dd.png"}.items()}

    # ---- tables ----
    def row_of(k):
        r = tb[tb["模型"] == k].iloc[0]
        return r

    def t_rows():
        out = []
        best_yr = tb["年化"].max()
        for k in order:
            r = row_of(k)
            hl = " style='background:#1b2434'" if k in ("M1 只买第1名", "M4 第1+第2") else ""
            out.append(
                f"<tr{hl}><td><b style='color:{COL[k]}'>{NAMES[k]}</b></td>"
                f"<td class='num'>{r['累计倍数']:.0f}×</td>"
                f"<td class='num'><b>{r['年化']*100:.1f}%</b></td>"
                f"<td class='num'>{r['年化波动']*100:.1f}%</td>"
                f"<td class='num' style='color:#ff8a8a'>{r['最大回撤(月)']*100:.1f}%</td>"
                f"<td class='num'>{r['夏普']:.2f}</td>"
                f"<td class='num'>{r['Calmar']:.2f}</td>"
                f"<td class='num'>{(r['年化']-row_of('SPY')['年化'])*100:+.1f}pp</td></tr>")
        return "\n".join(out)

    def seg_rows():
        out = []
        for _, r in seg.iterrows():
            cells = "".join(f"<td class='num'>{r[k]*100:.1f}%</td>" for k in order)
            out.append(f"<tr><td>{r['区间']}</td>{cells}</tr>")
        return "\n".join(out)

    def ann_rows():
        out = []
        cols = ["M1 只买第1名", "M2 只买第2名", "M3 只买第3名", "M4 第1+第2", "M5 第1+第2+第3"]
        for _, r in ann.iterrows():
            cells = ""
            for c in cols:
                v = r[c]
                if pd.isna(v):
                    cells += "<td class='num' style='color:#4b5563'>—</td>"
                else:
                    cells += (f"<td class='num' style=\"color:{'#2ecc71' if v>0 else '#ff5a5f'}\">"
                              f"{v*100:+.0f}%</td>")
            sv = r['SPY']
            sc = "—" if pd.isna(sv) else f"{sv*100:+.0f}%"
            out.append(f"<tr><td>{int(r.year)}</td>{cells}<td class='num'>{sc}</td></tr>")
        return "\n".join(out)

    best = tb.loc[tb["年化"].idxmax()]
    m4 = row_of("M4 第1+第2")
    m5 = row_of("M5 第1+第2+第3")
    m1 = row_of("M1 只买第1名")
    m6 = row_of("M6 前10名等权")
    spy = row_of("SPY")
    seg1 = seg[seg["区间"] == "2009-2018"].iloc[0]
    seg2 = seg[seg["区间"] == "2019-2026"].iloc[0]

    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>前一年涨幅第1/2/3名 —— 七种持有模型对比</title>
<style>
:root{{--bg:{C_BG};--card:#161d2c;--line:{C_GRID};--txt:{C_TXT};--dim:#93a4bd;--gold:#ffc857}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--txt);font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;line-height:1.75}}
.wrap{{max-width:1180px;margin:0 auto;padding:48px 28px 90px}}
h1{{font-size:30px;margin:0 0 6px;letter-spacing:-.4px}}
h2{{font-size:21px;margin:50px 0 14px;padding-left:12px;border-left:4px solid var(--gold)}}
h3{{font-size:15px;margin:24px 0 10px;color:var(--gold)}}
.sub{{color:var(--dim);font-size:13.5px;margin-bottom:28px}}
.hero{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:24px 0 8px}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}}
.kpi .v{{font-size:23px;font-weight:700;color:var(--gold);letter-spacing:-.5px}}
.kpi .l{{font-size:12px;color:var(--dim);margin-top:2px}}
.kpi .s{{font-size:11.5px;color:#6f8199;margin-top:6px}}
table{{width:100%;border-collapse:collapse;font-size:12.8px;margin:12px 0}}
th{{background:#1b2434;color:#aab9cf;font-weight:600;text-align:left;padding:9px 8px;border-bottom:1px solid var(--line)}}
td{{padding:7px 8px;border-bottom:1px solid #1e2637}}
tr:hover td{{background:#182033}}
.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
img{{width:100%;border-radius:12px;border:1px solid var(--line);margin:10px 0 4px;display:block}}
.note{{background:#141b28;border:1px solid var(--line);border-left:3px solid #4da3ff;border-radius:8px;padding:14px 18px;font-size:13px;color:#b6c3d6;margin:16px 0}}
.warn{{border-left-color:#ff5a5f}}
.ok{{border-left-color:#2ecc71}}
.callout{{background:linear-gradient(135deg,#1a2130,#131a26);border:1px solid #2f3b52;border-radius:14px;padding:22px 26px;margin:18px 0}}
.callout b{{color:var(--gold)}}
ul{{padding-left:22px}} li{{margin:6px 0}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
.foot{{color:#6f8199;font-size:11.5px;margin-top:40px;border-top:1px solid var(--line);padding-top:16px}}
</style></head><body><div class="wrap">

<h1>前一年涨幅第 1 / 2 / 3 名：七种持有模型对比</h1>
<div class="sub">2009–2025 共 17 个完整年度 ｜ 每年 1 月 1 日按上一年排名调仓，等权，不计成本 ｜ 标普500 成分股（年内）+ 年初股价 ≥ $5 ｜ 月度净值口径</div>

<div class="hero">
  <div class="kpi"><div class="v">{m1['年化']*100:.1f}%</div><div class="l">只买第 1 名（最高收益）</div><div class="s">{m1['累计倍数']:.0f} 倍 · 回撤 {m1['最大回撤(月)']*100:.0f}%</div></div>
  <div class="kpi"><div class="v">{m4['年化']*100:.1f}%</div><div class="l">第 1+第 2 名（最佳平衡）</div><div class="s">夏普 {m4['夏普']:.2f} · 回撤 {m4['最大回撤(月)']*100:.0f}%</div></div>
  <div class="kpi"><div class="v">{m6['年化']*100:.1f}%</div><div class="l">前 10 名等权（最稳）</div><div class="s">回撤仅 {m6['最大回撤(月)']*100:.0f}%</div></div>
  <div class="kpi"><div class="v">{spy['年化']*100:.1f}%</div><div class="l">标普500 基准</div><div class="s">回撤 {spy['最大回撤(月)']*100:.0f}%</div></div>
</div>

<h2>一、七个模型的完整对比</h2>
<table>
<thead><tr><th>模型</th><th class="num">17年累计</th><th class="num">年化</th><th class="num">年化波动</th>
<th class="num">最大回撤</th><th class="num">夏普</th><th class="num">Calmar</th><th class="num">超额(vs 标普)</th></tr></thead>
<tbody>{t_rows()}</tbody></table>
<div class="note">最大回撤为<b>月度净值</b>口径（用年内月度市值重算），比只看年末数字真实得多。</div>

<img src="data:image/png;base64,{imgs['a']}" alt="净值曲线">

<h2>二、关键发现：第 1 名是彩票，第 2 名才是信号</h2>
<img src="data:image/png;base64,{imgs['b']}" alt="分段对比">
<table>
<thead><tr><th style="width:110px">区间</th><th class="num">只买第1名</th><th class="num">只买第2名</th>
<th class="num">只买第3名</th><th class="num">第1+第2</th><th class="num">第1+2+3</th>
<th class="num">前10等权</th><th class="num">标普500</th></tr></thead>
<tbody>{seg_rows()}</tbody></table>

<div class="callout">
<b>① 只买第 1 名：前十年年化仅 {seg1['M1 只买第1名']*100:.1f}%，后八年 {seg2['M1 只买第1名']*100:.1f}%。</b><br>
整个 33.7% 的年化，<b>几乎全部来自 2019 年之后</b>（AMD→AMD→TSLA→NVDA→PLTR 五连中）。前十年的第 1 名经常是“一次性事件”：2009 年的 AXP、2010 年的 AXP、2014 年的 NFLX、2018 年的 HLT，次年不是回撤就是横盘。
</div>
<div class="callout">
<b>② 只买第 2 名：29.5% → 31.1%，两段几乎一样。</b><br>
这是七个模型里<b>唯一跨周期稳定</b>的单只策略。原因不难理解：年度第 2 名往往是“跑了一整年的趋势股”，而第 1 名常常是“某个月暴涨 3 倍的事件股”。但它的缺点是回撤极大（−60%），因为只有一只股票。
</div>
<div class="callout">
<b>③ 第 3 名：最差的选择。</b><br>
年化只有 16.4%，最大回撤 −63%，夏普 0.38。<b>排名越靠后，越接近“普通股票”，动量信息越弱。</b>第 1 名有彩票属性、第 2 名有趋势属性，第 3 名两头不靠。
</div>

<h2>三、为什么“1+2”比“只买1”更值得做</h2>
<div class="two">
<div class="callout"><b>只买第 1 名</b><br>
年化 {m1['年化']*100:.1f}%、累计 {m1['累计倍数']:.0f} 倍、回撤 {m1['最大回撤(月)']*100:.0f}%、夏普 {m1['夏普']:.2f}。<br>
<b>但前十年只有 {seg1['M1 只买第1名']*100:.1f}%</b>——比标普还低。你要是 2009 年入场，要熬十年才等到 2019 年那波。</div>
<div class="callout"><b>第 1 + 第 2 名等权</b><br>
年化 {m4['年化']*100:.1f}%、累计 {m4['累计倍数']:.0f} 倍、回撤 {m4['最大回撤(月)']*100:.0f}%、夏普 {m4['夏普']:.2f}。<br>
<b>前十年 {seg1['M4 第1+第2']*100:.1f}%、后八年 {seg2['M4 第1+第2']*100:.1f}%</b>——两段都显著跑赢标普，这才是“能拿得住”的模型。</div>
</div>
<div class="note ok"><b>结论：多买一只股票，只牺牲 1.3 个百分点的年化，却把回撤从 −44% 压到 −27%、把前十年的年化从 8.3% 拉到 20.8%。这是全表性价比最高的一次“分散”。</b></div>

<h2>四、逐年收益（%）</h2>
<table>
<thead><tr><th style="width:56px">年份</th><th class="num">只买第1</th><th class="num">只买第2</th>
<th class="num">只买第3</th><th class="num">第1+第2</th><th class="num">第1+2+3</th><th class="num">标普500</th></tr></thead>
<tbody>{ann_rows()}</tbody></table>

<h2>五、水下曲线：你能不能扛住</h2>
<img src="data:image/png;base64,{imgs['c']}" alt="回撤">
<p style="font-size:13.5px;color:#b6c3d6">
只买第 1 名的水下期最长、最深：<b>2010–2011 年连续两年亏损后，直到 2019 年才重新大幅创新高</b>；第 1+第 2 名的回撤曲线平缓得多，最深 −27%，且每次都能在 12–18 个月内修复。前 10 名等权最平（−21%），几乎贴着标普。
</p>

<h2>六、结论：两个模型</h2>
<div class="callout">
<b>🏆 收益率最高：「只买第 1 名」</b>——年化 {m1['年化']*100:.1f}%，17 年 {m1['累计倍数']:.0f} 倍。<br>
但你要接受：回撤 {m1['最大回撤(月)']*100:.0f}%、前十年年化只有 {seg1['M1 只买第1名']*100:.1f}%、收益高度集中在 2019 年后的少数几年。<b>它更像一张彩票，而不是一台机器。</b>
</div>
<div class="callout">
<b>✅ 收益率不错且稳定：「第 1 + 第 2 名等权」</b>——年化 {m4['年化']*100:.1f}%，17 年 {m4['累计倍数']:.0f} 倍，回撤 {m4['最大回撤(月)']*100:.0f}%，夏普 {m4['夏普']:.2f}，<b>且前后两段都跑赢标普 9 个百分点以上</b>。<br>
只有 2 只股票，仍然集中，建议：单只上限 30%、总仓位不超过组合的 30–40%，剩下放宽基。
</div>
<div class="note">如果只想“比标普好一点、但睡得着”：<b>前 10 名等权</b>——年化 {m6['年化']*100:.1f}%（比标普高 {(m6['年化']-spy['年化'])*100:.1f}pp），回撤 {m6['最大回撤(月)']*100:.0f}%（比标普还小），夏普 {m6['夏普']:.2f} vs 标普 {spy['夏普']:.2f}。</div>

<h2>七、必须知道的风险</h2>
<ul>
<li><b>幸存者偏差</b>：股票池只含今天仍能取到行情的票，退市/被并购的缺失。真实结果会比这里低 1–3 个百分点/年。</li>
<li><b>起点偏差</b>：2009 年正好是金融危机底部，任何多头策略都会显得好看。所以我特意做了前后两段拆分——只有「第2名」和「第1+第2」两段都稳。</li>
<li><b>样本太小</b>：17 个年度只有 17 个观测，任何结论的置信区间都很宽。第 1 名策略的 33.7% 年化，去掉 2020 年（AMD +100%）和 2024 年（NVDA +171%）就只剩 22%。</li>
<li><b>没有成本</b>：每年 100% 换手，双边 0.3% 成本约吃掉 0.3–0.5 个百分点/年；若是应税账户，短期资本利得税会再吃掉 3–5 个百分点。</li>
<li><b>排名口径</b>：本报告按“年内曾为标普500成分股 + 年初股价≥$5”统一排名，与媒体榜单有 2 个年份不同（2017 年 HLT 年中入指、2025 年 SNDK 为分拆新股）。</li>
</ul>

<div class="foot">数据：新浪财经美股全历史日线（自建拆股复权）｜ 股票池：6,853 只美股 ｜ 标普500 历史成分：fja05680/sp500 ｜ 本报告仅供研究，不构成投资建议。</div>
</div></body></html>"""

    p = os.path.join(OUT, "排名策略对比.html")
    open(p, "w", encoding="utf-8").write(html)
    print("report:", p, len(html))

    xp = os.path.join(OUT, "排名策略对比.xlsx")
    with pd.ExcelWriter(xp, engine="xlsxwriter") as w:
        t2 = tb.copy()
        for c in ["累计倍数", "年化", "年化波动", "最大回撤(月)", "夏普", "Calmar"]:
            t2[c] = t2[c].round(4)
        t2.to_excel(w, sheet_name="模型对比", index=False)
        ann.to_excel(w, sheet_name="逐年收益", index=False)
        seg.to_excel(w, sheet_name="分段检验", index=False)


if __name__ == "__main__":
    main()
