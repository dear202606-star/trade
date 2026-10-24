# -*- coding: utf-8 -*-
"""Step 2: fetch monthly adjusted klines from eastmoney into data/klines/{ticker}.json

Usage:
    python step2_fetch.py --limit 80          # sample test
    python step2_fetch.py                     # full universe
"""
import os, sys, json, time, random, argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
KDIR = os.path.join(DATA, "klines")
os.makedirs(KDIR, exist_ok=True)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
FIELDS = "f51,f53,f56,f57"  # date, close, volume, amount


def session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"})
    return s


def fetch_one(sess, ticker, prefix, beg="20030101", end="20500101", klt=103, fqt=2, tries=4):
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
           f"?secid={prefix}.{ticker}&fields1=f1,f2,f3,f4,f5,f6&fields2={FIELDS}"
           f"&klt={klt}&fqt={fqt}&beg={beg}&end={end}&lmt=100000")
    for i in range(tries):
        try:
            r = sess.get(url, timeout=20)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            j = r.json()
            d = j.get("data")
            if not d:
                return None
            kl = d.get("klines") or []
            if not kl:
                return None
            rows = []
            for line in kl:
                p = line.split(",")
                rows.append((p[0], float(p[1]), float(p[2] or 0), float(p[3] or 0)))
            return {"ticker": ticker, "secid": f"{prefix}.{ticker}", "name": d.get("name"), "bars": rows}
        except Exception:
            time.sleep(0.8 + i * 0.7 + random.random() * 0.4)
    return "ERR"


def work(ticker, prefix):
    path = os.path.join(KDIR, f"{ticker}.json")
    if os.path.exists(path) and os.path.getsize(path) > 40:
        return ("cached", ticker)
    sess = session()
    order = [prefix] + [p for p in (105, 106, 107) if p != prefix]
    for p in order:
        r = fetch_one(sess, ticker, p)
        if r == "ERR":
            continue
        if r is None:
            continue
        with open(path, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False)
        return ("ok", ticker)
    return ("miss", ticker)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    uni = pd.read_csv(os.path.join(DATA, "universe_all.csv"))
    tasks = list(zip(uni["ticker"], uni["secid_prefix"].astype(int)))
    if args.limit:
        tasks = tasks[: args.limit]

    t0 = time.time()
    stats = {"ok": 0, "cached": 0, "miss": 0}
    misses = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(work, t, p): t for t, p in tasks}
        for i, fu in enumerate(as_completed(futs), 1):
            st, tk = fu.result()
            stats[st] += 1
            if st == "miss":
                misses.append(tk)
            if i % 200 == 0:
                el = time.time() - t0
                print(f"{i}/{len(tasks)} ok={stats['ok']} cached={stats['cached']} miss={stats['miss']} "
                      f"{i/el:.2f}/s elapsed={el/60:.1f}min", flush=True)
    with open(os.path.join(DATA, "misses.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(misses)))
    el = time.time() - t0
    print(f"DONE {stats} in {el/60:.1f} min, rate={len(tasks)/el:.2f}/s")


if __name__ == "__main__":
    main()
