# -*- coding: utf-8 -*-
"""Step 5b: data-quality filtered panel + champions + backtests."""
import os, json
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "output")
os.makedirs(OUT, exist_ok=True)

START, END = 2008, 2026
LIQ_MIN = 20e6
PX_MIN, PX_MAX = 1.5, 5000.0
MAX_MONTH_JUMP = 4.0
MAX_FACTOR = 100.0
HIST_MIN = 24

BAD_WORDS = ("Note", "NOTES", "Preferred", "PREFERRED", "%", "Warrant", "WARRANT",
             " Unit", "Depositary", "Right", "Trust Preferred", "Subordinated")


def load():
    p = pd.read_pickle(os.path.join(DATA, "panel2.pkl"))
    p = p.sort_values(["ticker", "ym"]).copy()
    p["factor"] = p["adj"] / p["raw"]
    # --- data quality: drop tickers with factor drift / insane jumps / bad price levels
    g = p.groupby("ticker")
    fmax = g["factor"].max()
    fmin = g["factor"].min()
    p["raw_ratio"] = p["raw"] / g["raw"].shift(1)
    jmax = p.groupby("ticker")["raw_ratio"].max()
    pmax = g["raw"].max()
    bad = set(fmax[(fmax > MAX_FACTOR) | (fmin < 1 / MAX_FACTOR)].index)
    bad |= set(jmax[jmax > MAX_MONTH_JUMP].index)
    bad |= set(pmax[pmax > PX_MAX].index)
    print(f"drop factor-drift/jump: {len(bad)}")
    p = p[~p.ticker.isin(bad)]
    return p


def build(p):
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
        r = wide[y] / wide[y - 1] - 1
        sub = pd.DataFrame({"ret": r, "px_prev": raw[y - 1], "px_cur": raw[y],
                            "liq_prev": liq[y - 1] if y - 1 in liq.columns else np.nan,
                            "nmon_prev": nmon[y - 1] if y - 1 in nmon.columns else np.nan})
        sub["year"] = y
        sub = sub.reset_index()
        sub = sub[np.isfinite(sub["ret"])]
        sub = sub[(sub["px_prev"].between(PX_MIN, PX_MAX)) & (sub["px_cur"].between(PX_MIN, PX_MAX))]
        sub = sub[(sub["liq_prev"] >= LIQ_MIN) & (sub["nmon_prev"] >= 10)]
        rows.append(sub)
    return pd.concat(rows, ignore_index=True)


def membership():
    m = pd.read_csv(os.path.join(DATA, "sp500_ticker_start_end.csv"))
    m["start_date"] = m["start_date"].astype(str)
    m["end_date"] = m["end_date"].fillna("2099-12-31").astype(str)
    return m


def member_set(m, d):
    return set(m[(m.start_date <= d) & (m.end_date >= d)]["ticker"])


def perf(rets, name):
    r = np.asarray(rets, float)
    r = r[np.isfinite(r)]
    eq = np.cumprod(1 + r)
    n = len(r)
    final = eq[-1]
    vol = r.std(ddof=1) if n > 1 else 0.0
    peak = np.maximum.accumulate(eq)
    dd = float((eq / peak - 1).min())
    return {"strategy": name, "years": n, "final_multiple": final, "cagr": final ** (1 / n) - 1,
            "vol": vol, "max_dd": dd, "best_year": float(r.max()), "worst_year": float(r.min()),
            "positive_years": int((r > 0).sum()), "sharpe": float(r.mean() / vol) if vol else np.nan}


def main():
    p = load()
    df = build(p)
    m = membership()
    uni = pd.read_csv(os.path.join(DATA, "universe_all.csv"))
    nmap = {t: s for t, s in zip(uni["ticker"], uni["secname"])}

    print("\neligible per year:")
    print(df.groupby("year").size().to_string())

    tops, ca, cs = [], [], []
    for y in range(START, END + 1):
        sub = df[df.year == y].copy()
        if sub.empty:
            continue
        mem = member_set(m, f"{y-1}-12-31")
        sub["in_sp"] = sub.ticker.isin(mem)
        sub = sub.sort_values("ret", ascending=False).reset_index(drop=True)
        sub["rank"] = sub.index + 1
        sub["year"] = y
        sub["name"] = sub.ticker.map(nmap).fillna("")
        tops.append(sub.head(12))
        a = sub.iloc[0]
        ca.append({"year": y, "ticker": a.ticker, "ret": a.ret, "name": a["name"],
                   "median_ret": sub.ret.median(), "n": len(sub)})
        s5 = sub[sub.in_sp]
        if len(s5):
            s = s5.iloc[0]
            cs.append({"year": y, "ticker": s.ticker, "ret": s.ret, "name": s["name"],
                       "median_ret": s5.ret.median(), "n": len(s5)})
        print(f"\n{y} eligible={len(sub)} sp={int(sub.in_sp.sum())}")
        for _, r in sub.head(5).iterrows():
            print(f"   #{r['rank']} {r.ticker:<6} {r.ret*100:9.1f}% {str(r['name'])[:26]:<28}{'SP' if r.in_sp else ''}")

    pd.concat(tops, ignore_index=True).to_csv(os.path.join(OUT, "top12_by_year.csv"), index=False, encoding="utf-8-sig")
    ca, cs = pd.DataFrame(ca), pd.DataFrame(cs)
    ca.to_csv(os.path.join(OUT, "champions_all.csv"), index=False, encoding="utf-8-sig")
    cs.to_csv(os.path.join(OUT, "champions_sp500.csv"), index=False, encoding="utf-8-sig")

    # follow-up year for champions
    rm = df.pivot(index="year", columns="ticker", values="ret")

    def s(tk, y):
        if tk in rm.columns and y in rm.index:
            v = rm.at[y, tk]
            return v if np.isfinite(v) else np.nan
        return np.nan

    fu = [{"year": r.year, "ticker": r.ticker, "ret": r.ret,
           "ret_next": s(r.ticker, r.year + 1)} for _, r in cs.iterrows()]
    pd.DataFrame(fu).to_csv(os.path.join(OUT, "champion_followup.csv"), index=False, encoding="utf-8-sig")

    # ---------- backtests ----------
    years = list(range(2009, 2026))
    ch = dict(zip(cs.year, cs.ticker))
    ann = pd.DataFrame({"year": years})
    ann["完美预知_标普500"] = [cs.loc[cs.year == y, "ret"].iloc[0] for y in years]
    ann["完美预知_全市场"] = [ca.loc[ca.year == y, "ret"].iloc[0] for y in years]
    ann["追上年冠军_标普500"] = [s(ch.get(y - 1), y) for y in years]
    t10 = []
    for y in years:
        prev = df[df.year == y - 1]
        mem = member_set(m, f"{y-2}-12-31")
        top = prev[prev.ticker.isin(mem)].nlargest(10, "ret").ticker.tolist()
        vals = [s(t, y) for t in top]
        vals = [v for v in vals if np.isfinite(v)]
        t10.append(np.mean(vals) if vals else 0.0)
    ann["追上年Top10_等权"] = t10
    ann["全市场等权"] = [df[df.year == y].ret.mean() for y in years]
    spy = json.load(open(os.path.join(DATA, "sina2", "SPY.json"), encoding="utf-8"))
    spm = {b[0]: b[1] for b in spy["bars"]}
    ann["标普500指数"] = [spm[f"{y}-12"] / spm[f"{y-1}-12"] - 1 for y in years]

    qqq = json.load(open(os.path.join(DATA, "sina2", "QQQ.json"), encoding="utf-8"))
    qm = {b[0]: b[1] for b in qqq["bars"]}
    ann["纳指100(QQQ)"] = [qm[f"{y}-12"] / qm[f"{y-1}-12"] - 1 for y in years]
    ann.to_csv(os.path.join(OUT, "backtest_annual.csv"), index=False, encoding="utf-8-sig")

    sm = pd.DataFrame([perf(ann[c].values, c) for c in ann.columns[1:]])
    sm.to_csv(os.path.join(OUT, "backtest_summary.csv"), index=False, encoding="utf-8-sig")
    print("\n=== 回测 2009-2025 ===")
    print(sm.to_string(index=False))
    print(ann.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
