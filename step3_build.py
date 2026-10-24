# -*- coding: utf-8 -*-
"""Step 3: merge Sina (pre-2007 base) + Tencent (qfq 2007+) into one adjusted monthly panel."""
import os, json, glob
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
SINA = os.path.join(DATA, "sina")
TQ = os.path.join(DATA, "tq")


def load_sina():
    out = {}
    for f in glob.glob(os.path.join(SINA, "*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        tk = d["ticker"]
        bars = {b[0]: (b[1], b[2]) for b in d["bars"]}
        splits = [(s[0][:7], s[1]) for s in d.get("splits", [])]
        out[tk] = {"px": bars, "splits": splits}
    return out


def load_tq():
    out = {}
    for f in glob.glob(os.path.join(TQ, "*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        out[d["ticker"]] = {b[0]: b[1] for b in d["bars"]}
    return out


def build():
    sina = load_sina()
    tq = load_tq()
    print(f"sina tickers={len(sina)}  tencent tickers={len(tq)}")
    rows = []
    n_both = n_sina_only = 0
    for tk, s in sina.items():
        px = s["px"]
        if not px:
            continue
        months = sorted(px)
        # cumulative split multiplier
        mult, mult_map = 1.0, {}
        si = 0
        spl = sorted(s["splits"])
        for m in months:
            while si < len(spl) and spl[si][0] <= m:
                mult *= spl[si][1]
                si += 1
            mult_map[m] = mult
        adj = {m: px[m][0] * mult_map[m] for m in months}
        vol = {m: px[m][1] for m in months}
        t = tq.get(tk)
        if t:
            common = sorted(set(adj) & set(t))
            if common:
                m0 = common[0]
                b = t[m0] / adj[m0] if adj[m0] > 0 else np.nan
                for m in months:
                    if m >= m0:
                        adj[m] = t.get(m, np.nan)
                if np.isfinite(b) and b > 0:
                    for m in months:
                        if m < m0:
                            adj[m] *= b
                n_both += 1
            else:
                n_sina_only += 1
        else:
            n_sina_only += 1
        for m in months:
            v = adj[m]
            if v is None or not np.isfinite(v) or v <= 0:
                continue
            rows.append((tk, m, float(v), px[m][0], vol[m]))
    df = pd.DataFrame(rows, columns=["ticker", "ym", "adj", "raw", "vol"])
    df["year"] = df["ym"].str[:4].astype(int)
    print("panel rows:", len(df), "| both:", n_both, "| sina-only:", n_sina_only)
    df.to_pickle(os.path.join(DATA, "panel.pkl"))
    return df


if __name__ == "__main__":
    df = build()
    print(df.groupby("year")["ticker"].nunique().tail(25))
