# -*- coding: utf-8 -*-
"""冻结口径 B 所需的月度行情（复权价 + 原始价）。

CI 无法稳定抓取已退市股票的日线（例如 2010 年的 XL、2022 年的 MRO），
导致同一策略在 CI 与本地选出不同组合。把「成分股 × 月度」的最小数据集
冻结成本地文件后，两边的排名池、收益、回撤完全一致。

产物：static/bars_monthly.json
  { "TICKER": [["YYYY-MM", 复权价, 原始价], ...], ... }
"""
import json
import os

import pandas as pd

import datafeed

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "static", "bars_monthly.json")


def main():
    mem = pd.read_csv(os.path.join(BASE, "static", "members_frozen.csv"), dtype=str)
    mem = mem.fillna("2099-12-31")
    # 只需要 2009 年以后还在指数里的票（回测 2010 起，排名看 2009-12）
    mem = mem[mem["end_date"] >= "2009-01-01"]
    tickers = sorted(set(mem["ticker"].tolist()))
    print("需要抓取的票：%d" % len(tickers), flush=True)

    datafeed.prefetch(tickers, workers=10)

    out = {}
    miss = []
    for t in tickers:
        bars = datafeed.load_bars(t)
        if not bars:
            miss.append(t)
            continue
        rows = []
        for ym in sorted(bars):
            adj, raw = bars[ym]
            if adj is None:
                continue
            rows.append([ym, round(float(adj), 4),
                         round(float(raw), 4) if raw else None])
        if rows:
            out[t] = rows

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    size = os.path.getsize(OUT) / 1e6
    print("已写入 %s：%d 只 / %.1f MB（缺失 %d 只）" % (OUT, len(out), size, len(miss)))
    if miss:
        print("缺失样例:", miss[:10])


if __name__ == "__main__":
    main()
