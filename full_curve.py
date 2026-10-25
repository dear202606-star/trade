# -*- coding: utf-8 -*-
"""full_curve.py — 全周期（2010 至今）月度净值曲线

产出 docs/data/curve.json：
  points: [{d: "2010-01", s: 策略累计净值, b: 标普累计净值, dd: 策略回撤}]
  stats:  累计/年化/最大回撤/胜率

规则与逐年记录完全一致：每年 12 月末按 12 个月动量取前 3 名（PIT 成分股 + 行业上限），
全年持有；月度收益由持仓加权得到，无未来函数。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

import datafeed

OUT = os.path.join("docs", "data", "curve.json")
FINAL_N = 3
MIN_PRICE = 5.0
SECTOR_CAP = 0.60


def ym_add(ym, k):
    i = int(ym[:4]) * 12 + int(ym[5:7]) + k
    y, m = divmod(i - 1, 12)
    return f"{y:04d}-{m + 1:02d}"


def ret_12m(bars, ym):
    base = ym_add(ym, -12)
    seq = [ym_add(ym, -12 + i) for i in range(13)]
    if any(s not in bars for s in seq):
        return None
    a0, a1 = bars[base][0], bars[ym][0]
    if not a0 or a0 <= 0 or not a1:
        return None
    prev = a0
    for s in seq:
        cur = bars[s][0]
        if prev > 0 and (cur / prev > 3 or cur / prev < 1 / 3):
            return None
        prev = cur
    r = a1 / a0 - 1
    return r if -0.99 < r < 10 else None


def main():
    m = datafeed.load_members()
    if m is None:
        print("缺成分表", file=sys.stderr)
        return 1
    m["start_date"] = m["start_date"].astype(str)
    m["end_date"] = m["end_date"].fillna("2099-12-31").astype(str)
    sector, _ = datafeed.load_sector()

    tickers = sorted(set(m["ticker"].dropna().astype(str)))
    print(f"预取 {len(tickers)} 只…", flush=True)
    datafeed.prefetch(tickers + ["SPY"], workers=10)

    spy = datafeed.load_bars("SPY")
    if not spy:
        print("缺 SPY", file=sys.stderr)
        return 1

    # 复用冻结的选股快照，保证曲线与分年记录完全一致
    frozen = {}
    _fp = os.path.join("output", "backtest_picks.json")
    if os.path.exists(_fp):
        try:
            frozen = {int(k): v for k, v in
                      (json.load(open(_fp, encoding="utf-8")).get("years") or {}).items()}
        except Exception:
            frozen = {}

    rows = []
    for y in range(2010, 2027):
        rank_ym = f"{y - 1}-12"
        d0 = f"{y}-01-01"
        members = set(m[(m.start_date <= d0) & (m.end_date >= d0)]["ticker"])
        cand = []
        for t in members:
            bars = datafeed.load_bars(t)
            if not bars or rank_ym not in bars or bars[rank_ym][1] < MIN_PRICE:
                continue
            r = ret_12m(bars, rank_ym)
            if r is not None:
                cand.append((t, r))
        cand.sort(key=lambda x: -x[1])

        if y in frozen:
            picks = [(t, 0.0, sector.get(t, "Unknown")) for t in frozen[y]]
        else:
            picks, sec = [], {}
            for t, r in cand:
                if len(picks) >= FINAL_N:
                    break
                sc = sector.get(t, "Unknown")
                if sec.get(sc, 0) >= 2:
                    continue
                picks.append((t, r, sc))
                sec[sc] = sec.get(sc, 0) + 1
        if len(picks) < FINAL_N:
            continue

        secs = [p[2] for p in picks]
        dup = [s for s, c in pd.Series(secs).value_counts().items() if c >= 2]
        if dup and len(picks) > 2:
            cap, rest = SECTOR_CAP / 2, 1 - SECTOR_CAP
            w = [cap if p[2] in dup else rest / max(1, len(picks) - 2) for p in picks]
            tot = sum(w)
            w = [x / tot for x in w]
        else:
            w = [1 / len(picks)] * len(picks)

        for k in range(1, 13):
            ym = f"{y}-{k:02d}"
            prev = ym_add(ym, -1)
            if ym not in spy or prev not in spy:
                continue
            pr, okm = 0.0, True
            for (t, _r, _s), wi in zip(picks, w):
                bars = datafeed.load_bars(t)
                if not bars or ym not in bars or prev not in bars:
                    okm = False
                    break
                a0, a1 = bars[prev][0], bars[ym][0]
                if not a0 or a0 <= 0:
                    okm = False
                    break
                pr += wi * (a1 / a0 - 1)
            if not okm:
                continue
            rows.append({"d": ym, "p": round(pr, 6),
                         "b": round(spy[ym][0] / spy[prev][0] - 1, 6)})

    if not rows:
        print("无数据", file=sys.stderr)
        return 1

    df = pd.DataFrame(rows).drop_duplicates("d").sort_values("d").reset_index(drop=True)
    s = (1 + df["p"]).cumprod()
    b = (1 + df["b"]).cumprod()
    peak = s.cummax()
    dd = s / peak - 1

    pts = [{"d": r.d, "s": round(float(s[i]), 4), "b": round(float(b[i]), 4),
            "dd": round(float(dd[i]), 4)}
           for i, r in df.iterrows()]

    yrs = len(df) / 12
    total = float(s.iloc[-1] - 1)
    cagr = float(s.iloc[-1] ** (1 / yrs) - 1) if yrs > 0 else 0
    mdd = float(dd.min())
    vol = float(df["p"].std() * (12 ** 0.5))
    stats = {
        "total": round(total, 4), "cagr": round(cagr, 4), "mdd": round(mdd, 4),
        "vol": round(vol, 4),
        "sharpe": round(float(df["p"].mean() * 12 / vol), 3) if vol else None,
        "bench_total": round(float(b.iloc[-1] - 1), 4),
        "bench_cagr": round(float(b.iloc[-1] ** (1 / yrs) - 1), 4),
        "months": len(df), "start": df["d"].iloc[0], "end": df["d"].iloc[-1],
        "win": round(float((df["p"] > 0).mean()), 4),
    }

    # 按自然年聚合（与逐年记录同一口径）
    yb = []
    for y, g in df.groupby(df["d"].str[:4]):
        s_eq = float((1 + g["p"]).prod())
        b_eq = float((1 + g["b"]).prod())
        # 年内回撤（用年内累计净值）
        path = (1 + g["p"]).cumprod()
        ipeak = path.cummax()
        ydd = float((path / ipeak - 1).min())
        yb.append({"year": int(y), "ret": round(s_eq - 1, 4),
                   "bench": round(b_eq - 1, 4), "dd": round(ydd, 4)})

    out = {"points": pts, "stats": stats, "years": yb,
           "updated": pd.Timestamp.today().strftime("%Y-%m-%d")}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    print(f"\n全周期 {stats['start']} ~ {stats['end']}（{stats['months']} 个月）")
    print(f"  策略 累计 {total * 100:.0f}%  年化 {cagr * 100:.1f}%  "
          f"回撤 {mdd * 100:.1f}%  波动 {vol * 100:.1f}%  夏普 {stats['sharpe']}")
    print(f"  标普 累计 {stats['bench_total'] * 100:.0f}%  年化 {stats['bench_cagr'] * 100:.1f}%")
    print(f"已写入 {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
