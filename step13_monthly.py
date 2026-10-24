# -*- coding: utf-8 -*-
"""Step 13: 月度净值重算真实回撤 + 分段检验"""
import os, json
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "output")

PX_MIN, PX_MAX = 5.0, 5000.0
LIQ_MIN = 20e6
MAX_MONTH_JUMP = 8.0
MAX_FACTOR = 2000.0
FY0, FY1 = 2009, 2026


def load_panel():
    p = pd.read_pickle(os.path.join(DATA, "panel2.pkl")).sort_values(["ticker", "ym"]).copy()
    p["factor"] = p["adj"] / p["raw"]
    g = p.groupby("ticker")
    fmax, fmin = g["factor"].max(), g["factor"].min()
    p["adj_ratio"] = p["adj"] / g["adj"].shift(1)
    jmax = p.groupby("ticker")["adj_ratio"].max()
    jmin = p.groupby("ticker")["adj_ratio"].min()
    pmax = g["raw"].max()
    bad = set(fmax[(fmax > MAX_FACTOR) | (fmin < 1 / MAX_FACTOR)].index)
    bad |= set(jmax[jmax > MAX_MONTH_JUMP].index)
    bad |= set(jmin[jmin < 1 / MAX_MONTH_JUMP].index)
    bad |= set(pmax[pmax > PX_MAX].index)
    return p[~p.ticker.isin(bad)]


def main():
    p = load_panel()
    dec = p[p.ym.str[5:7] == "12"].groupby(["ticker", "year"]).agg(
        adj=("adj", "last"), raw=("raw", "last")).reset_index()
    wide = dec.pivot(index="ticker", columns="year", values="adj")
    raw = dec.pivot(index="ticker", columns="year", values="raw")
    liq = p.groupby(["ticker", "year"])["dv"].mean().unstack()
    nmon = p.groupby(["ticker", "year"]).size().unstack()

    rows = []
    for y in range(2008, 2026):
        if y - 1 not in wide.columns or y not in wide.columns:
            continue
        sub = pd.DataFrame({"ret": wide[y] / wide[y - 1] - 1,
                            "px_prev": raw[y - 1], "px_cur": raw[y],
                            "liq_prev": liq[y - 1] if y - 1 in liq.columns else np.nan,
                            "nmon_prev": nmon[y - 1] if y - 1 in nmon.columns else np.nan})
        sub["year"] = y
        sub = sub.reset_index()
        sub = sub[np.isfinite(sub["ret"])]
        sub = sub[(sub["px_prev"].between(PX_MIN, PX_MAX)) & (sub["px_cur"].between(PX_MIN, PX_MAX))]
        sub = sub[(sub["liq_prev"] >= LIQ_MIN) & (sub["nmon_prev"] >= 10)]
        rows.append(sub)
    df = pd.concat(rows, ignore_index=True)

    m = pd.read_csv(os.path.join(DATA, "sp500_ticker_start_end.csv"))
    m["start_date"] = m["start_date"].astype(str)
    m["end_date"] = m["end_date"].fillna("2099-12-31").astype(str)

    def mem_any(y):
        a, b = f"{y}-01-01", f"{y}-12-31"
        return set(m[(m.start_date <= b) & (m.end_date >= a)]["ticker"])

    ranks = {}
    for y in range(2008, 2026):
        sub = df[(df.year == y) & (df.ticker.isin(mem_any(y)))].sort_values("ret", ascending=False)
        ranks[y] = sub.head(10).ticker.tolist()

    pm = p.pivot(index="ym", columns="ticker", values="adj").sort_index()
    mret = pm.pct_change()
    months = [x for x in pm.index if "2009-01" <= x <= "2026-10"]
    yrmap = {x: int(x[:4]) for x in months}

    models = {
        "M1 只买第1名": [1],
        "M2 只买第2名": [2],
        "M3 只买第3名": [3],
        "M4 第1+第2": [1, 2],
        "M5 第1+第2+第3": [1, 2, 3],
        "M6 前10名等权": list(range(1, 11)),
    }
    curves = {}
    for name, ks in models.items():
        vals = []
        for ym in months:
            y = yrmap[ym]
            src = y - 1
            picks = [ranks[src][k - 1] for k in ks if src in ranks and len(ranks[src]) >= k]
            picks = [t for t in picks if t in mret.columns]
            if not picks:
                vals.append(0.0)
                continue
            r = mret.loc[ym, picks]
            vals.append(float(np.nanmean(r.values)))
        curves[name] = np.array(vals)
    spy = json.load(open(os.path.join(DATA, "sina2", "SPY.json"), encoding="utf-8"))
    spm = {b[0]: b[1] for b in spy["bars"]}
    sp = []
    for i, x in enumerate(months):
        prev = months[i - 1] if i > 0 else "2008-12"
        a, b = spm.get(prev), spm.get(x)
        sp.append((b / a - 1) if a and b else 0.0)
    curves["SPY"] = np.array(sp)

    def stats(r, name):
        r = pd.Series(r).dropna().values
        eq = np.cumprod(1 + r)
        yrs = len(r) / 12
        peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
        ann = eq[-1] ** (1 / yrs) - 1
        vol = r.std(ddof=1) * np.sqrt(12)
        mdd = float((eq / peak - 1).min())
        return {"模型": name, "累计倍数": eq[-1], "年化": ann, "年化波动": vol,
                "最大回撤(月)": mdd, "夏普": ann / vol, "Calmar": ann / abs(mdd)}

    tb = pd.DataFrame([stats(v, k) for k, v in curves.items()])
    tb = tb.sort_values("年化", ascending=False)
    tb.to_csv(os.path.join(OUT, "monthly_summary.csv"), index=False, encoding="utf-8-sig")
    print("=== 月度口径（2009-01 至 2026-10，含 2026 部分年度）===")
    print(tb.round(3).to_string(index=False))

    # 完整年度口径 2009-2025
    cut = [i for i, x in enumerate(months) if x <= "2025-12"]
    tb2 = pd.DataFrame([stats(np.array(v)[cut], k) for k, v in curves.items()])
    tb2 = tb2.sort_values("年化", ascending=False)
    tb2.to_csv(os.path.join(OUT, "monthly_summary_full.csv"), index=False, encoding="utf-8-sig")
    print("\n=== 月度口径（2009-01 至 2025-12，17 个完整年度）===")
    print(tb2.round(3).to_string(index=False))

    # 分段
    segs = {"2009-2018": [x for x in months if x < "2019-01"],
            "2019-2026": [x for x in months if x >= "2019-01"]}
    print("\n=== 分段年化 ===")
    out = []
    for sname, ms in segs.items():
        idx = [months.index(x) for x in ms]
        row = {"区间": sname}
        for k, v in curves.items():
            sub = v[idx]
            sub = sub[np.isfinite(sub)]
            eq = np.cumprod(1 + sub)
            row[k] = eq[-1] ** (12 / len(sub)) - 1
        out.append(row)
    seg = pd.DataFrame(out)
    seg.to_csv(os.path.join(OUT, "segment_summary.csv"), index=False, encoding="utf-8-sig")
    print(seg.round(4).to_string(index=False))

    np.save(os.path.join(OUT, "curves.npy"), curves, allow_pickle=True)
    json.dump({"months": months}, open(os.path.join(OUT, "months.json"), "w"))


if __name__ == "__main__":
    main()
