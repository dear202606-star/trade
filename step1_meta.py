# -*- coding: utf-8 -*-
"""Step 1: build metadata tables (robust paging)."""
import os, json, time, io
import requests
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
os.makedirs(DATA, exist_ok=True)

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0",
                  "Referer": "https://quote.eastmoney.com/"})


def get(url, tries=8, timeout=40, **kw):
    last = None
    for i in range(tries):
        try:
            r = S.get(url, timeout=timeout, **kw)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(1.0 + i * 1.2)
    raise RuntimeError(f"GET failed {url} :: {last}")


def fetch_market(mk, label):
    rows, pn, total, fails = [], 1, None, 0
    while True:
        url = ("https://push2.eastmoney.com/api/qt/clist/get"
               f"?pn={pn}&pz=200&po=1&np=1&fltt=2&invt=2&fid=f12&fs={mk}"
               "&fields=f12,f14,f20,f2,f13")
        try:
            j = get(url, tries=5).json()
        except Exception as e:
            fails += 1
            print(f"  {label} pn={pn} skip ({e})")
            if fails > 6:
                break
            pn += 1
            time.sleep(2)
            continue
        d = j.get("data") or {}
        total = total or d.get("total")
        diff = d.get("diff") or []
        if not diff:
            break
        for it in diff:
            rows.append({"code": it.get("f12"), "name": it.get("f14"),
                         "mktcap": it.get("f20"), "price": it.get("f2"),
                         "market": label, "mkt_num": it.get("f13")})
        if total and pn * 200 >= total:
            break
        pn += 1
        time.sleep(0.25)
    print(f"{label}: total={total} downloaded={len(rows)}")
    return rows


if __name__ == "__main__":
    rows = []
    for mk, label in [("m:105", "NASDAQ"), ("m:106", "NYSE"), ("m:107", "AMEX")]:
        rows += fetch_market(mk, label)
    meta = pd.DataFrame(rows).drop_duplicates(subset=["code", "mkt_num"])
    meta.to_csv(os.path.join(DATA, "us_ticker_master.csv"), index=False, encoding="utf-8-sig")
    print("master rows:", len(meta))
    print(meta.groupby(["market", "mkt_num"]).size())

    # S&P 500 point-in-time membership
    repo = "https://raw.githubusercontent.com/fja05680/sp500/master/"
    se = get(repo + "sp500_ticker_start_end.csv").content.decode("utf-8", "ignore")
    open(os.path.join(DATA, "sp500_ticker_start_end.csv"), "w", encoding="utf-8").write(se)
    df_se = pd.read_csv(io.StringIO(se))
    print("start_end cols:", list(df_se.columns), "rows:", len(df_se))
    print(df_se.head(3).to_string())

    comp = get(repo + "S%26P%20500%20Historical%20Components%20%26%20Changes%20(Updated).csv").content
    open(os.path.join(DATA, "sp500_components_updated.csv"), "wb").write(comp)
    df_c = pd.read_csv(io.BytesIO(comp))
    print("components cols:", list(df_c.columns), "rows:", len(df_c))

    for sid in ["100.SPX", "100.NDX", "105.SPY", "100.DJIA"]:
        try:
            url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
                   f"?secid={sid}&fields1=f1,f2&fields2=f51,f53&klt=103&fqt=1&beg=19900101&end=20500101&lmt=100000")
            j = get(url).json()
            d = j.get("data") or {}
            kl = d.get("klines") or []
            print(sid, d.get("name"), "bars", len(kl), kl[-1] if kl else "")
        except Exception as e:
            print(sid, "FAIL", e)
