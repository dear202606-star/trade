# -*- coding: utf-8 -*-
"""probe_yahoo.py — 实测 Yahoo chart API 的字段语义

要回答三个问题：
  1. close 字段是原始价还是已按拆股调整？
  2. splits 里的 numerator/denominator 方向如何？
  3. 月线 timestamp 是月初还是月末？
"""
import json

import requests

import market_data as md

UA = md.UA


def raw_payload(tk, years=26):
    import time
    now = int(time.time())
    start = now - int(years * 365.25 * 86400)
    sym = tk.replace(".", "-")
    url = (f"https://query2.finance.yahoo.com/v8/finance/chart/{sym}"
           f"?period1={start}&period2={now}&interval=1mo&events=split")
    r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
    return r.json()


def main():
    out = []
    for tk in ["AAPL", "TSLA", "KLAC", "NVDA", "MNST"]:
        try:
            j = raw_payload(tk)
            res = (j.get("chart") or {}).get("result") or []
            if not res:
                out.append(f"{tk}: 无数据")
                continue
            res = res[0]
            ts = res.get("timestamp") or []
            closes = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
            adj = (((res.get("indicators") or {}).get("adjclose") or [{}])[0]).get("adjclose") or []
            splits = ((res.get("events") or {}).get("splits")) or {}

            import datetime as dt
            out.append(f"\n=== {tk} ===")
            out.append(f"月线根数 {len(ts)}；首个 timestamp {ts[0]} -> {dt.datetime.utcfromtimestamp(ts[0]).date()}")
            for k, v in splits.items():
                out.append(f"  拆股 {dt.datetime.utcfromtimestamp(int(v['date'])).date()} "
                           f"num={v['numerator']} den={v['denominator']} ratio={v.get('splitRatio')}")
            # 打印拆股前后 3 个月的 close / adjclose
            for sv in splits.values():
                sd = int(sv["date"])
                idx = [i for i, t in enumerate(ts) if abs(t - sd) < 45 * 86400]
                for i in idx:
                    d = dt.datetime.utcfromtimestamp(ts[i]).date()
                    c = closes[i] if i < len(closes) else None
                    a = adj[i] if i < len(adj) else None
                    out.append(f"    {d} close={c} adjclose={a}")

            # 与新浪原始收盘对比（取最近 3 个月）
            sina = md.fetch_sina(tk)
            if sina:
                out.append("    新浪最近 3 月 raw: " +
                           ", ".join(f"{r[0]}={r[2]:.2f}" for r in sina[-3:]))
            out.append("    Yahoo 最近 3 月 close: " +
                       ", ".join(f"{dt.datetime.utcfromtimestamp(ts[i]).strftime('%Y-%m')}="
                                 f"{closes[i]:.2f}" for i in range(max(0, len(ts) - 3), len(ts))))
        except Exception as e:
            out.append(f"{tk}: 异常 {e}")

    txt = "\n".join(out)
    with open("output/yahoo_probe.txt", "w", encoding="utf-8") as f:
        f.write(txt)
    print(txt)


if __name__ == "__main__":
    main()
