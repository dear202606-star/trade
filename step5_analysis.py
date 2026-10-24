# -*- coding: utf-8 -*-
"""Step 5: champions + backtests (single-source, split-adjusted)."""
import os, json
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "output")
os.makedirs(OUT, exist_ok=True)

START, END = 2008, 2026
LIQ_MIN = 20e6
PX_MIN = 3.0
HIST_MIN = 24


def load():
    p = pd.read_pickle(os.path.join(DATA, "panel2.pkl"))
    return p


def yearend(p):
    dec = p[p.ym.str[5:7] == "12"]
    dec = dec.groupby(["ticker", "year"]).agg(adj=("adj", "last"), raw=("raw", "last")).reset_index()
    return dec


def build(p):
    dec = yearend(p)
    wide = dec.pivot(index="ticker", columns="year", values="adj")
    raw = dec.pivot(index="ticker", columns="year", values="raw")
    liq = p.groupby(["ticker", "year"])["dv"].mean().unstack()
    nmon = p.groupby(["ticker", "year"]).size().unstack()
    rows = []
    for y in range(START, END + 1):
        if y - 1 not in wide.columns or y not in wide.columns:
            continue
        curr, prev = wide[y], wide[y - 1]
        r = curr / prev - 1
        sub = pd.DataFrame({"ret": r, "px_prev": raw[y - 1],
                            "liq_prev": liq[y - 1] if y - 1 in liq.columns else np.nan,
                            "nmon_prev": nmon[y - 1] if y - 1 in nmon.columns else np.nan})
        sub["year"] = y
        sub = sub.reset_index()
        sub = sub[np.isfinite(sub["ret"])]
        sub = sub[(sub["px_prev"] >= PX_MIN) & (sub["liq_prev"] >= LIQ_MIN) & (sub["nmon_prev"] >= 10)]
        rows.append(sub)
    df = pd.concat(rows, ignore_index=True)
    return df, wide


def membership():
    m = pd.read_csv(os.path.join(DATA, "sp500_ticker_start_end.csv"))
    m["start_date"] = m["start_date"].astype(str)
    m["end_date"] = m["end_date"].fillna("2099-12-31").astype(str)
    return m


def member_set(m, d):
    return set(m[(m.start_date <= d) & (m.end_date >= d)]["ticker"])


def perf(rets, name):
    rets = np.asarray(rets, dtype=float)
    eq = np.cumprod(1 + rets)
    n = len(rets)
    final = eq[-1]
    cagr = final ** (1 / n) - 1
    vol = rets.std(ddof=1) if n > 1 else 0
    peak = np.maximum.accumulate(eq)
    dd = (eq / peak - 1).min()
    sharpe = (rets.mean() / vol * np.sqrt(1)) if vol > 0 else np.nan
    return {"strategy": name, "years": n, "final_multiple": final,
            "cagr": cagr, "vol": vol, "max_dd": dd,
            "best_year": rets.max(), "worst_year": rets.min(),
            "positive_years": int((rets > 0).sum()), "sharpe": sharpe}


def main():
    p = load()
    df, wide = build(p)
    m = membership()
    uni = pd.read_csv(os.path.join(DATA, "universe_all.csv"))
    nmap = dict(zip(uni["ticker"], uni["secname"]))

    # ---------- ranking tables ----------
    tops = {}
    for y in range(START, END + 1):
        sub = df[df.year == y].copy()
        if sub.empty:
            continue
        mem = member_set(m, f"{y-1}-12-31")
        sub["in_sp"] = sub.ticker.isin(mem)
        sub = sub.sort_values("ret", ascending=False)
        top = sub.head(15).copy()
        top["year"] = y
        top["rank"] = range(1, len(top) + 1)
        top["name"] = top.ticker.map(nmap).fillna("")
        tops[y] = top
        sp_top = sub[sub.in_sp].head(5)
        print(f"\n{y}  eligible={len(sub)}  sp_members={int(sub.in_sp.sum())}")
        for i, (_, r) in enumerate(top.head(6).iterrows(), 1):
            print(f"   ALL #{i} {r.ticker:<6} {r.ret*100:9.1f}%  {str(r['name'])[:22]:<24} {'SP' if r.in_sp else ''}")

    all_top = pd.concat(tops.values(), ignore_index=True)
    all_top.to_csv(os.path.join(OUT, "top15_by_year.csv"), index=False, encoding="utf-8-sig")

    # ---------- champions ----------
    ca, cs = [], []
    for y in range(START, END + 1):
        sub = df[df.year == y]
        if sub.empty:
            continue
        mem = member_set(m, f"{y-1}-12-31")
        a = sub.sort_values("ret", ascending=False).iloc[0]
        s5 = sub[sub.ticker.isin(mem)].sort_values("ret", ascending=False)
        ca.append({"year": y, "ticker": a.ticker, "ret": a.ret, "name": nmap.get(a.ticker, ""),
                   "median_ret": sub.ret.median(), "n": len(sub)})
        if len(s5):
            s = s5.iloc[0]
            cs.append({"year": y, "ticker": s.ticker, "ret": s.ret, "name": nmap.get(s.ticker, ""),
                       "median_ret": sub[sub.ticker.isin(mem)].ret.median(),
                       "n": int(sub.ticker.isin(mem).sum())})
    ca, cs = pd.DataFrame(ca), pd.DataFrame(cs)
    ca.to_csv(os.path.join(OUT, "champions_all.csv"), index=False, encoding="utf-8-sig")
    cs.to_csv(os.path.join(OUT, "champions_sp500.csv"), index=False, encoding="utf-8-sig")

    # ---------- returns matrix helpers ----------
    rm = df.pivot(index="year", columns="ticker", values="ret")

    def series(ticker, year):
        v = rm.at[year, ticker] if (year in rm.index and ticker in rm.columns) else np.nan
        return v

    # ---------- backtests ----------
    years = list(range(2009, 2026))
    res = {}

    def run(name, rets):
        res[name] = perf(list(rets), name)

    # perfect foresight
    for label, ch in [("完美预知_标普500", cs), ("完美预知_全市场", ca)]:
        rr = [ch.loc[ch.year == y, "ret"].iloc[0] for y in years]
        run(label, rr)

    # chase last year's champion (S&P)
    ch_map = dict(zip(cs.year, cs.ticker))
    rr, spent = [], []
    for y in years:
        tk = ch_map.get(y - 1)
        v = series(tk, y) if tk else np.nan
        rr.append(0.0 if not np.isfinite(v) else v)
        spent.append(tk)
    run("追上年冠军_标普500", rr)

    # top10 momentum
    rr = []
    for y in years:
        prev = df[df.year == y - 1]
        mem = member_set(m, f"{y-2}-12-31")
        top = prev[prev.ticker.isin(mem)].nlargest(10, "ret").ticker.tolist()
        vals = [series(t, y) for t in top]
        vals = [v for v in vals if np.isfinite(v)]
        rr.append(np.mean(vals) if vals else 0.0)
    run("追上年Top10_等权", rr)

    # equal weight all
    rr = [df[df.year == y].ret.mean() for y in years]
    run("全市场等权", rr)

    # S&P index via SPY
    spy = json.load(open(os.path.join(DATA, "sina2", "SPY.json"), encoding="utf-8"))
    spm = {b[0]: b[1] for b in spy["bars"]}
    rr = []
    for y in years:
        a, b = spm.get(f"{y-1}-12"), spm.get(f"{y}-12")
        rr.append(b / a - 1 if a and b else np.nan)
    run("标普500指数(SPY)", rr)

    bt = pd.DataFrame({k: pd.Series(v["years"] and [None]) for k, v in res.items()})
    tbl = pd.DataFrame(res.values())
    bt_annual = pd.DataFrame({"year": years})
    for k in res:
        pass

    # rebuild annual series frame
    ann = {"year": years}
    ann["完美预知_标普500"] = [cs.loc[cs.year == y, "ret"].iloc[0] for y in years]
    ann["完美预知_全市场"] = [ca.loc[ca.year == y, "ret"].iloc[0] for y in years]
    ann["追上年冠军_标普500"] = rr  # placeholder replaced below
    ann["追上年冠军_标普500"] = [series(ch_map.get(y - 1), y) for y in years]
    top10 = []
    for y in years:
        prev = df[df.year == y - 1]
        mem = member_set(m, f"{y-2}-12-31")
        top = prev[prev.ticker.isin(mem)].nlargest(10, "ret").ticker.tolist()
        vals = [series(t, y) for t in top]
        vals = [v for v in vals if np.isfinite(v)]
        top10.append(np.mean(vals) if vals else 0.0)
    ann["追上年Top10_等权"] = top10
    ann["全市场等权"] = [df[df.year == y].ret.mean() for y in years]
    ann["标普500指数(SPY)"] = [spm.get(f"{y}-12") / spm.get(f"{y-1}-12") - 1 for y in years]
    ann = pd.DataFrame(ann)
    for c in ann.columns[1:]:
        ann[c] = ann[c].astype(float)
    ann.to_csv(os.path.join(OUT, "backtest_annual.csv"), index=False, encoding="utf-8-sig")

    summary = [perf(ann[c].values, c) for c in ann.columns[1:]]
    sm = pd.DataFrame(summary)
    sm.to_csv(os.path.join(OUT, "backtest_summary.csv"), index=False, encoding="utf-8-sig")
    print("\n=== backtest 2009-2025 ===")
    print(sm.to_string(index=False))
    print("\n", ann.to_string(index=False))


if __name__ == "__main__":
    main()
