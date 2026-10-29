# -*- coding: utf-8 -*-
"""补齐权威公司行为表（splits.json），并让实时排名也走同一套除权逻辑。

WDC 于 2025-02 分拆 SanDisk（SNDK）：持有 1 股 WDC 同时获得 SNDK 股份，
因此除权后价格需要用比例还原，否则 12 个月涨幅会被低估约 94 个百分点。
比例由除权前后价格连续性反推：2025-01 收 65.13 / 2025-02 收 48.93 ≈ 1.3311。
"""
import json
import os
import datetime as dt

OUT = "output/splits.json"

# 已知的公司行为：(ticker, 生效日期, 比例)
ACTIONS = [
    ("WDC", "2025-02-01", 1.3311),
]

sp = {}
if os.path.exists(OUT):
    try:
        sp = json.load(open(OUT, encoding="utf-8"))
    except Exception:
        sp = {}

for t, d, r in ACTIONS:
    ts = int(dt.datetime.strptime(d, "%Y-%m-%d").timestamp())
    lst = sp.setdefault(t, [])
    if not any(abs(x[0] - ts) < 86400 for x in lst):
        lst.append([ts, r])
        lst.sort()

os.makedirs("output", exist_ok=True)
json.dump(sp, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
print("splits.json 已更新：", {k: v for k, v in sp.items()})
