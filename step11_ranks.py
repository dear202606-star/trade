# -*- coding: utf-8 -*-
"""Step 11: 前一年涨幅第1/2/3名 -> 次年持有，多模型对比"""
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

    m = pd.read_csv(os.path.join(DATA, "sp500_ticker_start_end.csv"))
    m["start_date"] = m["start_date"].astype(str)
    m["end_date"] = m["end_date"].fillna("2099-12-31").astype(str)

    def mem_any(y):
        a, b = f"{y}-01-01", f"{y}-12-31"
        return set(m[(m.start_date <= b) & (m.end_date >= a)]["ticker"])

    # 每年在「年内曾为标普500成分股」的池子里排前3
    ranks = {}
    for y in range(2008, 2027):
        sub = df[(df.year == y) & (df.ticker.isin(mem_any(y)))].sort_values("ret", ascending=False)
        ranks[y] = sub.head(5)[["ticker", "ret"]].reset_index(drop=True)

    ch = pd.read_csv(os.path.join(OUT, "champions_verified.csv"))
    pub = dict(zip(ch.year, ch.ticker))
    print("年份  我的#1    公开冠军   是否一致   #2        #3")
    ok = 0
    for y in sorted(ranks):
        r = ranks[y]
        if r.empty:
            continue
        t1 = r.ticker.iloc[0]
        pub_t = pub.get(y, "")
        hit = "√" if t1 == pub_t else ("—" if not pub_t else "×")
        ok += (t1 == pub_t)
        t2 = r.ticker.iloc[1] if len(r) > 1 else ""
        t3 = r.ticker.iloc[2] if len(r) > 2 else ""
        print(f"{y}  {t1:6s}  {str(pub_t):8s}  {hit}  {t2:6s}  {t3:6s}")
    print(f"\n一致 {ok}/20")


if __name__ == "__main__":
    main()
