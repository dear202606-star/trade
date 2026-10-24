# -*- coding: utf-8 -*-
"""Step 9: true intra-year drawdown of each champion using daily bars."""
import os, json, re, time
import requests
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "output")

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"})
PAT = re.compile(r"\(\s*(\[.*\])\s*\)", re.S)


def daily(tk):
    cache = os.path.join(DATA, "daily", f"{tk}.json")
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    if os.path.exists(cache):
        return json.load(open(cache, encoding="utf-8"))
    r = S.get(("https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/x/"
               f"US_MinKService.getDailyK?symbol={tk}"), timeout=40)
    arr = json.loads(PAT.search(r.text).group(1))
    json.dump(arr, open(cache, "w"), ensure_ascii=False)
    return arr


def main():
    ch = pd.read_csv(os.path.join(OUT, "champions_final.csv"))
    dds = []
    for _, row in ch.iterrows():
        tk, y = row.ticker, int(row.year)
        try:
            arr = daily(tk)
        except Exception as e:
            dds.append(np.nan)
            continue
        sp = json.load(open(os.path.join(DATA, "sina2", f"{tk}.json"), encoding="utf-8"))["splits"]
        sp = sorted([(s[0], s[1]) for s in sp])
        px = []
        for x in arr:
            if not x["d"][:4].isdigit():
                continue
            d = x["d"]
            if not (f"{y-1}-12-01" <= d <= f"{y}-12-31"):
                continue
            f = 1.0
            for sd, K in sp:
                if sd <= d:
                    f *= K
            px.append((d, float(x["c"]) * f))
        if len(px) < 20:
            dds.append(np.nan)
            continue
        s = np.array([p[1] for p in px])
        peak = np.maximum.accumulate(s)
        dds.append(float((s / peak - 1).min()))
        print(f"{y} {tk:6s} DD {dds[-1]*100:7.1f}%  days={len(px)}")
        time.sleep(0.2)
    ch["intra_dd"] = dds
    ch.to_csv(os.path.join(OUT, "champions_final.csv"), index=False, encoding="utf-8-sig")

    st = json.load(open(os.path.join(OUT, "stats_final.json"), encoding="utf-8"))
    st["median_intra_dd"] = float(np.nanmedian(dds))
    st["worst_intra_dd"] = float(np.nanmin(dds))
    st["intra_dd_basis"] = "daily"
    json.dump(st, open(os.path.join(OUT, "stats_final.json"), "w"), indent=2, ensure_ascii=False)
    print("median", round(st["median_intra_dd"] * 100, 1), "worst", round(st["worst_intra_dd"] * 100, 1))


if __name__ == "__main__":
    main()
