# -*- coding: utf-8 -*-
"""Step 4: champions per year + backtests."""
import os, json
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "output")
os.makedirs(OUT, exist_ok=True)

START, END = 2008, 2026
LIQ_MIN = 20e6      # avg monthly dollar volume in prior year (USD)
PX_MIN = 3.0


def load():
    p = pd.read_pickle(os.path.join(DATA, "panel.pkl"))
    p["dv"] = p["raw"] * p["vol"]
    return p


def yearend(p):
    dec = p[p.ym.str[5:7] == "12"].copy()
    dec = dec.groupby(["ticker", "year"]).agg(adj=("adj", "last"), raw=("raw", "last")).reset_index()
    return dec


def liquidity(p):
    """avg monthly dollar volume for each ticker-year (all 12 months)."""
    g = p.groupby(["ticker", "year"])["dv"].agg(["mean", "count"]).reset_index()
    g.columns = ["ticker", "year", "dv_mean", "n_mon"]
    return g


def build_returns(p):
    dec = yearend(p)
    liq = liquidity(p)
    dec = dec.merge(liq, on=["ticker", "year"], how="left")
    wide = dec.pivot(index="ticker", columns="year", values="adj")
    raw = dec.pivot(index="ticker", columns="year", values="raw")
    liqw = dec.pivot(index="ticker", columns="year", values="dv_mean")
    rows = []
    for y in range(START, END + 1):
        if y - 1 not in wide.columns or y not in wide.columns:
            continue
        r = wide[y] / wide[y - 1] - 1
        sub = pd.DataFrame({"ret": r, "px_prev": raw[y - 1], "liq_prev": liqw[y - 1],
                            "liq_cur": liqw[y]})
        sub["year"] = y
        sub = sub.reset_index()
        sub = sub[np.isfinite(sub["ret"])]
        sub = sub[(sub["px_prev"] >= PX_MIN) & (sub["liq_prev"] >= LIQ_MIN)]
        rows.append(sub)
    df = pd.concat(rows, ignore_index=True)
    return df, wide, raw


def sp500_membership():
    m = pd.read_csv(os.path.join(DATA, "sp500_ticker_start_end.csv"))
    m["start_date"] = m["start_date"].astype(str)
    m["end_date"] = m["end_date"].fillna("2099-12-31").astype(str)
    return m


def member_set(m, date_str):
    return set(m[(m.start_date <= date_str) & (m.end_date >= date_str)]["ticker"])


def main():
    p = load()
    df, wide, raw = build_returns(p)
    m = sp500_membership()

    # ---------- name map ----------
    names = {}
    for f in os.listdir(os.path.join(DATA, "tq")):
        pass
    uni = pd.read_csv(os.path.join(DATA, "universe_all.csv"))
    nmap = dict(zip(uni["ticker"], uni["secname"]))

    # ---------- champions ----------
    champ_all, champ_sp = [], []
    for y in range(START, END + 1):
        sub = df[df.year == y]
        if sub.empty:
            continue
        mem = member_set(m, f"{y-1}-12-31")
        a = sub.sort_values("ret", ascending=False).head(1)
        s = sub[sub.ticker.isin(mem)].sort_values("ret", ascending=False).head(1)
        for frame, store, tag in [(a, champ_all, "all"), (s, champ_sp, "sp500")]:
            if frame.empty:
                continue
            r = frame.iloc[0]
            store.append({"year": y, "ticker": r.ticker, "ret": r.ret,
                          "name": nmap.get(r.ticker, ""), "universe": tag,
                          "n_eligible": len(sub) if tag == "all" else sub.ticker.isin(mem).sum(),
                          "median_ret": sub.ret.median()})
    ca = pd.DataFrame(champ_all)
    cs = pd.DataFrame(champ_sp)
    ca.to_csv(os.path.join(OUT, "champions_all.csv"), index=False, encoding="utf-8-sig")
    cs.to_csv(os.path.join(OUT, "champions_sp500.csv"), index=False, encoding="utf-8-sig")
    print("== ALL-market champions ==")
    print(ca[["year", "ticker", "ret", "n_eligible", "median_ret"]].to_string(index=False))
    print("\n== S&P500 champions ==")
    print(cs[["year", "ticker", "ret", "n_eligible"]].to_string(index=False))

    # ---------- champion follow-up ----------
    fu = []
    for _, r in cs.iterrows():
        nxt = df[(df.year == r.year + 1) & (df.ticker == r.ticker)]
        fu.append({"year": r.year, "ticker": r.ticker,
                   "ret_this": r.ret,
                   "ret_next": float(nxt.ret.iloc[0]) if len(nxt) else np.nan})
    pd.DataFrame(fu).to_csv(os.path.join(OUT, "champion_followup.csv"), index=False, encoding="utf-8-sig")

    json.dump({"n_panel": int(p.ticker.nunique()), "n_rows": int(len(df)),
               "years": sorted(df.year.unique().tolist())},
              open(os.path.join(OUT, "summary.json"), "w"), indent=2)
    print("\neligible per year:")
    print(df.groupby("year").size().to_string())


if __name__ == "__main__":
    main()
