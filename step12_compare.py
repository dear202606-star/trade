# -*- coding: utf-8 -*-
"""Step 12: 前一年涨幅第1/2/3名 -> 次年持有：7 个模型对比"""
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
FY0, FY1 = 2009, 2025          # 回测年份
RY0, RY1 = 2008, 2025          # 排名年份（需要上一年）


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
    for y in range(RY0, RY1 + 1):
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

    uni = pd.read_csv(os.path.join(DATA, "universe_all.csv"))
    nmap = dict(zip(uni["ticker"], uni["secname"]))

    ranks = {}
    for y in range(RY0, RY1 + 1):
        sub = df[(df.year == y) & (df.ticker.isin(mem_any(y)))].sort_values("ret", ascending=False)
        ranks[y] = sub.head(12)[["ticker", "ret"]].reset_index(drop=True)

    # 次年收益
    def r_of(tk, y):
        if tk not in wide.index or y not in wide.columns or (y - 1) not in wide.columns:
            return np.nan
        a, b = wide.at[tk, y - 1], wide.at[tk, y]
        return float(b / a - 1) if np.isfinite(a) and np.isfinite(b) and a > 0 else np.nan

    spy = json.load(open(os.path.join(DATA, "sina2", "SPY.json"), encoding="utf-8"))
    spm = {b[0]: b[1] for b in spy["bars"]}

    def spx(y):
        a, b = spm.get(f"{y-1}-12"), spm.get(f"{y}-12")
        return (b / a - 1) if a and b else np.nan

    def pick(y, k):
        r = ranks.get(y - 1)
        if r is None or len(r) < k:
            return None
        return r.ticker.iloc[k - 1]

    def ret_of_set(y, ks):
        vals, names = [], []
        for k in ks:
            tk = pick(y, k)
            if tk is None:
                continue
            v = r_of(tk, y)
            if np.isfinite(v):
                vals.append(v)
                names.append(tk)
        return (float(np.mean(vals)) if vals else np.nan), names

    models = {
        "M1 只买第1名": [1],
        "M2 只买第2名": [2],
        "M3 只买第3名": [3],
        "M4 第1+第2": [1, 2],
        "M5 第1+第2+第3": [1, 2, 3],
        "M6 前10名等权": list(range(1, 11)),
    }
    ann = pd.DataFrame({"year": range(FY0, FY1 + 1)})
    picks_detail = {}
    for name, ks in models.items():
        vals, det = [], []
        for y in ann.year:
            v, names = ret_of_set(y, ks)
            vals.append(v)
            det.append(",".join(names))
        ann[name] = vals
        picks_detail[name] = det
    ann["SPY"] = [spx(y) for y in ann.year]

    # 50/30/20 加权
    w, v3 = np.array([0.5, 0.3, 0.2]), []
    for y in ann.year:
        rs = [r_of(pick(y, k), y) for k in (1, 2, 3)]
        ok = [(a, b) for a, b in zip(w, rs) if np.isfinite(b)]
        v3.append(float(sum(a * b for a, b in ok) / sum(a for a, _ in ok)) if ok else np.nan)
    ann["M7 第1/2/3名加权(50/30/20)"] = v3

    ann.to_csv(os.path.join(OUT, "compare_annual.csv"), index=False, encoding="utf-8-sig")

    # 统计
    def stats(s, name):
        s = pd.Series(s).dropna()
        eq = (1 + s.values).cumprod()
        peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
        yrs = len(s)
        return {"模型": name, "年数": yrs, "累计倍数": eq[-1], "年化": eq[-1] ** (1 / yrs) - 1,
                "年化波动": s.std(ddof=1), "最大回撤": float((eq / peak - 1).min()),
                "最好年": s.max(), "最差年": s.min(), "夏普": s.mean() / s.std(ddof=1),
                "Calmar": (eq[-1] ** (1 / yrs) - 1) / abs((eq / peak - 1).min()),
                "胜率vsSPY": float((s.values > ann.loc[s.index, "SPY"].values).mean())}

    tb = pd.DataFrame([stats(ann[c], c) for c in ann.columns if c != "year"])
    tb = tb.sort_values("年化", ascending=False)
    tb.to_csv(os.path.join(OUT, "compare_summary.csv"), index=False, encoding="utf-8-sig")
    print(tb.round(4).to_string(index=False))

    print("\n每年持仓：")
    print(pd.DataFrame({"year": ann.year, **{k: picks_detail[k] for k in models},
                        "SPY": ann.SPY}).to_string(index=False))

    print("\n逐年收益：")
    print(ann.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
