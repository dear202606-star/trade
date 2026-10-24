# -*- coding: utf-8 -*-
"""Step 8: charts + HTML report + Excel workbook."""
import os, json, base64, io
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "output")
IMG = os.path.join(OUT, "img")
os.makedirs(IMG, exist_ok=True)

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 150
plt.rcParams["savefig.bbox"] = "tight"

C_BG = "#0f1420"
C_GRID = "#2a3346"
C_TXT = "#e6ecf5"
C_RED = "#ff5a5f"
C_GREEN = "#2ecc71"
C_BLUE = "#4da3ff"
C_GOLD = "#ffc857"
C_PINK = "#ff7ab6"


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
    return base64.b64encode(open(p, "rb").read()).decode()


def main():
    ch = pd.read_csv(os.path.join(OUT, "champions_final.csv"))
    ann = pd.read_csv(os.path.join(OUT, "backtest_annual.csv"))
    sm = pd.read_csv(os.path.join(OUT, "backtest_summary.csv"))
    ytd = pd.read_csv(os.path.join(OUT, "ytd2026.csv"))

    # ---- 18 年版「每年买上年冠军」（含 2008 年 NOV 的 -66.7%）----
    m18 = pd.read_csv(os.path.join(OUT, "momentum_single_full.csv"))
    mf = m18[(m18.year <= 2025)].dropna(subset=["ret"]).reset_index(drop=True)
    eq18 = (1 + mf.ret.values).cumprod()
    peak18 = np.maximum.accumulate(np.concatenate([[1.0], eq18]))[1:]
    s18 = {"strategy": "追上年冠军（2008–2025）", "final_multiple": float(eq18[-1]),
           "cagr": float(eq18[-1] ** (1 / len(mf)) - 1),
           "vol": float(mf.ret.std(ddof=1)), "max_dd": float(np.min(eq18 / peak18 - 1)),
           "sharpe": float(mf.ret.mean() / mf.ret.std(ddof=1)), "years": len(mf)}
    sm.loc[sm.strategy == "追上年冠军", ["strategy", "final_multiple", "cagr", "vol",
                                        "max_dd", "sharpe", "years"]] = [
        s18["strategy"], s18["final_multiple"], s18["cagr"], s18["vol"],
        s18["max_dd"], s18["sharpe"], s18["years"]]
    m26 = m18[m18.year == 2026].ret.iloc[0]
    s18["with_2026"] = float(eq18[-1] * (1 + m26))
    print("momentum 2008-2025:", s18)
    st = json.load(open(os.path.join(OUT, "stats_final.json"), encoding="utf-8"))
    pf = json.load(open(os.path.join(OUT, "perfect.json"), encoding="utf-8"))["perfect"]

    # ---------- chart 1: champion vs index by year ----------
    fig, ax = plt.subplots(figsize=(12, 5.2))
    x = np.arange(len(ch))
    w = 0.38
    ax.bar(x - w / 2, ch.ret_published * 100, w, color=C_GOLD, label="年度冠军收益")
    ax.bar(x + w / 2, ch.spx * 100, w, color=C_BLUE, label="标普500")
    ax.set_xticks(x)
    ax.set_xticklabels([f"{int(y)}\n{t}" for y, t in zip(ch.year, ch.ticker)], fontsize=8)
    ax.axhline(0, color=C_TXT, lw=0.8, alpha=0.6)
    style(ax, "2006–2025 每年涨幅第一的股票 vs 标普500（%）", "年度收益 %")
    ax.legend(facecolor=C_BG, edgecolor=C_GRID, labelcolor=C_TXT, fontsize=9)
    for i, v in enumerate(ch.ret_published * 100):
        ax.text(i - w / 2, v + 40, f"{v:.0f}%", ha="center", color=C_GOLD, fontsize=7)
    save(fig, "c1_champions.png")

    # ---------- chart 2: equity curves (two panels) ----------
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.2), gridspec_kw={"width_ratios": [1, 1.25]})
    ax = axes[0]
    eq = (1 + ann["完美预知"].values).cumprod()
    ax.plot(ann.year, eq, "--", color=C_GOLD, lw=2)
    ax.set_yscale("log")
    ax.set_xticks(ann.year[::3])
    style(ax, "完美预知：2009 年初 1 万美元", "净值（倍，对数）")
    ax.annotate(f"${eq[-1]*1e4:,.0f}", (ann.year.iloc[-1], eq[-1]), xytext=(-100, 8),
                textcoords="offset points", color=C_GOLD, fontsize=9, weight="bold")

    ax = axes[1]
    mfin = m18[m18.year <= 2025]
    eq1 = (1 + mfin.ret.values).cumprod()
    ax.plot(mfin.year.values, eq1, "-", color=C_RED, lw=2.6, label="追上年冠军（自 2008）")
    ax.annotate(f"{eq1[-1]:.1f}×", (mfin.year.iloc[-1], eq1[-1]), xytext=(6, 8),
                textcoords="offset points", color=C_RED, fontsize=9, weight="bold")
    for c, col in [("追上年Top10等权", C_GREEN), ("全市场等权", C_PINK), ("标普500", C_BLUE)]:
        eq = (1 + ann[c].values).cumprod()
        ax.plot(ann.year, eq, "-", color=col, lw=2.2, label=c + "（自 2009）")
        ax.annotate(f"{eq[-1]:.1f}×", (ann.year.iloc[-1], eq[-1]),
                    xytext=(6, {"全市场等权": -11, "标普500": 6}.get(c, 0)),
                    textcoords="offset points", color=col, fontsize=9, weight="bold")
    ax.set_yscale("log")
    ax.set_xticks(range(2008, 2027, 2))
    ax.set_xlim(2007.6, 2026.4)
    style(ax, "规则策略（各自起点 = 1）", "净值（倍，对数）")
    ax.legend(facecolor=C_BG, edgecolor=C_GRID, labelcolor=C_TXT, fontsize=8.5, loc="upper left")
    fig.suptitle("净值曲线对比：完美预知 vs 规则策略", color=C_TXT, fontsize=13, weight="bold", y=1.02)
    save(fig, "c2_equity.png")

    # ---------- chart 3: distribution ----------
    d3 = ch[ch.year >= 2009].reset_index(drop=True)
    fig, ax = plt.subplots(figsize=(12, 5))
    xs = np.arange(len(d3))
    med = [st["median_stock_ret_by_year"][str(int(y))] * 100 for y in d3.year]
    ax.bar(xs + 0.2, d3.ret_published * 100, 0.36, color=C_GOLD, label="年度冠军")
    ax.bar(xs - 0.2, med, 0.36, color=C_BLUE, label="全市场中位数个股")
    ax.set_xticks(xs)
    ax.set_xticklabels([int(y) for y in d3.year], fontsize=9)
    ax.set_yscale("symlog", linthresh=10)
    ax.axhline(0, color=C_TXT, lw=0.8, alpha=0.6)
    ax.set_ylim(-30, 1500)
    style(ax, "年度冠军 vs 全市场中位数个股（%，symlog 刻度）", "年度收益 %")
    ax.legend(facecolor=C_BG, edgecolor=C_GRID, labelcolor=C_TXT, fontsize=9, loc="upper left")
    save(fig, "c3_dist.png")

    # ---------- chart 4: champion this year vs next year ----------
    d = ch.dropna(subset=["ret_next"])
    fig, ax = plt.subplots(figsize=(7.4, 5.6))
    ax.scatter(d.ret_published * 100, d.ret_next * 100, s=70, color=C_GOLD, edgecolor=C_TXT, zorder=3)
    for _, r in d.iterrows():
        ax.annotate(f"{r.ticker}{int(r.year)}", (r.ret_published * 100, r.ret_next * 100),
                    textcoords="offset points", xytext=(6, 4), fontsize=7, color=C_TXT)
    ax.axhline(0, color=C_TXT, lw=0.7, alpha=0.5)
    ax.axvline(0, color=C_TXT, lw=0.7, alpha=0.5)
    style(ax, "冠军的“下一年”：今年涨幅 vs 次年涨幅（%）", "次年收益 %")
    ax.set_xlabel("当年收益 %", color=C_TXT, fontsize=9)
    save(fig, "c4_next.png")

    imgs = {k: b64(os.path.join(IMG, f)) for k, f in {
        "c1": "c1_champions.png", "c2": "c2_equity.png",
        "c3": "c3_dist.png", "c4": "c4_next.png"}.items()}

    # ---------- tables ----------
    def champ_rows():
        out = []
        for _, r in ch.iterrows():
            dd = "" if pd.isna(r.intra_dd) else f"{r.intra_dd*100:.0f}%"
            out.append(
                f"<tr><td class='y'>{int(r.year)}</td><td class='tk'>{r.ticker}</td>"
                f"<td>{r['name']}</td><td class='sector'>{r.sector}</td>"
                f"<td class='num' style='color:#ffc857'>{r.ret_published*100:,.0f}%</td>"
                f"<td class='num'>{r.spx*100:+.1f}%</td>"
                f"<td class='num' style='color:#8fa3bf'>{dd}</td>"
                f"<td class='why'>{r.driver}</td></tr>")
        return "\n".join(out)

    def st_rows():
        out = []
        for _, r in sm.iterrows():
            if r.strategy == "完美预知":
                continue
            out.append(
                f"<tr><td>{r.strategy}</td><td class='num'>{r.final_multiple:,.1f}×</td>"
                f"<td class='num'>{r.cagr*100:.1f}%</td><td class='num'>{r.vol*100:.1f}%</td>"
                f"<td class='num'>{r.max_dd*100:.1f}%</td><td class='num'>{r.sharpe:.2f}</td></tr>")
        return "\n".join(out)

    def ann_rows():
        d = ann.copy()
        d["追上年冠军"] = [m18.loc[m18.year == y, "ret"].iloc[0] if (m18.year == y).any() else np.nan
                        for y in d.year]
        pre = pd.DataFrame([{"year": 2008, "完美预知": np.nan, "追上年冠军": -0.6673,
                             "追上年Top10等权": np.nan, "标普500": -0.3828}])
        d = pd.concat([pre, d], ignore_index=True)
        out = []
        for _, r in d.iterrows():
            def f(v):
                return "<td class='num' style='color:#4b5563'>—</td>" if pd.isna(v) else \
                    f"<td class='num' style=\"color:{C_GREEN if v>0 else C_RED}\">{v*100:+.1f}%</td>"
            tag = " <span style='color:#ff5a5f;font-size:11px'>首年</span>" if int(r.year) == 2008 else ""
            out.append(f"<tr><td>{int(r.year)}{tag}</td>{f(r['完美预知'])}{f(r['追上年冠军'])}"
                       f"{f(r['追上年Top10等权'])}{f(r['标普500'])}</tr>")
        return "\n".join(out)

    def ytd_rows():
        out = []
        for _, r in ytd[ytd.in_sp].head(8).iterrows():
            out.append(f"<tr><td class='tk'>{r.ticker}</td><td>{str(r['name'])[:38]}</td>"
                       f"<td class='num' style='color:{C_GREEN}'>{r.ret*100:+.1f}%</td></tr>")
        return "\n".join(out)

    worst = ch.loc[ch.intra_dd.idxmin()]
    pf_final = pf["final_10k"]
    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>近20年美股年度冠军股研究</title>
<style>
:root{{--bg:{C_BG};--card:#161d2c;--line:{C_GRID};--txt:{C_TXT};--dim:#93a4bd;--gold:{C_GOLD}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--txt);font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;line-height:1.75}}
.wrap{{max-width:1180px;margin:0 auto;padding:48px 28px 90px}}
h1{{font-size:31px;margin:0 0 6px;letter-spacing:-.4px}}
h2{{font-size:21px;margin:52px 0 14px;padding-left:12px;border-left:4px solid var(--gold)}}
h3{{font-size:15px;margin:26px 0 10px;color:var(--gold)}}
.sub{{color:var(--dim);font-size:13.5px;margin-bottom:30px}}
.hero{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:26px 0 8px}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}}
.kpi .v{{font-size:24px;font-weight:700;color:var(--gold);letter-spacing:-.5px}}
.kpi .l{{font-size:12px;color:var(--dim);margin-top:2px}}
.kpi .s{{font-size:11.5px;color:#6f8199;margin-top:6px}}
table{{width:100%;border-collapse:collapse;font-size:12.6px;margin:12px 0}}
th{{background:#1b2434;color:#aab9cf;font-weight:600;text-align:left;padding:9px 8px;border-bottom:1px solid var(--line);position:sticky;top:0}}
td{{padding:7px 8px;border-bottom:1px solid #1e2637;vertical-align:top}}
tr:hover td{{background:#182033}}
.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.tk{{font-weight:700;color:var(--gold);font-family:Consolas,monospace}}
.y{{color:var(--dim);font-family:Consolas,monospace}}
.why{{color:#a8b6cb;font-size:12px}}
.sector{{color:#7f92ab;font-size:12px;white-space:nowrap}}
img{{width:100%;border-radius:12px;border:1px solid var(--line);margin:10px 0 4px;display:block}}
.note{{background:#141b28;border:1px solid var(--line);border-left:3px solid {C_BLUE};border-radius:8px;padding:14px 18px;font-size:13px;color:#b6c3d6;margin:16px 0}}
.warn{{border-left-color:{C_RED}}}
.callout{{background:linear-gradient(135deg,#1a2130,#131a26);border:1px solid #2f3b52;border-radius:14px;padding:22px 26px;margin:18px 0}}
.callout b{{color:var(--gold)}}
ul{{padding-left:22px}} li{{margin:6px 0}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
.foot{{color:#6f8199;font-size:11.5px;margin-top:40px;border-top:1px solid var(--line);padding-top:16px}}
code{{background:#1b2434;padding:1px 5px;border-radius:4px;font-size:12px;color:#9fd0ff}}
</style></head><body><div class="wrap">

<h1>近 20 年美股「年度涨幅冠军」研究</h1>
<div class="sub">2006–2025 · 标普500口径 · 数据截至 2026-10-23 ｜ 复权价口径（不含股息）｜ 本地全样本回测</div>

<div class="hero">
  <div class="kpi"><div class="v">20 / 20</div><div class="l">冠军每年都跑赢指数</div><div class="s">中位超额 +136 个百分点</div></div>
  <div class="kpi"><div class="v">{ch.ret_published.median()*100:,.0f}%</div><div class="l">冠军收益中位数</div><div class="s">标普500 仅 {ch.spx.median()*100:+.1f}%</div></div>
  <div class="kpi"><div class="v">${pf_final/1e12:,.1f}万亿</div><div class="l">完美预知：1万美元终值</div><div class="s">1,763,861,773 倍 · CAGR 190%</div></div>
  <div class="kpi"><div class="v">27.6%</div><div class="l">“追上年冠军”年化（18年）</div><div class="s">首年 −66.7%，波动 63%</div></div>
</div>

<h2>一、结论先说</h2>
<div class="callout">
<b>① 每年买中冠军，20 年能把 1 万美元变成 17.6 万亿美元</b>——年化 190%，累计 17.6 亿倍。这是数学上的上界，不是可实现的策略：它要求你在每年 1 月 1 日就知道当年谁涨最多。<br><br>
<b>② 但你并不需要“完美预知”才能赚钱，你需要的是“不猜”</b>——同一套数据里，每年买上年冠军（1 只满仓）18 年年化 27.6%，但<b>第一年（2008 年买入 2007 冠军 NOV）就亏了 66.7%</b>；改成买上年 Top10 等权，年化 19.9%、波动只有 19.1%、回撤 −11.3%。<b>单押冠军的夏普只有 0.66，分散后是 1.12。</b><br><br>
<b>③ 冠军的代价是回撤</b>——20 只冠军在夺冠当年，年内最大回撤中位数 <b>{st['median_intra_dd']*100:.0f}%</b>，最惨的一只（{worst.ticker} {int(worst.year)}）年内一度 <b>{worst.intra_dd*100:.0f}%</b>。你就算提前知道答案，也未必拿得住。
</div>

<h2>二、2006–2025：每年涨幅第一的股票</h2>
<div class="note">口径：当年标普500成分股中年度价格涨幅第一名（媒体通用口径）。「年内回撤」为该股在夺冠当年的最大日内/月末回撤。表中 17 个可复算年份，本地复权数据与公开涨幅的误差均在 8 个百分点以内（详见文末「数据与方法」）。</div>
<table>
<thead><tr><th style="width:52px">年份</th><th style="width:62px">代码</th><th style="width:150px">公司</th><th style="width:78px">行业</th><th class="num" style="width:78px">当年涨幅</th><th class="num" style="width:74px">标普500</th><th class="num" style="width:74px">年内回撤</th><th>为什么涨</th></tr></thead>
<tbody>
{champ_rows()}
</tbody></table>

<h3>规律</h3>
<ul>
<li><b>半导体是最强赛道（5 次）</b>：NVDA 2016/2023/2024、AMD 2018/2019。AI 与算力周期一旦启动，龙头可以连续两年夺冠（NVDA 2023→2024）。</li>
<li><b>能源是熊市冠军（4 次）</b>：NOV 2007、COG 2011、DVN 2021、OXY 2022。2022 年标普500 前十名<b>全部是能源股</b>。</li>
<li><b>熊市冠军靠“逆周期”</b>：2008 是折扣零售 FDO（+27%，指数 −38%），2022 是油气 OXY（+119%，指数 −19%）。</li>
<li><b>“一年三倍”是常态，不是意外</b>：20 年里 11 年冠军涨幅超过 130%，5 年超过 240%。</li>
</ul>

<h2>三、完美预知：把 1 万美元变成 17.6 万亿美元</h2>
<div class="note">假设：2006 年初投入 1 万美元，每年 1 月 1 日全额买入当年冠军，12 月 31 日卖出并切换下一年，不计税费与滑点。</div>
<div class="hero" style="grid-template-columns:repeat(3,1fr)">
  <div class="kpi"><div class="v">${pf_final:,.0f}</div><div class="l">20 年终值</div><div class="s">起点 $10,000</div></div>
  <div class="kpi"><div class="v">{pf['final_multiple']:,.0f}×</div><div class="l">累计倍数</div><div class="s">≈ 1.76 亿倍</div></div>
  <div class="kpi"><div class="v">{pf['cagr']*100:.1f}%</div><div class="l">年化收益</div><div class="s">20 年，无一年亏损</div></div>
</div>
<img src="data:image/png;base64,{imgs['c1']}" alt="年度冠军收益">
<div class="note warn">这个数字的意义不是“目标”，而是<b>标尺</b>：它告诉你“选股能力”的理论上限有多高，也告诉你为什么市场上所有“每年翻倍”的课程都是骗局——真正的上限是每年 190%，而做到 15% 就已经是世界级。</div>

<h2>四、现实版：每年买入上一年冠军</h2>
<div class="note">规则：每年 1 月 1 日，全仓买入<b>上一年涨幅第一</b>的那只股票，持有满 12 个月，次年同日换成新的冠军。不预测、不择时、不止损。2008–2025 共 18 个完整年度，不计交易成本与税费。</div>
<div class="hero" style="grid-template-columns:repeat(4,1fr)">
  <div class="kpi"><div class="v">80.6×</div><div class="l">18 年累计（1 万 → 80.6 万）</div><div class="s">含 2026 年初至今为 420×</div></div>
  <div class="kpi"><div class="v">27.6%</div><div class="l">年化收益</div><div class="s">标普500 同期 12.6%</div></div>
  <div class="kpi"><div class="v">−66.7%</div><div class="l">第一年（2008）</div><div class="s">买了 2007 冠军 NOV</div></div>
  <div class="kpi"><div class="v">61%</div><div class="l">18 年里亏损年份占比</div><div class="s">11/18 年跑赢指数</div></div>
</div>
<div class="note warn"><b>最关键的一件事：这条策略的第一年就腰斩。</b>2007 年冠军是油气设备商 NOV（+128%），你在 2008 年 1 月满仓买入，然后金融危机爆发，年底剩三分之一（−66.7%）。<b>80 倍的终值，是从 0.33 起步的。</b>绝大多数人在这里就出局了。</div>
<table>
<thead><tr><th>策略</th><th class="num">累计</th><th class="num">年化</th><th class="num">年化波动</th><th class="num">最大回撤</th><th class="num">夏普</th></tr></thead>
<tbody>{st_rows()}</tbody></table>
<div class="note">注：第一行「追上年冠军」为 2008–2025（18 年），其余为 2009–2025（17 年）——因为 2007 年的成分股排名需要 2006 年数据，免费数据源在该年有断层。</div>
<img src="data:image/png;base64,{imgs['c2']}" alt="净值曲线">
<div class="two">
<div class="callout"><b>只买 1 只（上年冠军）</b><br>80.6 倍、年化 27.6%，18 年跑赢指数 2 倍多——但波动 63%、首年 −66.7%、2011 年 −19.7%、2023 年 −5.2%。它的高收益集中在 2019–2025 的 AMD→AMD→TSLA→NVDA→PLTR 连庄，本质是<b>运气集中在最后几年</b>。</div>
<div class="callout"><b>买上年 Top10（10 只等权）</b><br>21.9 倍、年化 19.9%，但波动只有 19.1%、回撤 −11.3%、夏普 1.12——<b>全表风险调整后最优</b>。同样是“买赢家”，分散之后从赌博变成了策略。</div>
</div>

<h3>逐年收益（%）</h3>
<table>
<thead><tr><th style="width:64px">年份</th><th class="num">完美预知</th><th class="num">追上年冠军</th><th class="num">追上年Top10</th><th class="num">标普500</th></tr></thead>
<tbody>{ann_rows()}</tbody></table>

<h2>五、为什么“每年抓冠军”注定做不到</h2>
<h3>1. 冠军和中位数个股差着一个数量级</h3>
<img src="data:image/png;base64,{imgs['c3']}" alt="分布">
<p style="font-size:13.5px;color:#b6c3d6">任何一年，全市场个股收益中位数只有 5%–30%，而冠军是 100%–750%。你要找的不是“好公司”，而是<b>当年全市场 3000 只股票里的唯一最大值</b>——这是抽样分布的极端值，不可预测。</p>

<h3>2. 冠军的“下一年”并不稳定</h3>
<img src="data:image/png;base64,{imgs['c4']}" alt="次年表现">
<p style="font-size:13.5px;color:#b6c3d6">17 个可跟踪样本里，冠军次年收益均值 {st['champ_next_mean']*100:+.0f}%、中位数 {st['champ_next_median']*100:+.0f}%，{st['champ_next_beat_spx']}/17 跑赢指数——<b>看起来动量有效，但分布极宽</b>：NVDA 次年 +171%、AMD 次年 +148%，而 CMI 次年 −20%、NFLX 2014 年 −7%。用 1 只股票去押，等于把结果交给运气。</p>

<h3>3. 持有过程极其反人性</h3>
<div class="note warn">20 只冠军在夺冠当年的<b>年内最大回撤中位数 −{abs(st['median_intra_dd'])*100:.0f}%</b>，最深的 {worst.ticker} {int(worst.year)} 达到 <b>{worst.intra_dd*100:.0f}%</b>。也就是说：即使有人把答案提前告诉你，你也要在浮亏一半以上时不割肉，才能拿到那个年度收益。</div>

<h2>六、那每年到底该怎么投？</h2>
<div class="callout">
<b>把“猜冠军”换成“跟上赢家 + 强制分散 + 年度再平衡”三件事。</b>
</div>
<h3>可执行的年度流程（每年 12 月最后一个交易周做一次）</h3>
<ul>
<li><b>第一步｜取数</b>：拉出标普500（或罗素1000）全部成分股过去 12 个月的涨幅，剔除市值过小、成交额过低、上市不足 12 个月的股票。</li>
<li><b>第二步｜排序取前 10</b>：不要只买第 1 名——第 1 名是极端值，单押时波动 63%；<b>前 10 名等权，波动立刻降到 19%，夏普从 0.66 升到 1.12</b>。</li>
<li><b>第三步｜等权买入，单只上限 10%–15%</b>：避免任何一只股票贡献超过 15% 的仓位。</li>
<li><b>第四步｜持有一整年，不做中途止损</b>：动量策略的收益来自“让赢家继续跑”，中途因为回撤砍掉会破坏统计优势。</li>
<li><b>第五步｜次年同一天全部换仓</b>：一年一次，换手 100%，交易成本约 0.1%–0.3%，对 20% 的年化不构成致命影响。</li>
<li><b>第六步｜用 60%–80% 仓位做这件事，剩下 20%–40% 放宽基</b>：把“追冠军”当成卫星仓位，而不是全部身家。</li>
</ul>

<h3>三个必须知道的坑</h3>
<ul>
<li><b>幸存者偏差</b>：本次回测的股票池只包含“今天还能查到行情”的股票，退市/被并购的股票不在内。真实世界里，这会高估动量策略 1–3 个百分点/年。</li>
<li><b>冠军会反转</b>：单只冠军次年中位数 +31%，但仍有 6/17 跑输指数；<b>只买 1 只 = 押注尾部</b>。</li>
<li><b>风格集中</b>：2022 年前十全是能源、2023–2024 全是 AI 算力——Top10 策略本质是“押当年最强风格”，一旦风格切换（2023 年能源崩塌）会整组回撤。</li>
</ul>

<h2>七、2026 年进行时</h2>
<table><thead><tr><th style="width:80px">代码</th><th>公司</th><th class="num" style="width:110px">年初至今</th></tr></thead>
<tbody>{ytd_rows()}</tbody></table>
<div class="note">截至 2026-10-23。今年 AI 存储/硬件链条继续领跑（SNDK、MU、DELL），同时 MRNA、DD 属于深度困境反转。<b>再次印证：冠军几乎总是“当年最强叙事 + 最强业绩弹性”的交集，而不是去年最强的那个。</b></div>

<h2>八、数据与方法</h2>
<ul>
<li><b>样本</b>：6,853 只美股（NASDAQ/NYSE/AMEX 普通股，剔除 ETF），本地复权月度面板 90 万行。</li>
<li><b>价格</b>：新浪财经美股全历史日线，自建拆股复权（正向/反向拆股按除权日开盘价判定，累计因子校验）。已用 TSLA 2020（+743%）、NVDA 2023（+239%）、NFLX 2013（+298%）等公开值交叉校验。</li>
<li><b>冠军口径</b>：标普500 点内成分股（年初在册）年度涨幅第一；公开数据交叉验证，17 个可复算年份误差均 &lt; 8 个百分点。</li>
<li><b>回测口径</b>：每年 1 月 1 日调仓、等权、不计交易成本与税费、复权价（不含股息）。标普500 基准用 SPY 价格回报。</li>
<li><b>已知局限</b>：①免费数据在 2005–2006 年存在断层，2006–2007 年冠军采用公开数据；②退市股票缺失，存在幸存者偏差；③不含股息，高股息策略会被低估 1.5–2 个百分点/年；④分拆（spin-off）未复权，个别年份（如 MO 2008）会低估。</li>
</ul>

<div class="foot">数据来源：新浪财经（价格）、NASDAQ Trader（股票清单）、fja05680/sp500（标普500 历史成分）、公开媒体交叉验证（CNBC / Fortune / Kiplinger / Nasdaq / Motley Fool 等）。本报告仅供研究，不构成投资建议。</div>
</div></body></html>"""

    p = os.path.join(OUT, "report.html")
    open(p, "w", encoding="utf-8").write(html)
    print("report:", p, len(html))

    # ---------- excel ----------
    xp = os.path.join(OUT, "美股年度冠军研究.xlsx")
    PCT = ["当年涨幅", "标普500", "超额", "年内最大回撤", "次年涨幅"]
    ct = ch.rename(columns={"year": "年份", "ticker": "代码", "name": "公司", "sector": "行业",
                            "ret_published": "当年涨幅", "spx": "标普500", "excess": "超额",
                            "driver": "上涨原因", "intra_dd": "年内最大回撤",
                            "ret_next": "次年涨幅"})[
        ["年份", "代码", "公司", "行业", "当年涨幅", "标普500", "超额", "年内最大回撤",
         "次年涨幅", "上涨原因"]].copy()
    for c in PCT:
        ct[c] = (ct[c] * 100).round(1)
    ct.columns = [c + "(%)" if c in PCT else c for c in ct.columns]

    a2 = ann.copy()
    a2["追上年冠军"] = [m18.loc[m18.year == y, "ret"].iloc[0] if (m18.year == y).any() else np.nan
                    for y in a2.year]
    a2 = pd.concat([pd.DataFrame([{"year": 2008, "完美预知": np.nan, "追上年冠军": -0.6673,
                                   "追上年Top10等权": np.nan, "全市场等权": np.nan,
                                   "标普500": -0.3828}]), a2], ignore_index=True)
    for c in a2.columns[1:]:
        a2[c] = (a2[c] * 100).round(1)
    a2.columns = [c + "(%)" if i > 0 else c for i, c in enumerate(a2.columns)]

    s2 = sm.copy()
    for c in ["final_multiple", "cagr", "vol", "max_dd", "best", "worst", "sharpe"]:
        s2[c] = s2[c].round(4)
    s2.columns = ["策略", "年数", "累计倍数", "年化", "年化波动", "最大回撤", "最好年份",
                  "最差年份", "正收益年数", "夏普"]

    y2 = ytd.head(50).copy()
    y2["ret"] = (y2["ret"] * 100).round(1)

    with pd.ExcelWriter(xp, engine="xlsxwriter") as w:
        ct.to_excel(w, sheet_name="年度冠军", index=False)
        a2.to_excel(w, sheet_name="逐年收益", index=False)
        s2.to_excel(w, sheet_name="策略汇总", index=False)
        y2.to_excel(w, sheet_name="2026YTD", index=False)
        bk = w.book
        for i, sh in enumerate(w.sheets.values()):
            pd.DataFrame({"": []})
            sh.set_column(0, 30, 14)
            sh.freeze_panes(1, 0)
        w.sheets["年度冠军"].set_column(8, 8, 60)
        w.sheets["年度冠军"].set_column(2, 2, 24)
        w.sheets["年度冠军"].set_column(3, 3, 18)
        w.sheets["策略汇总"].set_column(0, 0, 20)
        w.sheets["2026YTD"].set_column(2, 2, 40)


if __name__ == "__main__":
    main()
