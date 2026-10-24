# -*- coding: utf-8 -*-
"""Step 10: 每年买入上一年冠军（动量策略）完整回测 2008-2025 + 2026YTD"""
import os, json
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "output")

START, END = 2008, 2026
LIQ_MIN = 20e6
PX_MIN, PX_MAX = 1.5, 5000.0
MAX_MONTH_JUMP = 8.0
MAX_FACTOR = 2000.0


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
    ch = pd.read_csv(os.path.join(OUT, "champions_verified.csv"))
    dec = p[p.ym.str[5:7] == "12"].groupby(["ticker", "year"]).agg(
        adj=("adj", "last"), raw=("raw", "last")).reset_index()
    wide = dec.pivot(index="ticker", columns="year", values="adj")
    raw = dec.pivot(index="ticker", columns="year", values="raw")
    liq = p.groupby(["ticker", "year"])["dv"].mean().unstack()
    nmon = p.groupby(["ticker", "year"]).size().unstack()

    rows = []
    for y in range(START, END + 1):
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
    rm = df.pivot(index="year", columns="ticker", values="ret")

    # 直接由 12 月复权价计算（不受历史长度/流动性过滤影响），用于冠军策略
    dec_adj = p[p.ym.str[5:7] == "12"].groupby(["ticker", "year"])["adj"].last().unstack()
    bad = set()

    def r_direct(tk, y):
        try:
            a, b = dec_adj.at[tk, y - 1], dec_adj.at[tk, y]
        except KeyError:
            return np.nan
        if not (np.isfinite(a) and np.isfinite(b)) or a <= 0:
            return np.nan
        return float(b / a - 1)

    def r_of(tk, y):
        if y == 2026:
            a = p[(p.ticker == tk) & (p.ym == "2025-12")]["adj"]
            b = p[(p.ticker == tk) & (p.ym == p.ym.max())]["adj"]
            if len(a) and len(b):
                return float(b.iloc[0] / a.iloc[0] - 1)
            return np.nan
        return r_direct(tk, y)

    chmap = dict(zip(ch.year, ch.ticker))
    name = dict(zip(ch.ticker, ch["name"]))

    # ---- 单只：每年买上年冠军 ----
    recs = []
    for y in range(START, 2027):
        tk = chmap.get(y - 1)
        if tk is None:
            continue
        r = r_of(tk, y)
        recs.append({"year": y, "hold": tk, "name": name.get(tk, ""),
                     "ret": r, "champ_prev_ret": float(ch.loc[ch.year == y - 1, "ret_published"].iloc[0]),
                     "spx": None})
    d1 = pd.DataFrame(recs)

    spy = json.load(open(os.path.join(DATA, "sina2", "SPY.json"), encoding="utf-8"))
    spm = {b[0]: b[1] for b in spy["bars"]}
    def spx_ret(y):
        if y == 2026:
            a, b = spm.get("2025-12"), spm.get("2026-10")
        else:
            a, b = spm.get(f"{y-1}-12"), spm.get(f"{y}-12")
        return (b / a - 1) if a and b else np.nan
    d1["spx"] = [spx_ret(y) for y in d1.year]

    print("=== 每年买上年冠军（单只满仓）===")
    print(d1.round(4).to_string(index=False))

    full = d1[d1.year <= 2025].dropna(subset=["ret"])
    eq = (1 + full.ret.values).cumprod()
    n = len(full)
    vol = full.ret.std(ddof=1)
    peak = np.maximum.accumulate(eq)
    print(f"\n2008-2025 累计 {eq[-1]:.2f}x  CAGR {eq[-1]**(1/n)-1:.2%}  vol {vol:.2%}  "
          f"MDD {(eq/peak-1).min():.2%}  最差年 {full.ret.min():.2%}  "
          f"跑赢指数 {int((full.ret>full.spx).sum())}/{n}")
    eq2 = np.concatenate([[1.0], eq])
    print("净值路径:", " ".join(f"{v:.2f}" for v in eq2))

    # ---- 对比：上年 TopN 等权 ----
    m = pd.read_csv(os.path.join(DATA, "sp500_ticker_start_end.csv"))
    m["start_date"] = m["start_date"].astype(str)
    m["end_date"] = m["end_date"].fillna("2099-12-31").astype(str)

    def mem(d):
        return set(m[(m.start_date <= d) & (m.end_date >= d)]["ticker"])

    out = {"year": [], "Top1": [], "Top3": [], "Top5": [], "Top10": [], "SPX": []}
    for y in range(2009, 2026):
        prev = df[df.year == y - 1]
        mem_ = mem(f"{y-2}-12-31")
        rank = prev[prev.ticker.isin(mem_)].nlargest(10, "ret").ticker.tolist()
        out["year"].append(y)
        for N in (1, 3, 5, 10):
            vals = [r_of(t, y) for t in rank[:N]]
            vals = [v for v in vals if np.isfinite(v)]
            out[f"Top{N}"].append(np.mean(vals) if vals else np.nan)
        out["SPX"].append(spx_ret(y))
    cmp = pd.DataFrame(out)
    print("\n=== 上年 TopN 等权（2009-2025）===")
    print(cmp.round(4).to_string(index=False))
    for c in ["Top1", "Top3", "Top5", "Top10", "SPX"]:
        s = cmp[c].dropna()
        e = (1 + s.values).prod()
        pk = np.maximum.accumulate((1 + s.values).cumprod())
        print(f"  {c:6s} {e:8.2f}x  CAGR {(e**(1/len(s))-1)*100:6.1f}%  "
              f"vol {s.std(ddof=1)*100:5.1f}%  MDD {((1+s.values).cumprod()/pk-1).min()*100:6.1f}%  "
              f"Sharpe {s.mean()/s.std(ddof=1):.2f}")

    d1.to_csv(os.path.join(OUT, "momentum_single_full.csv"), index=False, encoding="utf-8-sig")
    cmp.to_csv(os.path.join(OUT, "momentum_topn.csv"), index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
