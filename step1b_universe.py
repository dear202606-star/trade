# -*- coding: utf-8 -*-
"""Step 1b: build universe from NASDAQ Trader symbol directory + S&P500 point-in-time list."""
import os, io, re, time
import requests
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
os.makedirs(DATA, exist_ok=True)

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0"})


def get(url, tries=6, timeout=40):
    last = None
    for i in range(tries):
        try:
            r = S.get(url, timeout=timeout)
            if r.status_code == 200:
                return r
        except Exception as e:
            last = e
        time.sleep(1.5 + i)
    raise RuntimeError(f"GET failed {url}: {last}")


BAD_SUFFIX = re.compile(r"(W|R|U|S)$")


def clean_syms(df, col):
    s = df[col].astype(str).str.strip()
    ok = s.str.match(r"^[A-Z]{1,5}$")
    return df[ok].copy()


# ---------- NASDAQ listed ----------
nas = pd.read_csv(io.StringIO(get("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt").text), sep="|")
nas = nas[nas["Symbol"].notna()]
nas = nas[nas["Test Issue"] == "N"]
nas = nas[nas["ETF"] == "N"]
nas = clean_syms(nas, "Symbol")
nas["exch"] = "NASDAQ"
nas["secid_prefix"] = 105
nas = nas.rename(columns={"Symbol": "ticker", "Security Name": "secname"})[["ticker", "secname", "exch", "secid_prefix"]]

# ---------- Other listed (NYSE / AMEX) ----------
oth = pd.read_csv(io.StringIO(get("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt").text), sep="|")
oth = oth[oth["ACT Symbol"].notna()]
oth = oth[oth["Test Issue"] == "N"]
oth = oth[oth["ETF"] == "N"]
oth = clean_syms(oth, "ACT Symbol")
MKT = {"N": ("NYSE", 106), "A": ("AMEX", 107)}
oth = oth[oth["Exchange"].isin(MKT.keys())].copy()
oth["exch"] = oth["Exchange"].map(lambda x: MKT[x][0])
oth["secid_prefix"] = oth["Exchange"].map(lambda x: MKT[x][1])
oth = oth.rename(columns={"ACT Symbol": "ticker", "Security Name": "secname"})[["ticker", "secname", "exch", "secid_prefix"]]

uni = pd.concat([nas, oth]).drop_duplicates(subset=["ticker"]).reset_index(drop=True)
uni.to_csv(os.path.join(DATA, "universe_all.csv"), index=False, encoding="utf-8-sig")
print("universe (common stocks, non-ETF):", len(uni))
print(uni.groupby("exch").size())

# ---------- S&P 500 point-in-time ----------
repo = "https://raw.githubusercontent.com/fja05680/sp500/master/"
se = get(repo + "sp500_ticker_start_end.csv").content.decode("utf-8", "ignore")
open(os.path.join(DATA, "sp500_ticker_start_end.csv"), "w", encoding="utf-8").write(se)
df_se = pd.read_csv(io.StringIO(se))
print("sp500_ticker_start_end cols:", list(df_se.columns), len(df_se))

comp = get(repo + "S%26P%20500%20Historical%20Components%20%26%20Changes%20(Updated).csv").content
open(os.path.join(DATA, "sp500_components_updated.csv"), "wb").write(comp)
df_c = pd.read_csv(io.BytesIO(comp))
print("components cols:", list(df_c.columns), len(df_c))
print(df_c.tail(2).to_string()[:500])

# union of sp500 tickers -> add missing ones to a "sp500" flag list
sp_tickers = set(df_se.iloc[:, 0].astype(str).str.strip())
miss = sorted(sp_tickers - set(uni["ticker"]))
print("SP500 tickers not in universe:", len(miss), miss[:20])
