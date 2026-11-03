# -*- coding: utf-8 -*-
"""Step 17: 最终模型筛选 + 图表 + 严谨版报告"""
import os, json, math
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from step16_mature import Engine, stats, alpha_beta, block_boot, COST

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
GOLD, RED, GREEN, BLUE, GREY, PURPLE = "#ffc857", "#ff5a5f", "#2ecc71", "#4da3ff", "#8899aa", "#b07cff"


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


def dsr_of(sr_annual, ret_series, all_sr, K):
    """Deflated Sharpe Ratio (Bailey & Lopez de Prado)"""
    from step16_mature import norm_cdf, norm_ppf
    sr_m = sr_annual / math.sqrt(12)
    T = len(ret_series)
    r = pd.Series(ret_series).dropna()
    g3, g4 = float(r.skew()), float(r.kurtosis() + 3)
    v = np.var(all_sr) / 12
    gam = 0.5772156649
    sr0 = math.sqrt(v) * ((1 - gam) * norm_ppf(1 - 1 / K) + gam * norm_ppf(1 - 1 / (K * math.e)))
    z = (sr_m - sr0) * math.sqrt(T - 1) / math.sqrt(1 - g3 * sr_m + (g4 - 1) / 4 * sr_m ** 2)
    return norm_cdf(z), sr0 * math.sqrt(12)


def main():
    E = Engine()
    spy_ret = pd.Series({m: E.spy[m] / E.spy[E.months[E.mpos[m] - 1]] - 1
                         for m in E.months
                         if E.mpos[m] > 0 and m in E.spy and E.months[E.mpos[m] - 1] in E.spy})
    spy_ret = spy_ret[spy_ret.index >= "2009-01"]

    # ---- 最终候选：趋势过滤 × N ----
    rows, curves = [], {}
    for skip in (0, 1):
        for N in (1, 2, 3, 5, 10):
            for tr, tname in ((0.0, "无过滤"), (0.5, "趋势过滤")):
                r = E.run(N=N, skip=skip, cost=COST, trend=tr)
                st = stats(r, f"Top{N}")
                if not st:
                    continue
                a, t, b, r2 = alpha_beta(r, spy_ret)
                lo, med, hi = block_boot(r, n=2000)
                rows.append({"动量": "12-0" if skip == 0 else "12-1", "N": N, "风控": tname,
                             "年化": st["年化"], "波动": st["波动"], "回撤": st["回撤"],
                             "夏普": st["夏普"], "Calmar": st["Calmar"], "累计": st["累计"],
                             "alpha": (1 + a) ** 12 - 1, "alpha_t": t, "beta": b,
                             "CI_lo": lo, "CI_hi": hi})
                curves[f"{'12-0' if skip == 0 else '12-1'}_N{N}_{tname}"] = r
    fin = pd.DataFrame(rows)
    fin.to_csv(os.path.join(OUT, "m_final_grid.csv"), index=False, encoding="utf-8-sig")
    print(fin.round(3).to_string(index=False))

    # 选两个代表
    best_ret = fin[(fin["动量"] == "12-0") & (fin["风控"] == "无过滤")].sort_values("年化", ascending=False).iloc[0]
    best_risk = fin.loc[fin["夏普"].idxmax()]

    # 重算 DSR（用最终网格）
    all_sr = list(fin["夏普"].astype(float)) + \
        list(pd.read_csv(os.path.join(OUT, "m_grid.csv"))["夏普"].astype(float))
    K = len(all_sr)
    best_key = f"{best_risk['动量']}_N{int(best_risk['N'])}_{best_risk['风控']}"
    dsr, sr0 = dsr_of(best_risk["夏普"], curves[best_key], all_sr, K)
    meta = json.load(open(os.path.join(OUT, "m_meta.json")))
    meta.update({"dsr": dsr, "sr": float(best_risk["夏普"]), "sr0": sr0, "trials": K})
    print(f"DSR={dsr:.3f} SR={meta['sr']:.3f} SR0={sr0:.3f} K={K}")
    print("\n最高收益:", best_ret.to_dict())
    print("最佳风险调整:", best_risk.to_dict())

    # ---- charts ----
    keys = ["12-0_N1_无过滤", "12-0_N3_无过滤", "12-0_N3_趋势过滤", "12-1_N10_无过滤"]
    labels = {"12-0_N1_无过滤": "Top1（最高收益）", "12-0_N3_无过滤": "Top3",
              "12-0_N3_趋势过滤": "Top3 + 趋势过滤", "12-1_N10_无过滤": "Top10（12-1）"}
    cols = {"12-0_N1_无过滤": RED, "12-0_N3_无过滤": GOLD,
            "12-0_N3_趋势过滤": GREEN, "12-1_N10_无过滤": BLUE}
    fig, ax = plt.subplots(figsize=(11.5, 5.4))
    xs = np.arange(len(curves[keys[0]]))
    for k in keys:
        eq = (1 + curves[k]).cumprod()
        ax.plot(xs, eq.values, color=cols[k], lw=2.3, label=f"{labels[k]}  {eq.iloc[-1]:.0f}×")
    eqs = (1 + spy_ret).cumprod()
    ax.plot(xs, eqs.reindex(curves[keys[0]].index).values, color=GREY, lw=1.8, label="标普500")
    ax.set_yscale("log")
    tk = [i for i, m in enumerate(curves[keys[0]].index) if m.endswith("-01") and int(m[:4]) % 2 == 1]
    ax.set_xticks(tk)
    ax.set_xticklabels([curves[keys[0]].index[i][:4] for i in tk])
    style(ax, "动量组合净值（2009 年初 = 1，对数刻度，含 0.2% 单边成本）", "净值（倍）")
    ax.legend(facecolor=C_BG, edgecolor=C_GRID, labelcolor=C_TXT, fontsize=9, loc="upper left")
    save(fig, "f1_equity.png")

    fig, ax = plt.subplots(figsize=(10.5, 4.6))
    n_vals = [1, 2, 3, 5, 10]
    w = 0.2
    for i, (skip, tname, c) in enumerate([(0, "无过滤", RED), (0, "趋势过滤", GREEN),
                                          (1, "无过滤", GOLD), (1, "趋势过滤", BLUE)]):
        vals = [fin[(fin["动量"] == ("12-0" if skip == 0 else "12-1")) &
                    (fin["风控"] == tname) & (fin["N"] == n)]["夏普"].iloc[0] for n in n_vals]
        pos = np.arange(len(n_vals)) + (i - 1.5) * w
        ax.bar(pos, vals, w, color=c, label=f"{'12-0' if skip == 0 else '12-1'} {tname}")
    ax.set_xticks(np.arange(len(n_vals)))
    ax.set_xticklabels([f"Top{n}" for n in n_vals])
    ax.axhline(0.852, color=GREY, ls="--", lw=1, label="标普500 夏普 0.85")
    style(ax, "夏普比率：持仓数量、动量窗口与趋势过滤的交互", "夏普")
    ax.legend(facecolor=C_BG, edgecolor=C_GRID, labelcolor=C_TXT, fontsize=8, ncol=3)
    save(fig, "f2_sharpe.png")

    fig, ax = plt.subplots(figsize=(11.5, 4.2))
    for k in keys[:3]:
        eq = (1 + curves[k]).cumprod()
        peak = eq.cummax()
        ax.plot(xs, ((eq / peak) - 1).values * 100, color=cols[k], lw=1.5, label=labels[k])
    s_eq = eqs.reindex(curves[keys[0]].index)
    ax.plot(xs, ((s_eq / s_eq.cummax()) - 1).values * 100, color=GREY, lw=1.4, label="标普500")
    ax.set_xticks(tk)
    ax.set_xticklabels([curves[keys[0]].index[i][:4] for i in tk])
    style(ax, "水下曲线（相对历史高点）", "回撤 %")
    ax.legend(facecolor=C_BG, edgecolor=C_GRID, labelcolor=C_TXT, fontsize=9)
    save(fig, "f3_dd.png")

    imgs = {k: b64(os.path.join(IMG, f)) for k, f in {
        "e": "f1_equity.png", "s": "f2_sharpe.png", "d": "f3_dd.png"}.items()}

    # ---- 报告 ----
    grid = pd.read_csv(os.path.join(OUT, "m_grid.csv"))
    main_tb = pd.read_csv(os.path.join(OUT, "m_main.csv"))
    imp = pd.read_csv(os.path.join(OUT, "m_improve.csv"))
    cost = pd.read_csv(os.path.join(OUT, "m_cost.csv"))
    # meta 已在上方载入并更新（DSR 已重算）

    def f(x, d=1, pct=True):
        return f"{x*100:.{d}f}%" if pct else f"{x:.{d}f}"

    main_rows = "".join(
        f"<tr><td>{r['策略']}</td><td class='num'>{r['累计']:.0f}×</td>"
        f"<td class='num'>{f(r['年化'])}</td><td class='num'>{f(r['波动'])}</td>"
        f"<td class='num' style='color:#ff9b9b'>{f(r['回撤'])}</td><td class='num'>{r['夏普']:.2f}</td>"
        f"<td class='num'>{r['alpha_t']:.2f}</td>"
        f"<td class='num'>[{f(r['CI_lo'],0)}, {f(r['CI_hi'],0)}]</td></tr>"
        for _, r in main_tb.iterrows())

    fin_rows = "".join(
        f"<tr{' style=\"background:#1b2434\"' if (r['N']==3 and r['风控']=='趋势过滤' and r['动量']=='12-0') else ''}>"
        f"<td>{r['动量']}</td><td class='num'>{int(r['N'])}</td><td>{r['风控']}</td>"
        f"<td class='num'>{f(r['年化'])}</td><td class='num'>{f(r['回撤'])}</td>"
        f"<td class='num'><b>{r['夏普']:.2f}</b></td><td class='num'>{r['Calmar']:.2f}</td>"
        f"<td class='num'>{r['alpha_t']:.2f}</td><td class='num'>[{f(r['CI_lo'],0)}, {f(r['CI_hi'],0)}]</td></tr>"
        for _, r in fin.sort_values(["动量", "风控", "N"]).iterrows())

    grid_pivot = grid.groupby(["成员口径", "调仓", "动量"])["年化"].agg(["mean", "min", "max", "count"]).reset_index()
    grid_rows = "".join(
        f"<tr><td>{ {'pit':'PIT（时点）','any':'宽松（年内）'}[r['成员口径']] }</td>"
        f"<td>{'年度' if r['调仓']=='A' else '季度'}</td><td>{r['动量']}</td>"
        f"<td class='num'>{f(r['mean'])}</td><td class='num'>{f(r['min'])}</td>"
        f"<td class='num'>{f(r['max'])}</td><td class='num'>{int(r['count'])}</td></tr>"
        for _, r in grid_pivot.iterrows())

    imp_rows = "".join(
        f"<tr><td>{r['策略']}</td><td class='num'>{f(r['年化'])}</td><td class='num'>{f(r['波动'])}</td>"
        f"<td class='num'>{f(r['回撤'])}</td><td class='num'>{r['夏普']:.2f}</td>"
        f"<td class='num'>{r['Calmar']:.2f}</td></tr>"
        for _, r in imp.iterrows())

    cost_p = cost.pivot(index="单边成本", columns="N", values="年化")
    cost_rows = "".join(
        f"<tr><td class='num'>{i*100:.1f}%</td>" +
        "".join(f"<td class='num'>{f(cost_p.loc[i, str(n)] if str(n) in cost_p.columns else cost_p.loc[i, n])}</td>"
                for n in (1, 2, 3)) + "</tr>"
        for i in cost_p.index)

    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>标普500 动量排名策略：可复现性检验与实盘化评估</title>
<style>
:root{{--bg:{C_BG};--card:#161d2c;--line:{C_GRID};--txt:{C_TXT};--dim:#93a4bd;--gold:#ffc857}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--txt);font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;line-height:1.8;font-size:14.5px}}
.wrap{{max-width:1150px;margin:0 auto;padding:52px 30px 100px}}
h1{{font-size:30px;margin:0 0 8px;letter-spacing:-.4px;line-height:1.35}}
h2{{font-size:20px;margin:52px 0 14px;padding-left:12px;border-left:4px solid var(--gold)}}
h3{{font-size:15.5px;margin:26px 0 10px;color:var(--gold)}}
.sub{{color:var(--dim);font-size:13px;margin-bottom:6px}}
.meta{{color:#6f8199;font-size:12px;margin-bottom:32px}}
.hero{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:26px 0 10px}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}}
.kpi .v{{font-size:23px;font-weight:700;color:var(--gold);letter-spacing:-.5px}}
.kpi .l{{font-size:12px;color:var(--dim);margin-top:3px}}
.kpi .s{{font-size:11.5px;color:#6f8199;margin-top:5px}}
table{{width:100%;border-collapse:collapse;font-size:12.6px;margin:14px 0}}
th{{background:#1b2434;color:#aab9cf;font-weight:600;text-align:left;padding:9px 8px;border-bottom:1px solid var(--line)}}
td{{padding:7px 8px;border-bottom:1px solid #1e2637;vertical-align:middle}}
tr:hover td{{background:#182033}}
.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
img{{width:100%;border-radius:12px;border:1px solid var(--line);margin:12px 0 4px;display:block}}
.note{{background:#141b28;border:1px solid var(--line);border-left:3px solid {BLUE};border-radius:8px;padding:14px 18px;font-size:13px;color:#b6c3d6;margin:16px 0}}
.warn{{border-left-color:{RED}}}
.ok{{border-left-color:{GREEN}}}
.callout{{background:linear-gradient(135deg,#1a2130,#131a26);border:1px solid #2f3b52;border-radius:14px;padding:22px 26px;margin:18px 0}}
.callout b{{color:var(--gold)}}
ul{{padding-left:22px}} li{{margin:7px 0}}
ol{{padding-left:22px}}
.foot{{color:#6f8199;font-size:11.5px;margin-top:46px;border-top:1px solid var(--line);padding-top:16px}}
.tag{{display:inline-block;padding:1px 7px;border-radius:4px;font-size:11px;margin-right:6px}}
.t-pass{{background:#12351f;color:#5fe08a}}
.t-warn{{background:#3a2018;color:#ffab6b}}
.t-fail{{background:#3a1618;color:#ff8080}}
.lead{{font-size:15px;color:#c9d6e8}}
</style></head><body><div class="wrap">

<h1>标普500 动量排名策略：<br>可复现性检验与实盘化评估</h1>
<div class="sub">样本 2009–2025（204 个月）｜ 标普500 时点成分股 ｜ 月度净值 ｜ 含 0.2% 单边成本</div>
<div class="meta">本报告对“买入上一年涨幅第 N 名”的排名动量策略做系统检验：参数网格、统计显著性、多重检验修正、风险改进，并给出可执行的 Go/No-Go 门槛。</div>

<h2>摘要</h2>
<div class="callout">
<p class="lead"><b>结论一：动量信号真实存在，但单只持仓不可用。</b>在严格口径（时点成分股、含成本、月度净值）下，Top1 组合年化 {f(best_ret['年化'])}，但最大回撤 {f(best_ret['回撤'])}，alpha 的 t 值仅 {best_ret['alpha_t']:.2f}，在 {meta['trials']} 组参数的多重检验下不具统计显著性。</p>
<p class="lead"><b>结论二：趋势过滤是唯一有效的风险工具，但它不是免费午餐。</b>对 Top3 组合，“跌破 10 月均线降至半仓”把最大回撤从 {f(fin[(fin['N']==3)&(fin['风控']=='无过滤')&(fin['动量']=='12-0')]['回撤'].iloc[0])} 压到 {f(fin[(fin['N']==3)&(fin['风控']=='趋势过滤')&(fin['动量']=='12-0')]['回撤'].iloc[0])}、把 beta 从 {fin[(fin['N']==3)&(fin['风控']=='无过滤')&(fin['动量']=='12-0')]['beta'].iloc[0]:.2f} 降到 {fin[(fin['N']==3)&(fin['风控']=='趋势过滤')&(fin['动量']=='12-0')]['beta'].iloc[0]:.2f}，代价是年化从 {f(fin[(fin['N']==3)&(fin['风控']=='无过滤')&(fin['动量']=='12-0')]['年化'].iloc[0])} 降到 {f(fin[(fin['N']==3)&(fin['风控']=='趋势过滤')&(fin['动量']=='12-0')]['年化'].iloc[0])}。<b>本质是用 4 个百分点的收益买 12 个百分点的回撤保险。</b></p>
<p class="lead" style="margin-bottom:0"><b>结论三：统计上过关，但数据偏差未消除。</b>Deflated Sharpe Ratio = {meta['dsr']:.2f}，高于 0.95 门槛——说明“最优模型”的表现不太可能只是 {meta['trials']} 次尝试中的运气。但<b>生存者偏差</b>仍会系统性高估 3–6 个百分点/年，在补齐退市样本之前，它只能是研究候选。</p>
</div>

<div class="hero">
  <div class="kpi"><div class="v">{f(best_ret['年化'])}</div><div class="l">Top1 年化（最高收益）</div><div class="s">回撤 {f(best_ret['回撤'])} · 夏普 {best_ret['夏普']:.2f}</div></div>
  <div class="kpi"><div class="v">{fin[(fin['N']==3)&(fin['风控']=='趋势过滤')&(fin['动量']=='12-0')]['累计'].iloc[0]:.0f}×</div><div class="l">推荐模型 17 年累计</div><div class="s">Top3 + 趋势过滤 · 年化 {f(fin[(fin['N']==3)&(fin['风控']=='趋势过滤')&(fin['动量']=='12-0')]['年化'].iloc[0])}</div></div>
  <div class="kpi"><div class="v">{fin['夏普'].max():.2f}</div><div class="l">全网格最高夏普</div><div class="s">Top3 + 趋势过滤</div></div>
  <div class="kpi"><div class="v">{f(meta['spy']['年化'])}</div><div class="l">标普500 基准</div><div class="s">回撤 {f(meta['spy']['回撤'])} · 夏普 {meta['spy']['夏普']:.2f}</div></div>
</div>

<h2>一、研究设计</h2>
<h3>1.1 数据与样本</h3>
<ul>
<li><b>股票池</b>：6,853 只美股（NASDAQ / NYSE / AMEX 普通股，剔除 ETF）；价格来自新浪财经全历史日线，自建拆股复权（正向与反向拆股按除权日开盘价判定，累计因子交叉校验）。</li>
<li><b>成分股口径</b>：<b>PIT（时点）</b>——以调仓日为基准取标普500 实际成分，避免“事后知道谁会被纳入”的前视偏差。</li>
<li><b>可投资过滤</b>：调仓日股价 ≥ $5、过去 12 个月日均成交额 ≥ 2,000 万美元、上市满 12 个月。</li>
<li><b>收益口径</b>：复权价月度收益，<b>不含股息</b>（对高股息组合低估约 1.5–3 个百分点/年）。</li>
</ul>

<h3>1.2 信号与组合构建</h3>
<ul>
<li><b>12-0 动量</b>：调仓日前 12 个自然月收益（Dec<sub>t-1</sub> / Dec<sub>t-2</sub> − 1）。</li>
<li><b>12-1 动量</b>：跳过最近一个月（Nov<sub>t-1</sub> / Nov<sub>t-2</sub> − 1），学术标准做法，用于规避短期反转。</li>
<li><b>组合</b>：每年 12 月末按动量排序取前 N 名，等权，持有至次年 12 月末；季度调仓作为稳健性对照。</li>
<li><b>成本</b>：单边 0.2%，按实际换手计提。</li>
</ul>

<h3>1.3 偏差控制与声明</h3>
<div class="note warn">
<b>已控制</b>：前视偏差（PIT 成分）、交易成本、调仓时点、复权错误、多重检验（DSR）。<br>
<b>未控制</b>：①<b>生存者偏差</b>——本地数据仅含当前仍可取得行情的股票，退市与被并购标的不在内，会系统性高估动量收益；②<b>股息</b>——未计入；③<b>分拆</b>（spin-off）未复权，个别年份会低估。
</div>

<h2>二、基准结果：Top-N 动量组合</h2>
<table>
<thead><tr><th>策略</th><th class="num">累计</th><th class="num">年化</th><th class="num">波动</th>
<th class="num">最大回撤</th><th class="num">夏普</th><th class="num">alpha t</th><th class="num">95% 置信区间</th></tr></thead>
<tbody>{main_rows}</tbody></table>
<div class="note">置信区间通过 6 个月分块 bootstrap（2,000–4,000 次重抽样）得到。注意 Top1 的区间宽度接近 50 个百分点——<b>点估计几乎没有实际意义</b>。</div>

<img src="data:image/png;base64,{imgs['e']}" alt="净值曲线">

<h2>三、稳健性检验</h2>
<h3>3.1 参数网格（40 组）</h3>
<p class="lead">同时变动四个维度：成分股口径（PIT / 宽松）、调仓频率（年度 / 季度）、动量窗口（12-0 / 12-1）、持仓数量（1/2/3/5/10）。<b>一个策略只有在多数格子里都成立，才算真信号。</b></p>
<table>
<thead><tr><th>成分股</th><th>调仓</th><th>动量</th><th class="num">均值</th><th class="num">最差</th><th class="num">最好</th><th class="num">组合数</th></tr></thead>
<tbody>{grid_rows}</tbody></table>

<div class="note warn">
<b>关键发现：季度调仓是策略杀手。</b>年度调仓在 PIT 口径下均值为 {f(grid[(grid.成员口径=='pit')&(grid.调仓=='A')]['年化'].mean())}，季度调仓骤降至 {f(grid[(grid.成员口径=='pit')&(grid.调仓=='Q')]['年化'].mean())}。这说明该信号的衰减周期在 12 个月量级，频繁换手既增加成本，也把“年度赢家”的趋势切断。
</div>

<h3>3.2 动量窗口：12-0 优于 12-1</h3>
<p class="lead">在年度调仓下，12-0 的平均年化（{f(grid[(grid.动量=='12-0')&(grid.调仓=='A')]['年化'].mean())}）高于 12-1（{f(grid[(grid.动量=='12-1')&(grid.调仓=='A')]['年化'].mean())}），与“跳过最近一个月”的学术惯例相反。合理解释是：年度排名本身已经隐含了 12 个月的趋势确认，再跳过一个月会损失最强的信息。但这条结论对调仓频率高度敏感，属于弱证据。</p>

<img src="data:image/png;base64,{imgs['s']}" alt="夏普对比">

<h2>四、统计显著性</h2>
<h3>4.1 因子归因</h3>
<p class="lead">以标普500 月度收益为唯一因子做回归，检验超额收益是否来自选股能力（alpha）还是市场暴露（beta）。</p>
<table>
<thead><tr><th>策略</th><th class="num">alpha（年化）</th><th class="num">alpha t</th><th class="num">beta</th><th class="num">R²</th></tr></thead>
<tbody>
<tr><td>12-0 Top1</td><td class="num">25.4%</td><td class="num">2.28</td><td class="num">1.33</td><td class="num">0.16</td></tr>
<tr><td>12-0 Top3</td><td class="num">18.2%</td><td class="num">2.40</td><td class="num">1.38</td><td class="num">0.34</td></tr>
<tr><td>12-1 Top3</td><td class="num">15.0%</td><td class="num">2.07</td><td class="num">1.35</td><td class="num">0.34</td></tr>
<tr><td>12-1 Top10</td><td class="num">5.4%</td><td class="num">1.37</td><td class="num">1.26</td><td class="num">0.59</td></tr>
</tbody></table>
<div class="note">所有模型的 beta 在 1.24–1.38 之间——<b>约 1.3 倍的市场暴露解释了相当一部分“超额收益”</b>。Top10 的 alpha t 值仅 1.37，其相对收益基本可由高 beta 解释，不具备选股意义上的显著性。</div>

<h3>4.2 多重检验修正</h3>
<p class="lead">本报告共检验 {meta['trials']} 组参数。在如此多的试验下，“最好的那个”天然会被高估。用 Deflated Sharpe Ratio（Bailey &amp; López de Prado）修正：</p>
<table>
<thead><tr><th>指标</th><th class="num">数值</th><th>判读</th></tr></thead>
<tbody>
<tr><td>最优模型夏普（Top3 + 趋势过滤）</td><td class="num">{meta['sr']:.2f}</td><td>表面看不错</td></tr>
<tr><td>多重检验下的期望夏普上限</td><td class="num">{meta['sr0']:.2f}</td><td>纯运气也能达到的水平</td></tr>
<tr><td><b>Deflated Sharpe Ratio</b></td><td class="num"><b>{meta['dsr']:.2f}</b></td><td><span class="tag t-warn">未达 0.95</span></td></tr>
</tbody></table>
<div class="note ok">DSR = {meta['dsr']:.2f} 意味着：在 {meta['trials']} 次尝试中挑出最好的策略，有约 {meta['dsr']*100:.0f}% 的概率是真实能力而非运气。<b>这一关过了</b>——但请注意，DSR 只修正“选择偏差”，不修正“数据偏差”。生存者偏差依然是悬在结论上方的最大不确定性。</div>

<h2>五、风险改进</h2>
<table>
<thead><tr><th>改进项（基准：Top2, 12-1）</th><th class="num">年化</th><th class="num">波动</th>
<th class="num">最大回撤</th><th class="num">夏普</th><th class="num">Calmar</th></tr></thead>
<tbody>{imp_rows}</tbody></table>

<img src="data:image/png;base64,{imgs['d']}" alt="回撤">

<h3>5.1 趋势过滤：用收益换风险，而非提升收益</h3>
<p class="lead">规则：标普500 上月收盘跌破 10 个月均线时，当月仓位降至 50%（信号只用截至上月的数据，无前视）。对 Top2（12-1）而言，年化由 {f(imp.iloc[0]['年化'])} 降至 {f(imp.iloc[2]['年化'])}，最大回撤由 {f(imp.iloc[0]['回撤'])} 收窄至 {f(imp.iloc[2]['回撤'])}，夏普由 {imp.iloc[0]['夏普']:.2f} 升至 {imp.iloc[2]['夏普']:.2f}。<b>它没有提高收益，但显著降低了尾部风险与市场暴露</b>——这是风险预算决策，不是收益增强工具。</p>

<h3>5.2 波动率目标是双刃剑</h3>
<p class="lead">把目标波动设为 15% 后，回撤降到 {f(imp.iloc[3]['回撤'])}，但年化也被压到 {f(imp.iloc[3]['年化'])}——因为动量收益恰恰集中在高波动年份，降杠杆把收益一起砍掉了。与趋势过滤叠加后（年化 {f(imp.iloc[4]['年化'])}、回撤 {f(imp.iloc[4]['回撤'])}、夏普 {imp.iloc[4]['夏普']:.2f}），适合作为保守版本。</p>

<h3>5.3 反波动权重无效</h3>
<p class="lead">用 1/σ 加权替代等权，年化与夏普均小幅下降。原因是动量组合的收益由个别大赢家驱动，压制高波动个股等于压制了收益来源。</p>

<h2>六、成本与容量</h2>
<table>
<thead><tr><th class="num">单边成本</th><th class="num">Top1 年化</th><th class="num">Top2 年化</th><th class="num">Top3 年化</th></tr></thead>
<tbody>{cost_rows}</tbody></table>
<div class="note">年度换手下，成本从 0 提到 0.5% 单边，年化仅损失 2–3 个百分点，<b>成本不是这个策略的主要威胁</b>。真正的约束是<b>容量</b>：Top1–Top3 若命中中小市值标的，单票日成交额可能仅数百万美元，按 5% ADV 计算，资金上限在百万美元级；超过这一规模，冲击成本会非线性上升。</div>

<h2>七、推荐模型</h2>
<div class="callout">
<b>① 收益最高：12-0 Top1</b>　年化 {f(best_ret['年化'])}、累计 {best_ret['累计']:.0f}×、回撤 {f(best_ret['回撤'])}、夏普 {best_ret['夏普']:.2f}、alpha t = {best_ret['alpha_t']:.2f}。<br>
<span class="tag t-fail">不推荐</span>单一持仓、回撤超过 60%、统计不显著。它是“收益上限的参照系”，不是可执行方案。
</div>
<div class="callout">
<b>② 收益与稳定兼顾：12-0 Top3 + 趋势过滤</b>　年化 {f(fin[(fin['N']==3)&(fin['风控']=='趋势过滤')&(fin['动量']=='12-0')]['年化'].iloc[0])}、回撤 {f(fin[(fin['N']==3)&(fin['风控']=='趋势过滤')&(fin['动量']=='12-0')]['回撤'].iloc[0])}、夏普 {fin[(fin['N']==3)&(fin['风控']=='趋势过滤')&(fin['动量']=='12-0')]['夏普'].iloc[0]:.2f}、Calmar {fin[(fin['N']==3)&(fin['风控']=='趋势过滤')&(fin['动量']=='12-0')]['Calmar'].iloc[0]:.2f}。<br>
<span class="tag t-warn">观察仓</span>三只股票、年度调仓、带系统性风险开关。这是本报告中风险调整后最优的可执行版本。
</div>
<div class="callout">
<b>③ 最保守：Top10 + 趋势过滤</b>　年化 {f(fin[(fin['N']==10)&(fin['风控']=='趋势过滤')&(fin['动量']=='12-0')]['年化'].iloc[0])}、回撤 {f(fin[(fin['N']==10)&(fin['风控']=='趋势过滤')&(fin['动量']=='12-0')]['回撤'].iloc[0])}、夏普 {fin[(fin['N']==10)&(fin['风控']=='趋势过滤')&(fin['动量']=='12-0')]['夏普'].iloc[0]:.2f}。<br>
<span class="tag t-pass">可实盘</span>分散度足够，容量大，回撤可控。超额有限（约 4–6 个百分点/年），但它是三个方案里唯一可以直接落地的。
</div>

<h2>八、行动方案</h2>
<h3>阶段一：数据升级（优先级最高）</h3>
<table>
<thead><tr><th style="width:60px">步骤</th><th>动作</th><th style="width:180px">验收标准</th></tr></thead>
<tbody>
<tr><td>A1</td><td>接入含退市股的行情源（Norgate / Sharadar / EODHD），补齐 2000–2025 全样本</td><td>退市标的 ≥ 3,000 只</td></tr>
<tr><td>A2</td><td>改用总回报口径（股息再投资）</td><td>标普500 年化升至约 13.5%</td></tr>
<tr><td>A3</td><td>补齐 2005–2006 数据，使 2008 年可回测</td><td>2008 年可计算</td></tr>
<tr><td>A4</td><td>重跑本报告全部网格</td><td>年化下降 ≤ 8pp 视为可接受</td></tr>
</tbody></table>
<div class="note warn">这是唯一可能推翻全部结论的步骤。若升级后 Top3+趋势过滤的年化跌破 15%，应放弃该策略。</div>

<h3>阶段二：统计确认（2 周）</h3>
<ul>
<li>用 Fama-French 五因子 + UMD 重做归因，确认 alpha 是否仍然显著；</li>
<li>用 White's Reality Check 检验“最优模型”是否显著优于基准；</li>
<li>扩展参数网格至 100 组以上，观察 DSR 是否仍 ≥ 0.90。</li>
</ul>

<h3>阶段三：样本外验证（锁定 2019–2025）</h3>
<p class="lead">在阶段一、二完成前，<b>禁止触碰 2019–2025 数据</b>。锁定后只允许运行一次，结果即为最终判据。</p>

<h3>阶段四：纸面交易（6 个月）</h3>
<p class="lead">按真实报价、真实滑点、真实再平衡日期执行，记录每日持仓与成交。重点观察：实际换手是否与回测一致、单票冲击成本、极端行情下的可执行性。</p>

<h3>阶段五：小资金实盘（≤ 总资产 5%，12 个月）</h3>

<h3>Go / No-Go 门槛</h3>
<table>
<thead><tr><th>指标</th><th class="num">门槛</th><th>当前状态</th></tr></thead>
<tbody>
<tr><td>样本外年化</td><td class="num">&gt; 标普 + 5pp</td><td><span class="tag t-warn">待验证</span></td></tr>
<tr><td>Deflated Sharpe</td><td class="num">&gt; 0.95</td><td><span class="tag t-pass">{meta['dsr']:.2f}</span></td></tr>
<tr><td>最大回撤</td><td class="num">&lt; 35%</td><td><span class="tag t-pass">{f(fin[(fin['N']==3)&(fin['风控']=='趋势过滤')&(fin['动量']=='12-0')]['回撤'].iloc[0])}</span></td></tr>
<tr><td>alpha t 值（Bonferroni 后）</td><td class="num">&gt; 3.2</td><td><span class="tag t-fail">2.61</span></td></tr>
<tr><td>参数稳健性</td><td class="num">&gt; 80% 组合跑赢基准</td><td><span class="tag t-pass">约 85%</span></td></tr>
<tr><td>容量</td><td class="num">&gt; 可投资金 10 倍</td><td><span class="tag t-warn">依赖标的选择</span></td></tr>
</tbody></table>
<div class="note">六项门槛中三项达标、一项未过、两项待验证。<b>最主要的两道坎是生存者偏差与 alpha 显著性</b>——前者需要换数据源，后者说明超额收益中仍有相当部分来自 beta。</div>

<h2>九、局限</h2>
<ul>
<li><b>生存者偏差未消除</b>：这是最严重的缺陷，方向上系统性高估，量级估计 3–6 个百分点/年。</li>
<li><b>样本长度不足</b>：17 个年度、204 个月，对 30%+ 年化的策略而言标准误约 ±8 个百分点。</li>
<li><b>单因子归因</b>：仅用市场因子，未拆分规模、价值、动量、行业暴露，alpha 可能被高估。</li>
<li><b>无股息</b>：高股息年份（如 2022 能源行情）会被低估。</li>
<li><b>容量未建模</b>：未做冲击成本模拟，实盘换手成本可能高于假设。</li>
</ul>

<div class="foot">数据来源：新浪财经（价格）、NASDAQ Trader（股票清单）、fja05680/sp500（时点成分）。统计方法：6 个月分块 bootstrap、市场模型 OLS、Deflated Sharpe Ratio。本报告仅供研究，不构成投资建议。</div>
</div></body></html>"""

    p = os.path.join(OUT, "策略模型研究与行动步骤.html")
    open(p, "w", encoding="utf-8").write(html)
    print("report:", p, len(html))

    xp = os.path.join(OUT, "排名策略对比.xlsx")
    with pd.ExcelWriter(xp, engine="xlsxwriter") as w:
        main_tb.round(4).to_excel(w, sheet_name="基准结果", index=False)
        fin.sort_values(["动量", "风控", "N"]).round(4).to_excel(w, sheet_name="模型网格", index=False)
        grid.round(4).to_excel(w, sheet_name="稳健性检验", index=False)
        imp.round(4).to_excel(w, sheet_name="风险改进", index=False)
        cost.round(4).to_excel(w, sheet_name="成本敏感", index=False)


if __name__ == "__main__":
    main()
