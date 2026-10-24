# -*- coding: utf-8 -*-
"""Step 7: final numbers -- champion table, perfect-foresight compounding, realistic strategies."""
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
MAX_MONTH_JUMP = 8.0
MAX_FACTOR = 2000.0

CHAMPS = pd.read_csv(os.path.join(OUT, "champions_verified.csv"))


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
    print("tickers dropped by quality filter:", len(bad))
    return p[~p.ticker.isin(bad)]


def annual_panel(p):
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
    return pd.concat(rows, ignore_index=True), wide


def membership():
    m = pd.read_csv(os.path.join(DATA, "sp500_ticker_start_end.csv"))
    m["start_date"] = m["start_date"].astype(str)
    m["end_date"] = m["end_date"].fillna("2099-12-31").astype(str)
    return m


def member_set(m, d):
    return set(m[(m.start_date <= d) & (m.end_date >= d)]["ticker"])


def perf(r, name):
    r = np.asarray(r, float)
    r = r[np.isfinite(r)]
    eq = np.cumprod(1 + r)
    vol = r.std(ddof=1) if len(r) > 1 else 0.0
    peak = np.maximum.accumulate(eq)
    return {"strategy": name, "years": len(r), "final_multiple": float(eq[-1]),
            "cagr": float(eq[-1] ** (1 / len(r)) - 1), "vol": float(vol),
            "max_dd": float((eq / peak - 1).min()), "best": float(r.max()),
            "worst": float(r.min()), "pos_years": int((r > 0).sum()),
            "sharpe": float(r.mean() / vol) if vol else np.nan}


def main():
    p = load_panel()
    df, wide = annual_panel(p)
    m = membership()
    rm = df.pivot(index="year", columns="ticker", values="ret")

    def r_of(tk, y):
        if tk in rm.columns and y in rm.index:
            v = rm.at[y, tk]
            return float(v) if np.isfinite(v) else np.nan
        return np.nan

    def intra_dd(tk, y):
        d = p[(p.ticker == tk) & ((p.year == y) | (p.ym == f"{y-1}-12"))]
        if d.empty:
            return np.nan
        s = d.sort_values("ym")["adj"].values
        peak = np.maximum.accumulate(s)
        return float((s / peak - 1).min())

    CHAMPS["intra_dd"] = [intra_dd(t, y) for y, t in zip(CHAMPS.year, CHAMPS.ticker)]

    # ---- 2026 YTD ----
    dec25 = p[p.ym == "2025-12"].set_index("ticker")["adj"]
    cur = p[p.ym == p.ym.max()].set_index("ticker")
    ytd = []
    for tk in set(dec25.index) & set(cur.index):
        a, b = dec25[tk], cur.loc[tk, "adj"]
        if a > 0.1 and b > 0.1:
            ytd.append((tk, b / a - 1))
    ytd = pd.DataFrame(ytd, columns=["ticker", "ret"]).sort_values("ret", ascending=False)
    uni = pd.read_csv(os.path.join(DATA, "universe_all.csv"))
    nmap = dict(zip(uni["ticker"], uni["secname"]))
    mem26 = member_set(m, "2025-12-31")
    ytd["in_sp"] = ytd.ticker.isin(mem26)
    ytd["name"] = ytd.ticker.map(nmap).fillna("")
    ytd.head(30).to_csv(os.path.join(OUT, "ytd2026.csv"), index=False, encoding="utf-8-sig")
    print("== 2026 YTD (S&P500 members) ==")
    print(ytd[ytd.in_sp].head(8)[["ticker", "ret", "name"]].to_string(index=False))

    # ---- perfect foresight 2006-2025 ----
    r = CHAMPS.ret_published.values
    eq = np.cumprod(1 + r)
    pf = {"years": 20, "final_multiple": float(eq[-1]), "final_10k": float(eq[-1] * 10000),
          "cagr": float(eq[-1] ** (1 / 20) - 1), "best": float(r.max()), "worst": float(r.min()),
          "annual": [float(x) for x in r]}
    print(f"\nperfect foresight 2006-2025: 10000 -> {pf['final_10k']:,.0f}  x{pf['final_multiple']:,.0f}  CAGR {pf['cagr']*100:.1f}%")

    # ---- realistic 2009-2025 ----
    years = list(range(2009, 2026))
    ch = dict(zip(CHAMPS.year, CHAMPS.ticker))
    ann = pd.DataFrame({"year": years})
    ann["完美预知"] = [float(CHAMPS.loc[CHAMPS.year == y, "ret_published"].iloc[0]) for y in years]
    ann["追上年冠军"] = [r_of(ch.get(y - 1), y) for y in years]
    t10, t10names = [], []
    for y in years:
        prev = df[df.year == y - 1]
        mem = member_set(m, f"{y-2}-12-31")
        top = prev[prev.ticker.isin(mem)].nlargest(10, "ret").ticker.tolist()
        vals = [r_of(t, y) for t in top]
        vals = [v for v in vals if np.isfinite(v)]
        t10.append(float(np.mean(vals)) if vals else 0.0)
        t10names.append(",".join(top))
    ann["追上年Top10等权"] = t10
    ann["全市场等权"] = [float(df[df.year == y].ret.mean()) for y in years]
    spy = json.load(open(os.path.join(DATA, "sina2", "SPY.json"), encoding="utf-8"))
    spm = {b[0]: b[1] for b in spy["bars"]}
    ann["标普500"] = [spm[f"{y}-12"] / spm[f"{y-1}-12"] - 1 for y in years]
    ann.to_csv(os.path.join(OUT, "backtest_annual.csv"), index=False, encoding="utf-8-sig")
    pd.DataFrame({"year": years, "top10": t10names}).to_csv(
        os.path.join(OUT, "momentum_picks.csv"), index=False, encoding="utf-8-sig")

    sm = pd.DataFrame([perf(ann[c].values, c) for c in ann.columns[1:]])
    sm.to_csv(os.path.join(OUT, "backtest_summary.csv"), index=False, encoding="utf-8-sig")
    print("\n=== 2009-2025 ===")
    print(sm[["strategy", "final_multiple", "cagr", "vol", "max_dd", "best", "worst", "sharpe"]].round(4).to_string(index=False))
    print("\n", ann.round(4).to_string(index=False))

    stats = {
        "champion_median_ret": float(CHAMPS.ret_published.median()),
        "spx_median_ret": float(CHAMPS.spx.median()),
        "champ_beat_index_years": int((CHAMPS.ret_published > CHAMPS.spx).sum()),
        "median_intra_dd": float(np.nanmedian(CHAMPS.intra_dd)),
        "worst_intra_dd": float(np.nanmin(CHAMPS.intra_dd)),
        "n_semis": int(CHAMPS.sector.astype(str).str.contains("半导体").sum()),
        "median_stock_ret_by_year": {int(y): float(df[df.year == y].ret.median()) for y in years},
        "pct_beating_index": {int(y): float((df[df.year == y].ret > spm[f"{y}-12"] / spm[f"{y-1}-12"] - 1).mean()) for y in years},
    }
    CHAMPS["ret_next"] = [r_of(t, y + 1) for t, y in zip(CHAMPS.ticker, CHAMPS.year)]
    ok = CHAMPS.dropna(subset=["ret_next"])
    stats["champ_next_mean"] = float(ok.ret_next.mean())
    stats["champ_next_median"] = float(ok.ret_next.median())
    spx_by_year = dict(zip(CHAMPS.year, CHAMPS.spx))
    stats["champ_next_beat_spx"] = int(sum(ok.ret_next.iloc[i] > spx_by_year.get(int(ok.year.iloc[i]) + 1, -9) for i in range(len(ok))))
    stats["n_next"] = int(len(ok))
    stats["n_years"] = int(len(ann))
    CHAMPS.to_csv(os.path.join(OUT, "champions_final.csv"), index=False, encoding="utf-8-sig")
    json.dump(stats, open(os.path.join(OUT, "stats_final.json"), "w"), indent=2, ensure_ascii=False)
    json.dump({"perfect": pf}, open(os.path.join(OUT, "perfect.json"), "w"), indent=2, ensure_ascii=False)
    print("\n== stats ==")
    for k, v in stats.items():
        if not isinstance(v, (dict, list)):
            print(f"  {k}: {v}")
    print("\nchampion next year:")
    print(ok[["year", "ticker", "ret_published", "ret_next"]].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
