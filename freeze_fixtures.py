# -*- coding: utf-8 -*-
"""冻结口径 B 的输入快照：成分表 + 行业映射。

为什么要冻结：
  datafeed.load_members() 超过 7 天会从网络刷新，CI 与本地拿到的快照不同，
  于是「全年成分」的池子不同 → 同一策略算出不同的历史（本地 +7088%，CI +5955%）。
  冻结后结果可复现，升级成分数据时显式运行本脚本。

产物：
  static/members_frozen.csv   标普 500 时点成分（ticker/start_date/end_date）
  static/sector_frozen.json   行业映射（含 sector_overrides 补充）
"""
import json
import os
import sys

import pandas as pd

import datafeed
import sys as _sys
try:                                    # Windows cp936 下避免 emoji 打印崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")
os.makedirs(STATIC, exist_ok=True)


def main():
    m = datafeed.load_members()
    m = m[["ticker", "start_date", "end_date"]].copy()
    m["start_date"] = m["start_date"].astype(str)
    m["end_date"] = m["end_date"].fillna("2099-12-31").astype(str)
    m = m.sort_values(["ticker", "start_date"]).reset_index(drop=True)
    p1 = os.path.join(STATIC, "members_frozen.csv")
    m.to_csv(p1, index=False)
    print("成分快照：%d 行 → %s" % (len(m), p1))

    sector, _ = datafeed.load_sector()
    sec = {k: v for k, v in sector.items() if v and v != "Unknown"}
    p2 = os.path.join(STATIC, "sector_frozen.json")
    with open(p2, "w", encoding="utf-8") as f:
        json.dump(sec, f, ensure_ascii=False, indent=0, sort_keys=True)
    print("行业快照：%d 只 → %s" % (len(sec), p2))


if __name__ == "__main__":
    main()
