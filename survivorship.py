# -*- coding: utf-8 -*-
"""survivorship.py — 量化生存者偏差：统计每年标普500 成分股中缺少行情数据的比例

用法：
  python survivorship.py            # 用本地 data/sina2 缓存统计
  python survivorship.py --probe    # 额外用 Yahoo 尝试补齐缺失标的（CI 可用）
"""
import argparse
import glob
import json
import os
import sys

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")


def local_tickers():
    return {os.path.basename(p)[:-5].replace("_", ".") for p in glob.glob(os.path.join(DATA, "sina2", "*.json"))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="用 Yahoo 尝试补齐（CI 环境）")
    args = ap.parse_args()

    m = pd.read_csv(os.path.join(DATA, "sp500_ticker_start_end.csv"))
    m["start_date"] = m["start_date"].astype(str)
    m["end_date"] = m["end_date"].fillna("2099-12-31").astype(str)

    have = local_tickers()
    print(f"本地行情缓存: {len(have)} 只")

    rows = []
    for y in range(2008, 2026):
        d = f"{y}-01-01"
        mem = set(m[(m.start_date <= d) & (m.end_date >= d)]["ticker"])
        miss = sorted(t for t in mem if t not in have)
        rows.append({"year": y, "members": len(mem), "covered": len(mem) - len(miss),
                     "missing": len(miss), "coverage": (len(mem) - len(miss)) / max(len(mem), 1),
                     "missing_list": miss})
    df = pd.DataFrame(rows)

    print("\n年份  成分股  有数据  缺失   覆盖率")
    for _, r in df.iterrows():
        print(f"{r['year']}  {r['members']:5d}  {r['covered']:5d}  {r['missing']:4d}   {r['coverage']*100:5.1f}%")

    allmiss = sorted({t for lst in df["missing_list"] for t in lst})
    print(f"\n累计缺失标的（去重）: {len(allmiss)} 只")
    print("样例:", ", ".join(allmiss[:25]))

    recovered = []
    if args.probe:
        try:
            import market_data
            print("\n尝试用 Yahoo 补齐 ...", flush=True)
            ok = 0
            for i, t in enumerate(allmiss, 1):
                rows_ = market_data.fetch_yahoo(t)
                if rows_ and len(rows_) >= 13:
                    recovered.append(t)
                    ok += 1
                if i % 50 == 0:
                    print(f"  {i}/{len(allmiss)} 已恢复 {ok}", flush=True)
            print(f"  可恢复 {len(recovered)} / {len(allmiss)}")
        except Exception as e:
            print("Yahoo 探测失败:", e, file=sys.stderr)

    out = {
        "generated": pd.Timestamp.today().strftime("%Y-%m-%d"),
        "by_year": [{k: (v if k != "missing_list" else v) for k, v in r.items()} for r in rows],
        "missing_unique": len(allmiss),
        "recovered": len(recovered),
        "recovered_list": recovered,
    }
    with open(os.path.join(BASE, "output", "survivorship.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    lines = ["# 生存者偏差审计", "",
             f"生成时间：{out['generated']}", "",
             "| 年份 | 成分股 | 有行情 | 缺失 | 覆盖率 |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['year']} | {r['members']} | {r['covered']} | {r['missing']} | {r['coverage']*100:.1f}% |")
    lines += ["", f"累计缺失（去重）：**{len(allmiss)} 只**"]
    if args.probe:
        lines.append(f"Yahoo 可恢复：**{len(recovered)} 只**")
    with open(os.path.join(BASE, "output", "survivorship.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n已写入 output/survivorship.json / .md")


if __name__ == "__main__":
    main()
