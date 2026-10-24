# -*- coding: utf-8 -*-
"""Step 3: compute calendar-year returns, pick yearly champions, run backtests."""
import os, json, glob
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
KDIR = os.path.join(DATA, "klines")
OUT = os.path.join(BASE, "output")
os.makedirs(OUT, exist_ok=True)

YEARS = list(range(2005, 2027))          # need 2005 as base year for 2006 return
BACKTEST_YEARS = list(range(2006, 2026))  # 20 full years


def load_all():
    recs = []
    meta = []
    for path in glob.glob(os.path.join(KDIR, "*.json")):
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        tk = d["ticker"]
        bars = d["bars"]
        if len(bars) < 13:
            continue
        meta.append({"ticker": tk, "name": d.get("name"), "secid": d.get("secid"),
                     "n_bars": len(bars), "first": bars[0][0], "last": bars[-1][0]})
        for b in bars:
            recs.append((tk, b[0], b[1], b[2], b[3]))
    df = pd.DataFrame(recs, columns=["ticker", "date", "close", "volume", "amount"])
    df["ym"] = df["date"].str[:7]
    df["year"] = df["date"].str[:4].astype(int)
    meta = pd.DataFrame(meta)
    return df, meta


def year_end_table(df):
    """Last available bar of each ticker-year: close, amount; plus month of that bar."""
    g = df.groupby(["ticker", "year"]).agg(
        close=("close", "last"), amount=("amount", "last"),
        ym=("ym", "last"), nmonth=("ym", "count"),
    ).reset_index()
    g["last_month"] = g["ym"].str[5:7].astype(int)
    return g


def annual_returns(ye):
    """Wide table: ticker x year -> return (%), plus amount-12m and eligibility flags."""
    ret = {}
    ye_idx = ye.set_index(["ticker", "year"])

    def get(tk, y, col):
        try:
            return ye_idx.loc[(tk, y), col]
        except KeyError:
            return np.nan

    tickers = ye["ticker"].unique()
    rows = []
    for tk in tickers:
        sub = ye[ye["ticker"] == tk].sort_values("year")
        py = sub.set_index("year")
        for y in YEARS:
            if y not in py.index or (y - 1) not in py.index:
                continue
            cur, prev = py.loc[y], py.loc[y - 1]
            if isinstance(cur, pd.DataFrame) or isinstance(prev, pd.DataFrame):
                continue
            if prev["close"] <= 0 or cur["close"] <= 0:
                continue
            # must have traded through both year-ends (Dec bar present)
            if prev["last_month"] != 12 or cur["last_month"] != 12:
                continue
            rows.append({
                "ticker": tk, "year": y,
                "ret": cur["close"] / prev["close"] - 1.0,
                "close_end": cur["close"],
                "amt_12m": py.loc[[yy for yy in range(y - 1, y) if yy in py.index], "amount"].mean(),
            })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    df, meta = load_all()
    print("tickers:", meta.shape[0], "| bars:", df.shape[0])
    ye = year_end_table(df)
    ar = annual_returns(ye)
    ar = ar.merge(meta[["ticker", "name"]], on="ticker", how="left")

    # investability filters
    ar["liquid"] = ar["amt_12m"] >= 20e6          # >= $20M avg monthly turnover
    print("rows:", ar.shape)
    ar.to_csv(os.path.join(OUT, "annual_returns.csv"), index=False, encoding="utf-8-sig")

    for y in BACKTEST_YEARS:
        sub = ar[(ar["year"] == y) & ar["liquid"]].nlargest(5, "ret")
        tot = (ar["year"] == y).sum()
        print(f"\n{y}  eligible={tot:4d} liquid={((ar['year']==y)&ar['liquid']).sum():4d}")
        for _, r in sub.iterrows():
            print(f"   {r['ticker']:<6} {str(r['name'])[:14]:<16} {r['ret']*100:9.1f}%")
