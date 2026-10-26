# -*- coding: utf-8 -*-
"""Step 18: 全模型对比 + 预期收益 + 当前选股 + 直白版报告"""
import os, json, math
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from step16_mature import Engine, stats, COST

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
GOLD, RED, GREEN, BLUE, GREY = "#ffc857", "#ff5a5f", "#2ecc71", "#4da3ff", "#8899aa"


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


def boot(r, n=4000, block=6, seed=11):
    rng = np.random.default_rng(seed)
    r = np.asarray(pd.Series(r).dropna())
    N = len(r)
    out = []
    for _ in range(n):
        idx = []
        while len(idx) < N:
            s = rng.integers(0, N)
            idx.extend((s + np.arange(block)) % N)
        x = r[np.array(idx[:N])]
        out.append(np.prod(1 + x) ** (12 / N) - 1)
    return np.percentile(out, [5, 50, 95])


def main():
    E = Engine()
    spy = pd.Series({m: E.spy[m] / E.spy[E.months[E.mpos[m] - 1]] - 1
                     for m in E.months
                     if E.mpos[m] > 0 and m in E.spy and E.months[E.mpos[m] - 1] in E.spy})
    spy = spy[spy.index >= "2009-01"]

    MODELS = [
        ("第1名", 1, 0, 0.0),
        ("第1+2名", 2, 0, 0.0),
        ("前3名", 3, 0, 0.0),
        ("前5名", 5, 0, 0.0),
        ("前10名", 10, 0, 0.0),
        ("前3名(12-1)", 3, 1, 0.0),
        ("前10名(12-1)", 10, 1, 0.0),
        ("前3名+趋势", 3, 0, 0.5),
        ("前5名+趋势", 5, 0, 0.5),
        ("前10名+趋势", 10, 0, 0.5),
    ]
    rows, curves = [], {}
    for name, N, skip, tr in MODELS:
        r = E.run(N=N, skip=skip, cost=COST, trend=tr)
        st = stats(r, name)
        lo, med, hi = boot(r)
        ann = pd.Series(r).groupby(pd.Series(r).index.str[:4]).apply(lambda x: (1 + x).prod() - 1)
        rows.append({"策略": name, "年化": st["年化"], "累计": st["累计"], "回撤": st["回撤"],
                     "最差年": ann.min(), "最好年": ann.max(),
                     "赚钱年数": int((ann > 0).sum()), "总年数": len(ann),
                     "跑赢标普年数": int((ann > spy.groupby(spy.index.str[:4]).apply(
                         lambda x: (1 + x).prod() - 1).reindex(ann.index)).sum()),
                     "下限": lo, "中位": med, "上限": hi})
        curves[name] = r
    df = pd.DataFrame(rows)

    sa = pd.Series({y: (1 + x).prod() - 1 for y, x in spy.groupby(spy.index.str[:4])})
    st_spy = stats(spy, "标普500")
    df.loc[len(df)] = {"策略": "标普500", "年化": st_spy["年化"], "累计": st_spy["累计"],
                       "回撤": st_spy["回撤"], "最差年": sa.min(), "最好年": sa.max(),
                       "赚钱年数": int((sa > 0).sum()), "总年数": len(sa), "跑赢标普年数": 0,
                       "下限": 0.064, "中位": 0.127, "上限": 0.193}
    curves["标普500"] = spy
    df.to_csv(os.path.join(OUT, "final_models.csv"), index=False, encoding="utf-8-sig")
    print(df.round(3).to_string(index=False))

    # ---- 起点敏感性（对前3、前3+趋势、前1）----
    sens = []
    for start in ("2009-01", "2011-01", "2013-01", "2015-01"):
        for nm in ("第1名", "前3名", "前3名+趋势"):
            r = curves[nm]
            r2 = r[r.index >= start]
            eq = (1 + r2).cumprod()
            sens.append({"起点": start[:4], "策略": nm,
                         "年化": eq.iloc[-1] ** (12 / len(r2)) - 1,
                         "回撤": float((eq / eq.cummax() - 1).min())})
    sens = pd.DataFrame(sens)
    sens.to_csv(os.path.join(OUT, "final_sens.csv"), index=False, encoding="utf-8-sig")
    print("\n起点敏感性：")
    print(sens.pivot(index="起点", columns="策略", values="年化").round(3).to_string())

    # ---- 图1：年化排序 ----
    d = df.sort_values("年化")
    fig, ax = plt.subplots(figsize=(10.5, 5.2))
    cols = [GOLD if x == "标普500" else (GREEN if "趋势" in x else RED) for x in d["策略"]]
    ax.barh(d["策略"], d["年化"] * 100, color=cols, height=0.62)
    for i, (v, n) in enumerate(zip(d["年化"], d["策略"])):
        ax.text(v * 100 + 0.5, i, f"{v*100:.1f}%", va="center", color=C_TXT, fontsize=9)
    ax.axvline(12.7, color=GOLD, ls="--", lw=1.2)
    style(ax, "年化收益对比（2009–2025，含成本）", "年化收益 %")
    save(fig, "g1_rank.png")

    # ---- 图2：净值 ----
    fig, ax = plt.subplots(figsize=(11, 5))
    show = [("第1名", RED), ("前3名", GREEN), ("前3名+趋势", BLUE), ("标普500", GREY)]
    for nm, c in show:
        eq = (1 + curves[nm]).cumprod()
        ax.plot(range(len(eq)), eq.values, color=c, lw=2.2, label=f"{nm}  {eq.iloc[-1]:.0f}×")
    ax.set_yscale("log")
    idx = curves["第1名"].index
    tk = [i for i, m in enumerate(idx) if m.endswith("-01") and int(m[:4]) % 2 == 1]
    ax.set_xticks(tk)
    ax.set_xticklabels([idx[i][:4] for i in tk])
    style(ax, "净值走势（起点 1，对数刻度）", "净值（倍）")
    ax.legend(facecolor=C_BG, edgecolor=C_GRID, labelcolor=C_TXT, fontsize=10, loc="upper left")
    save(fig, "g2_curve.png")

    # ---- 图3：回撤 ----
    fig, ax = plt.subplots(figsize=(11, 3.8))
    for nm, c in show:
        eq = (1 + curves[nm]).cumprod()
        ax.plot(range(len(eq)), ((eq / eq.cummax()) - 1).values * 100, color=c, lw=1.5, label=nm)
    ax.set_xticks(tk)
    ax.set_xticklabels([idx[i][:4] for i in tk])
    style(ax, "最坏的时候亏多少（相对历史高点）", "回撤 %")
    ax.legend(facecolor=C_BG, edgecolor=C_GRID, labelcolor=C_TXT, fontsize=9)
    save(fig, "g3_dd.png")

    # ---- 当前选股（2026-10）----
    last = "2026-10"
    s = E.score(last, 12, 0)
    elig = E.eligible(last, "pit")
    cur = s[s.index.isin(elig)].dropna().sort_values(ascending=False)
    uni = pd.read_csv(os.path.join(DATA, "universe_all.csv"))
    nmap = dict(zip(uni["ticker"], uni["secname"]))
    px = E.raw.loc[last]
    picks = []
    for i, (tk, v) in enumerate(cur.head(12).items(), 1):
        picks.append({"名次": i, "代码": tk, "公司": str(nmap.get(tk, ""))[:34],
                      "过去12月涨幅": v, "股价": float(px.get(tk, np.nan))})
    pk = pd.DataFrame(picks)
    pk.to_csv(os.path.join(OUT, "current_picks.csv"), index=False, encoding="utf-8-sig")
    print("\n2026-10 排名（前12）：")
    print(pk.to_string(index=False))

    # 趋势状态
    i = E.mpos[last]
    ma = np.mean([E.spy[x] for x in E.months[i - 9:i + 1]])
    cur_px = E.spy[last]
    trend_on = cur_px >= ma
    print(f"SPY {cur_px:.2f} vs 10月均线 {ma:.2f} -> {'满仓' if trend_on else '半仓'}")

    with open(os.path.join(OUT, "final_meta.json"), "w", encoding="utf-8") as f:
        json.dump({"picks": pk.to_dict("records"), "spy": float(cur_px), "ma": float(ma),
                   "trend_on": bool(trend_on),
                   "models": df.to_dict("records"),
                   "sens": sens.to_dict("records")}, f, ensure_ascii=False, indent=2)

    imgs = {k: b64(os.path.join(IMG, f)) for k, f in {
        "g1": "g1_rank.png", "g2": "g2_curve.png", "g3": "g3_dd.png"}.items()}
    return df, sens, pk, trend_on, cur_px, ma, imgs


if __name__ == "__main__":
    main()
