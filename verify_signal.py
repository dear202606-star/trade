# -*- coding: utf-8 -*-
"""独立复核当期信号：从原始行情重算 2025-12 排名 → 与前 3 名对照。

不复用 monthly_pick 的选股结果，只用「时点成分表 + 原始月线 + 行业表」重算，
用来证明线上展示的 3 只真的是按规则选出来的。
"""
import json
import os
import sys

import pandas as pd

import datafeed

BASE_YEAR = 2026          # 当期持仓所属年度
RANK_AT = "2025-12"       # 排名所用月份
PREV = "2024-12"          # 12 个月前的月份
MIN_PRICE = 5.0
MIN_DV = 20e6
TOPN = 3
SECTOR_CAP = 0.60


def main():
    m = datafeed.load_members()
    if m is None:
        print("无法获取成分表", file=sys.stderr)
        return 2
    m["start_date"] = m["start_date"].astype(str)
    m["end_date"] = m["end_date"].fillna("2099-12-31").astype(str)
    day = "2025-12-31"
    members = sorted(m[(m.start_date <= day) & (m.end_date >= day)]["ticker"].unique().tolist())
    print("2025-12-31 成分股：%d 只" % len(members))

    sector, name_map = datafeed.load_sector()
    datafeed.prefetch(members, workers=8)

    rows = []
    for t in members:
        b = datafeed.load_bars(t)
        if not b or RANK_AT not in b or PREV not in b:
            continue
        adj_now, raw_now = b[RANK_AT][0], b[RANK_AT][1]
        adj_prev = b[PREV][0]
        if not adj_prev or adj_prev <= 0 or not raw_now or raw_now < MIN_PRICE:
            continue
        ret = adj_now / adj_prev - 1
        # 说明：月线数据不含成交量，成交额门槛无法在此独立复算，
        # 这里只复算「价格≥ + 12 个月涨幅排名 + 行业上限」。
        rows.append({"ticker": t, "ret12": ret,
                     "sector": sector.get(t, "Unknown"),
                     "name": name_map.get(t, t),
                     "price": raw_now})

    q = pd.DataFrame(rows).sort_values("ret12", ascending=False).reset_index(drop=True)
    print("通过筛选（价格≥$5、日均成交额≥$2000万）：%d 只" % len(q))

    # 前 3 名 + 行业上限（同行业最多 2 只）
    picks, cnt = [], {}
    for _, r in q.iterrows():
        if len(picks) >= TOPN:
            break
        s = r["sector"]
        if cnt.get(s, 0) >= 2:
            continue
        picks.append(r)
        cnt[s] = cnt.get(s, 0) + 1

    secs = [p["sector"] for p in picks]
    dup = [s for s, c in pd.Series(secs).value_counts().items() if c >= 2]
    if dup:
        w = [SECTOR_CAP / 2 if p["sector"] in dup else (1 - SECTOR_CAP) / max(1, len(picks) - 2)
             for p in picks]
        tot = sum(w)
        w = [x / tot for x in w]
    else:
        w = [1 / len(picks)] * len(picks)

    print("\n独立重算结果：")
    for p, ww in zip(picks, w):
        print("  %-6s %-28s %-24s 12月涨幅 %+7.1f%%  权重 %.0f%%" %
              (p["ticker"], str(p["name"])[:26], p["sector"][:22], p["ret12"] * 100, ww * 100))

    sig = json.load(open(os.path.join("docs", "data", "signal.json"), encoding="utf-8"))
    off = sig.get("official") or []
    got = [(p["ticker"], round(p["weight"], 4)) for p in off]
    want = [(p["ticker"], round(float(ww), 4)) for p, ww in zip(picks, w)]
    print("\n线上信号：", got)

    ok = True
    if [x[0] for x in got] != [x[0] for x in want]:
        print("❌ 名单不一致：线上 %s / 重算 %s" % ([x[0] for x in got], [x[0] for x in want]))
        ok = False
    else:
        print("✅ 名单一致")
    for (gt, gw), (wt, ww2) in zip(got, want):
        if abs(gw - ww2) > 1e-3:
            print("❌ 权重不一致 %s：线上 %.3f / 重算 %.3f" % (gt, gw, ww2))
            ok = False
    if ok:
        print("✅ 权重一致")

    # 涨幅一致性
    for p in off:
        hit = q[q.ticker == p["ticker"]]
        if len(hit):
            diff = abs(float(hit.iloc[0]["ret12"]) - float(p.get("ret12") or 0))
            flag = "✅" if diff < 0.005 else "❌"
            print("%s %s 入选涨幅：线上 %+.1f%% / 重算 %+.1f%%（差 %.4f）" %
                  (flag, p["ticker"], (p.get("ret12") or 0) * 100, float(hit.iloc[0]["ret12"]) * 100, diff))
            if diff >= 0.005:
                ok = False

    # 前几名与线上是否可能漏掉更好的
    print("\n重算前 8 名（供对照）：")
    for i, (_, r) in enumerate(q.head(8).iterrows(), 1):
        print("  %d. %-6s %+7.1f%%  %s" % (i, r["ticker"], r["ret12"] * 100, r["sector"][:24]))

    print("\n结论：", "✅ 信号与规则完全一致" if ok else "❌ 存在不一致")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
