# -*- coding: utf-8 -*-
"""strategy_history.py — 生成逐年策略记录（无未来函数）

对每年 Y（2010 起）：
  1. 排序基准日 = Y-1 年 12 月；股票池 = Y 年 1 月 1 日的标普 500 成分股
  2. 取 12 个月动量前 3 名（同行业最多 2 只、行业权重上限 60%）
  3. 计算该组合在 Y 年的收益、年内最大回撤、与标普 500 对比
  4. 记录持仓、权重、是否发生换仓

产物：docs/data/strategy_history.json
  {years:[{year, holdings:[{ticker,name,sector,weight,ret12_at_pick,ret_year}],
           ret, dd, bench, switched}], stocks:[{ticker, years, total_ret, appear}]}
"""
import json
import os
import sys

import pandas as pd

import datafeed

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "data", "sina2")
MEMBERS = os.path.join(BASE, "data", "sp500_ticker_start_end.csv")
SP500 = os.path.join(BASE, "data", "sp500.csv")
OUT = os.path.join(BASE, "docs", "data", "strategy_history.json")

FINAL_N = 3
MIN_PRICE = 5.0
SECTOR_CAP = 0.60


def load_bars(t):
    import datafeed
    return datafeed.load_bars(t)


def ret_12m(bars, ym):
    """返回 (12个月收益, 是否合格)"""
    i = int(ym[:4]) * 12 + int(ym[5:7])
    base = f"{i//12:04d}-{i%12 or 12:02d}"
    # 精确算：ym 往前 12 个月
    y, m = int(ym[:4]), int(ym[5:7])
    y2, m2 = (y - 1, m)
    base = f"{y2:04d}-{m2:02d}"
    seq = []
    yy, mm = y2, m2
    for _ in range(13):
        seq.append(f"{yy:04d}-{mm:02d}")
        mm += 1
        if mm > 12:
            mm = 1
            yy += 1
    if any(s not in bars for s in seq):
        return None, False
    adj0 = bars[base][0]
    adj1 = bars[ym][0]
    if not adj0 or adj0 <= 0 or not adj1:
        return None, False
    prev = adj0
    for s in seq:
        cur = bars[s][0]
        # 单月超过 3 倍必然有问题（真实拆股要么已复权，要么就是漏检）
        if prev > 0 and (cur / prev > 3 or cur / prev < 1 / 3):
            return None, False
        prev = cur
    r = adj1 / adj0 - 1
    if r > 10:            # 12 个月涨 10 倍以上，视为数据异常
        return None, False
    return r, True


def pick_for_year(y, members, sector):
    cand = []
    rank_ym = f"{y-1}-12"
    for t in members:
        bars = load_bars(t)
        if not bars or rank_ym not in bars:
            continue
        price = bars[rank_ym][1]
        if price < MIN_PRICE:
            continue
        r, ok = ret_12m(bars, rank_ym)
        if not ok:
            continue
        cand.append((t, r, price))
    cand.sort(key=lambda x: -x[1])

    picks, sec_cnt = [], {}
    for t, r, px in cand:
        if len(picks) >= FINAL_N:
            break
        s = sector.get(t, "Unknown")
        if sec_cnt.get(s, 0) >= 2:
            continue
        picks.append({"ticker": t, "ret12": r, "sector": s})
        sec_cnt[s] = sec_cnt.get(s, 0) + 1

    if not picks:
        return []
    secs = [p["sector"] for p in picks]
    dup = [s for s, c in pd.Series(secs).value_counts().items() if c >= 2 and len(picks) > 2]
    if dup:
        cap, rest = SECTOR_CAP / 2, 1 - SECTOR_CAP
        w = [cap if p["sector"] in dup else rest / max(1, len(picks) - 2) for p in picks]
        tot = sum(w)
        w = [x / tot for x in w]
    else:
        w = [1 / len(picks)] * len(picks)
    for p, wi in zip(picks, w):
        p["weight"] = round(wi, 4)
    return picks


def year_perf(y, picks):
    """组合在 Y 年的收益与年内最大回撤（月末）"""
    seq = [f"{y}-{m:02d}" for m in range(1, 13)]
    prev_ym = f"{y-1}-12"
    curves, ok = [], True
    for p in picks:
        bars = load_bars(p["ticker"])
        if not bars or prev_ym not in bars:
            ok = False
            break
        p["_bars"] = bars

    if not ok:
        return None, None, None

    vals = []
    for m in seq:
        v = 0.0
        for p in picks:
            bars = p["_bars"]
            if m not in bars:
                # 用上一个有效月
                cand_m = [k for k in sorted(bars) if k <= m]
                if not cand_m:
                    v = None
                    break
                px = bars[cand_m[-1]][0]
            else:
                px = bars[m][0]
            base = bars[prev_ym][0]
            if not base or base <= 0:
                v = None
                break
            v += p["weight"] * (px / base)
        if v is None:
            return None, None, None
        vals.append(v)

    ret = vals[-1] - 1
    peak, mdd = 0, 0
    for v in [1.0] + vals:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)

    # 每只股票在 Y 年的收益
    for p in picks:
        bars = p.pop("_bars", None)
        if bars and prev_ym in bars and f"{y}-12" in bars:
            p["ret_year"] = round(bars[f"{y}-12"][0] / bars[prev_ym][0] - 1, 4)
        else:
            p["ret_year"] = None
    return round(ret, 4), round(mdd, 4), True


def main():
    if not os.path.exists(MEMBERS):
        print("缺 成分股时点表", file=sys.stderr)
        return 1

    m = pd.read_csv(MEMBERS)
    m["start_date"] = m["start_date"].astype(str)
    m["end_date"] = m["end_date"].fillna("2099-12-31").astype(str)
    sector, name_map = datafeed.load_sector()
    if not sector:
        print("WARN: 行业表不可用，按等权处理", file=sys.stderr)

    spy = load_bars("SPY")
    years, prev_set = [], None
    for y in range(2010, 2026):
        d = f"{y}-01-01"
        members = set(m[(m.start_date <= d) & (m.end_date >= d)]["ticker"])
        picks = pick_for_year(y, members, sector)
        if not picks:
            continue
        ret, dd, ok = year_perf(y, picks)
        bench = None
        if spy and f"{y-1}-12" in spy and f"{y}-12" in spy:
            bench = round(spy[f"{y}-12"][0] / spy[f"{y-1}-12"][0] - 1, 4)
        cur_set = tuple(sorted(p["ticker"] for p in picks))
        switched = (prev_set is not None and cur_set != prev_set)
        prev_set = cur_set
        for p in picks:
            p["name"] = str(name_map.get(p["ticker"], p["ticker"]))[:32]
            p["ret12"] = round(p["ret12"], 4)
        years.append({"year": y, "holdings": picks, "ret": ret, "dd": dd,
                      "bench": bench, "switched": switched,
                      "n_members": len(members)})

    # 分股票统计
    stat = {}
    for r in years:
        for p in r["holdings"]:
            s = stat.setdefault(p["ticker"], {"ticker": p["ticker"], "name": p["name"],
                                              "years": [], "rets": [], "sector": p["sector"]})
            s["years"].append(r["year"])
            if p.get("ret_year") is not None:
                s["rets"].append(p["ret_year"])
    stocks = []
    for s in stat.values():
        tot = 1.0
        for x in s["rets"]:
            tot *= 1 + x
        stocks.append({"ticker": s["ticker"], "name": s["name"], "sector": s["sector"],
                       "years": s["years"], "n": len(s["years"]),
                       "avg_ret": round(sum(s["rets"]) / len(s["rets"]), 4) if s["rets"] else None,
                       "total_ret": round(tot - 1, 4) if s["rets"] else None})
    stocks.sort(key=lambda x: (-x["n"], -(x["total_ret"] or -9)))

    out = {"years": years, "stocks": stocks,
           "generated": pd.Timestamp.today().strftime("%Y-%m-%d")}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    print(f"逐年记录 {len(years)} 年，涉及 {len(stocks)} 只股票")
    for r in years[-6:]:
        print(f"  {r['year']} {' '.join(p['ticker'] for p in r['holdings'])} "
              f"ret={r['ret']*100 if r['ret'] is not None else float('nan'):+.1f}% "
              f"dd={r['dd']*100 if r['dd'] is not None else float('nan'):+.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
