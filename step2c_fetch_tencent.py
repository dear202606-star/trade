# -*- coding: utf-8 -*-
"""Step 2c: Tencent monthly qfq klines for the whole universe (2007-03 -> today).

Saves compact yearly data to data/tq/{ticker}.json :
  {"ticker":..,"exch":..,"bars":[[ym, close], ...]}  (month-end closes, qfq)
"""
import os, sys, json, time, random, argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
ODIR = os.path.join(DATA, "tq")
os.makedirs(ODIR, exist_ok=True)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0"
SUFFIX = {"NASDAQ": "OQ", "NYSE": "N", "AMEX": "A"}


def fetch(tk, exch, tries=3):
    ex = SUFFIX.get(exch, "OQ")
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/usfqkline/get"
           f"?param=us{tk}.{ex},month,2005-01-01,2026-12-31,400,qfq")
    for i in range(tries):
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": UA})
            j = r.json()
            if j.get("code") != 0:
                return None
            d = list(j.get("data", {}).values())[0]
            k = d.get("qfqmonth") or d.get("month") or []
            if not k:
                return None
            bars = [[x[0][:7], float(x[2])] for x in k]
            return {"ticker": tk, "exch": exch, "bars": bars}
        except Exception:
            time.sleep(0.5 + i * 0.5 + random.random() * 0.3)
    return "ERR"


def work(args):
    tk, ex = args
    path = os.path.join(ODIR, f"{tk}.json")
    if os.path.exists(path) and os.path.getsize(path) > 20:
        return "cached"
    r = fetch(tk, ex)
    if isinstance(r, dict):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(r, f)
        return "ok"
    return "miss" if r is None else "err"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    uni = pd.read_csv(os.path.join(DATA, "universe_all.csv"))
    # append sp500-only tickers (delisted) - they will mostly miss
    tasks = list(zip(uni["ticker"], uni["exch"]))
    if a.limit:
        tasks = tasks[: a.limit]
    t0 = time.time()
    st = {"ok": 0, "cached": 0, "miss": 0, "err": 0}
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(work, t) for t in tasks]
        for i, fu in enumerate(as_completed(futs), 1):
            st[fu.result()] += 1
            if i % 500 == 0:
                el = time.time() - t0
                print(f"{i}/{len(tasks)} {st} {i/el:.1f}/s", flush=True)
    print("DONE", st, f"{(time.time()-t0)/60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
