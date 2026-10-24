# -*- coding: utf-8 -*-
"""Step 2d: Sina full-history daily -> compact month-end closes (streaming).

For each ticker store:
  {"ticker":.., "bars": [[ym, close, month_volume], ...],
   "splits": [[date, factor], ...]}   # factor = prev_close/today_close style ratio
Only month-end (last trading day of each month) closes are kept, plus detected splits.
"""
import os, sys, json, re, time, argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
ODIR = os.path.join(DATA, "sina")
os.makedirs(ODIR, exist_ok=True)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0"
PAT = re.compile(r"\(\s*(\[.*\])\s*\)", re.S)
NICE = [1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 25, 30, 40, 50]


def seg(sess, tk):
    url = ("https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/x/"
           f"US_MinKService.getDailyK?symbol={tk}")
    r = sess.get(url, timeout=30)
    m = PAT.search(r.text)
    if not m:
        return None
    try:
        arr = json.loads(m.group(1))
    except Exception:
        return None
    if not arr:
        return None
    out, splits = [], []
    prev_c = prev_v = None
    cur_ym, cur_c, cur_v = None, None, 0.0
    for x in arr:
        try:
            d = x["d"]
            c = float(x["c"])
            v = float(x.get("v") or 0)
        except Exception:
            continue
        if c <= 0:
            continue
        # split detection
        if prev_c and prev_c > 0:
            k = prev_c / c
            if 1.45 <= k <= 55 and prev_v and prev_v > 0:
                vr = (v / prev_v) / k
                if 0.55 <= vr <= 1.8:
                    near = min(NICE, key=lambda z: abs(z - k))
                    if abs(near - k) / k <= 0.08:
                        splits.append([d, round(near, 4)])
        ym = d[:7]
        if ym != cur_ym:
            if cur_ym:
                out.append([cur_ym, cur_c, cur_v])
            cur_ym, cur_v = ym, 0.0
        cur_c, cur_v = c, cur_v + v
        prev_c, prev_v = c, v
    if cur_ym:
        out.append([cur_ym, cur_c, cur_v])
    return {"ticker": tk, "bars": out, "splits": splits}


def work(tk):
    path = os.path.join(ODIR, f"{tk.replace('.', '_')}.json")
    if os.path.exists(path) and os.path.getsize(path) > 40:
        return "cached"
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": "https://finance.sina.com.cn"})
    for i in range(3):
        try:
            r = seg(s, tk)
            if r and len(r["bars"]) >= 6:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(r, f)
                return "ok"
            if r is not None:
                return "short"
        except Exception:
            time.sleep(0.6 + i)
    return "miss"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    uni = pd.read_csv(os.path.join(DATA, "universe_all.csv"))
    tks = list(uni["ticker"])
    p = os.path.join(DATA, "sp500_ticker_start_end.csv")
    if os.path.exists(p):
        extra = sorted(set(pd.read_csv(p)["ticker"].astype(str).str.strip()) - set(tks))
        tks = tks + extra
    if a.limit:
        tks = tks[: a.limit]
    t0 = time.time()
    st = {}
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(work, t) for t in tks]
        for i, fu in enumerate(as_completed(futs), 1):
            k = fu.result()
            st[k] = st.get(k, 0) + 1
            if i % 500 == 0:
                print(f"{i}/{len(tks)} {st} {(i)/(time.time()-t0):.1f}/s", flush=True)
    print("DONE", st, f"{(time.time()-t0)/60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
