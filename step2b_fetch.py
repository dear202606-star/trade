# -*- coding: utf-8 -*-
"""Step 2b: polite, resumable fetch of monthly adjusted klines (eastmoney).

Priority: S&P500 point-in-time tickers first, then the rest of the universe.
Single-threaded token bucket with adaptive cooldown on rate-limit signals.
"""
import os, sys, json, time, random, argparse
import requests
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
KDIR = os.path.join(DATA, "klines")
os.makedirs(KDIR, exist_ok=True)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
FIELDS = "f51,f53,f56,f57"
SESS = requests.Session()
SESS.headers.update({"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"})


def fetch_one(ticker, prefix, beg="20030101", end="20500101", klt=103, fqt=2):
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
           f"?secid={prefix}.{ticker}&fields1=f1,f2,f3,f4,f5,f6&fields2={FIELDS}"
           f"&klt={klt}&fqt={fqt}&beg={beg}&end={end}&lmt=100000")
    r = SESS.get(url, timeout=20)
    if r.status_code != 200:
        return "RATE"
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


def build_tasks():
    uni = pd.read_csv(os.path.join(DATA, "universe_all.csv"))
    pri = set()
    p = os.path.join(DATA, "sp500_ticker_start_end.csv")
    if os.path.exists(p):
        pri = set(pd.read_csv(p)["ticker"].astype(str).str.strip())
    uni["prio"] = uni["ticker"].map(lambda t: 0 if t in pri else 1)
    # also attach SP500-only tickers absent from the current trading universe
    extra = [{"ticker": t, "secname": "", "exch": "?", "secid_prefix": 105, "prio": 0}
             for t in sorted(pri - set(uni["ticker"]))]
    uni = pd.concat([uni, pd.DataFrame(extra)], ignore_index=True)
    uni = uni.sort_values(["prio", "ticker"]).reset_index(drop=True)
    return uni


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float, default=1.6, help="requests per second target")
    ap.add_argument("--only-priority", action="store_true")
    ap.add_argument("--max-minutes", type=float, default=1e9)
    args = ap.parse_args()

    uni = build_tasks()
    if args.only_priority:
        uni = uni[uni["prio"] == 0]
    tasks = list(zip(uni["ticker"], uni["secid_prefix"].astype(int)))
    interval = 1.0 / args.rate
    t_start = time.time()
    ok = cached = miss = 0
    fails = 0

    for i, (tk, pref) in enumerate(tasks, 1):
        if (time.time() - t_start) / 60 > args.max_minutes:
            print("time budget reached", flush=True)
            break
        path = os.path.join(KDIR, f"{tk}.json")
        if os.path.exists(path) and os.path.getsize(path) > 40:
            cached += 1
            continue
        t0 = time.time()
        result = None
        for p in [pref] + [x for x in (105, 106, 107) if x != pref]:
            try:
                result = fetch_one(tk, p)
            except requests.exceptions.ConnectionError:
                fails += 1
                if fails <= 3:
                    print(f"  rate-limit signal, cooling 45s (ticker={tk})", flush=True)
                time.sleep(45)
                result = "RETRY"
                break
            except Exception:
                result = "RETRY"
                break
            if result == "RATE":
                time.sleep(45)
                result = "RETRY"
                break
            if isinstance(result, dict):
                break
            time.sleep(interval)
        if isinstance(result, dict):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False)
            ok += 1
            fails = 0
        elif result is None:
            miss += 1
        if i % 100 == 0:
            el = time.time() - t_start
            done = ok + miss
            print(f"{i}/{len(tasks)} new={ok} miss={miss} cached={cached} "
                  f"elapsed={el/60:.1f}m rate={done/max(el,1):.2f}/s", flush=True)
        sleep_for = interval - (time.time() - t0)
        if sleep_for > 0:
            time.sleep(sleep_for + random.random() * 0.1)

    print(f"DONE new={ok} miss={miss} cached={cached} elapsed={(time.time()-t_start)/60:.1f}m", flush=True)


if __name__ == "__main__":
    main()
