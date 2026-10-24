# -*- coding: utf-8 -*-
"""Step 3b: build panel from split-adjusted Sina data (single source)."""
import os, json, glob
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
S2 = os.path.join(DATA, "sina2")
ETF = {"SPY", "QQQ", "IWM", "DIA"}


def build():
    rows = []
    for f in glob.glob(os.path.join(S2, "*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        tk = d["ticker"]
        if tk in ETF:
            continue
        for b in d["bars"]:
            if b[1] > 0:
                rows.append((tk, b[0], float(b[1]), float(b[2]), float(b[3])))
    df = pd.DataFrame(rows, columns=["ticker", "ym", "adj", "raw", "vol"])
    df["year"] = df["ym"].str[:4].astype(int)
    df["dv"] = df["raw"] * df["vol"]
    df.to_pickle(os.path.join(DATA, "panel2.pkl"))
    print("panel rows:", len(df), "tickers:", df.ticker.nunique())
    print(df.groupby("year").size().loc[2003:2012].to_string())
    return df


if __name__ == "__main__":
    build()
