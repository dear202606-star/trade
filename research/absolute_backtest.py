# -*- coding: utf-8 -*-
"""absolute_backtest.py — 追求「绝对正收益」：个股级风险过滤

基准组合：每年 12 月末按 12 个月动量取前 3 名（PIT 成分股 + 行业上限），全年持有。
问题：2011 年组合亏 26%（NFLX 一年跌 60%），大盘还是正收益 —— 风险在个股，不在大盘。

因此叠加层必须作用在**个股**上，而不是大盘或组合净值：

  A 基准            纯多头
  G 个股趋势        某只票跌破自己的 10 月均线 → 该只权重转现金
  H 个股趋势+大盘    G，且标普跌破 10 月均线时整体再减半
  I G + 波动率目标   H 之上按 6 个月实现波动缩放到年化 18%

全部信号都用「上月末」数据，无未来函数。
产物：docs/data/absolute.json
"""
import json
import os
import sys

import numpy as np
import pandas as pd

import datafeed

OUT = os.path.join("docs", "data", "absolute.json")
FINAL_N = 3
MIN_PRICE = 5.0
SECTOR_CAP = 0.60
MA_WIN = 10
TGT_VOL = 0.18
MAX_LEV = 1.5
LEV_COST = 0.005
CASH_RATE = 0.02


def ym_add(ym, k):
    i = int(ym[:4]) * 12 + int(ym[5:7]) + k
    y, m = divmod(i - 1, 12)
    return f"{y:04d}-{m+1:02d}"


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

    rows = []
    for y in range(2010, 2027):
        rank_ym = f"{y-1}-12"
        d = f"{y}-01-01"
        members = set(m[(m.start_date <= d) & (m.end_date >= d)]["ticker"])
        cand = []
        for t in members:
            bars = datafeed.load_bars(t)
            if not bars or rank_ym not in bars or bars[rank_ym][1] < MIN_PRICE:
                continue
            r = ret_12m(bars, rank_ym)
            if r is not None:
                cand.append((t, r))
        cand.sort(key=lambda x: -x[1])

        picks, sec_cnt = [], {}
        for t, r in cand:
            if len(picks) >= FINAL_N:
                break
            s = sector.get(t, "Unknown")
            if sec_cnt.get(s, 0) >= 2:
                continue
            picks.append((t, r, s))
            sec_cnt[s] = sec_cnt.get(s, 0) + 1
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

            base_r, trend_r = 0.0, 0.0
            ok = True
            for (t, _r, _s), wi in zip(picks, w):
                bars = datafeed.load_bars(t)
                if not bars or ym not in bars or prev not in bars:
                    ok = False
                    break
                a0, a1 = bars[prev][0], bars[ym][0]
                if not a0 or a0 <= 0:
                    ok = False
                    break
                ri = a1 / a0 - 1
                base_r += wi * ri
                # 个股趋势：上月末价 vs 前 10 个月均线
                months = [ym_add(prev, -j) for j in range(MA_WIN)]
                if all(x in bars for x in months):
                    ma = sum(bars[x][0] for x in months) / MA_WIN
                    on = bars[prev][0] >= ma
                else:
                    on = True
                trend_r += wi * (ri if on else CASH_RATE / 12)
            if not ok:
                continue

            sr = spy[ym][0] / spy[prev][0] - 1
            rows.append({"ym": ym, "year": y,
                         "base": base_r, "trend": trend_r, "spy": sr,
                         "hold": [p[0] for p in picks]})

    if not rows:
        print("无数据", file=sys.stderr)
        return 1

    df = pd.DataFrame(rows).drop_duplicates("ym").sort_values("ym").reset_index(drop=True)

    # 大盘趋势（上月末 SPY vs 10 月均线）
    spy_ser = (1 + df["spy"]).cumprod()
    mkt_ma = spy_ser.rolling(MA_WIN).mean()
    mkt_on = (spy_ser >= mkt_ma).shift(1).fillna(True).values

    # 波动率（6 个月，上月末）
    vol6 = df["trend"].rolling(6).std().shift(1).values * (12 ** 0.5)

    variants = {}
    variants["A"] = df["base"].values
    variants["G"] = df["trend"].values
    variants["H"] = np.array([
        r * 0.5 + 0.5 * CASH_RATE / 12 if not mkt_on[i] else r
        for i, r in enumerate(df["trend"].values)])
    vt = np.zeros(len(df))
    for i, r in enumerate(variants["H"]):
        v = vol6[i]
        k = min(MAX_LEV, TGT_VOL / v) if (v and v > 0 and not np.isnan(v)) else 1.0
        vt[i] = k * r - LEV_COST * max(0, k - 1) / 12
    variants["I"] = vt

    names = {"A": "纯多头（基准）", "G": "个股趋势过滤",
             "H": "个股趋势 + 大盘减半", "I": "H + 波动率目标 18%"}

    def st(r):
        eq = np.cumprod(1 + r)
        yrs = len(r) / 12
        cagr = eq[-1] ** (1 / yrs) - 1 if yrs > 0 else 0
        vol = np.std(r, ddof=1) * (12 ** 0.5)
        peak = np.maximum.accumulate(eq)
        return {"cagr": round(float(cagr), 4), "vol": round(float(vol), 4),
                "sharpe": round(float((np.mean(r) * 12) / vol), 3) if vol else 0,
                "mdd": round(float(np.min(eq / peak - 1)), 4),
                "total": round(float(eq[-1] - 1), 4),
                "win": round(float((r > 0).mean()), 4),
                # 滚动 12 个月正收益概率（更贴近“绝对收益”的定义）
                "r12_pos": round(float((pd.Series(np.cumprod(1 + r)).pct_change(12) > 0).mean()), 4),
                "r12_worst": round(float(pd.Series(np.cumprod(1 + r)).pct_change(12).min()), 4)}

    stats = {k: st(v) for k, v in variants.items()}

    years = sorted(df["year"].unique())
    by_year = []
    for y in years:
        idx = df.index[df["year"] == y].tolist()
        row = {"year": int(y)}
        for k, v in variants.items():
            row[k] = round(float(np.prod(1 + v[idx[0]:idx[-1] + 1]) - 1), 4)
        row["spy"] = round(float(np.prod(1 + df["spy"].values[idx[0]:idx[-1] + 1]) - 1), 4)
        by_year.append(row)

    neg = {k: [r["year"] for r in by_year if r[k] < 0] for k in variants}

    out = {"stats": stats, "names": names, "years": by_year, "neg_years": neg,
           "params": {"tgt_vol": TGT_VOL, "ma": MA_WIN, "cash": CASH_RATE,
                      "lev_cost": LEV_COST, "max_lev": MAX_LEV},
           "period": f"{df['ym'].iloc[0]} ~ {df['ym'].iloc[-1]}",
           "updated": pd.Timestamp.today().strftime("%Y-%m-%d")}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    print(f"\n区间 {out['period']}  {len(df)} 个月")
    print(f"{'方案':24s}{'年化':>8s}{'波动':>8s}{'夏普':>7s}{'回撤':>9s}{'亏损年':>7s}")
    for k in ("A", "G", "H", "I"):
        s = stats[k]
        print(f"{names[k]:24s}{s['cagr']*100:7.1f}%{s['vol']*100:7.1f}%{s['sharpe']:7.2f}"
              f"{s['mdd']*100:8.1f}%{len(neg[k]):7d}")
    for k in ("A", "G", "H", "I"):
        print(f"  {names[k]}: " + ("✅ 无亏损年" if not neg[k] else f"亏损年 {neg[k]}"))
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
