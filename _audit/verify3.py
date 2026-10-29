# -*- coding: utf-8 -*-
"""Phase 3: 用站点自身 SPY 月线重建 10 月均线开关路径"""
import json, os
os.chdir(r"C:\Users\Administrator\Desktop\量化")
cv = json.load(open("docs/data/curve.json", encoding="utf-8"))
eq = json.load(open("docs/data/equity.json", encoding="utf-8"))

# 2010-01..2025-12 月线（站点 curve.json）
spy = {p["d"]: p["b"] for p in cv["points"]}
base = spy["2025-12"]                      # 2025-12 的 b
# 2026 月末（equity.json 日频取每月最后一点），按同一比例换算
last = {}
for p in eq["points"]:
    last[p["d"][:6]] = p
for m, p in last.items():
    key = m[:4] + "-" + m[4:]
    spy[key] = p["b"] * base
ms = sorted(spy)
print("series %s .. %s  n=%d  base(2025-12 b)=%.4f" % (ms[0], ms[-1], len(ms), base))

rows = []
for i, m in enumerate(ms):
    if i < 9:
        continue
    win = ms[i - 9:i + 1]
    ma = sum(spy[x] for x in win) / 10
    on = spy[m] >= ma
    rows.append((m, spy[m], ma, on))

print("\n月末判定（close vs 含当月的 10 月均线）：")
for m, c, ma, on in rows:
    if m >= "2024-10":
        print("  %s close=%7.2f ma10=%7.2f  %s" % (m, c, ma, "满仓" if on else "半仓 <<<"))

below = [r[0] for r in rows if not r[3]]
print("\n全期触发月数: %d / %d" % (len(below), len(rows)))
print("触发月份:", ", ".join(below))
print("\n2026 年触发:", ", ".join([m for m in below if m >= "2026-01"]))

# 若按规则 2026-03 起半仓，账户 YTD 会变成多少（敞口回填口径）
s = eq["points"][-1]["s"]
print("\n敞口回填演示: 当前 s=%.6f (+%.2f%%)" % (s, (s - 1) * 100))
for e in (1.0, 0.5):
    v = s * e + (1 - e)
    print("  exposure=%.1f -> YTD %+.2f%%" % (e, (v - 1) * 100))

# 2026 各月策略净值变化（用于估算 3 月半仓的影响）
pts = eq["points"]
mm = {}
for p in pts:
    mm[p["d"][:6]] = p["s"]
k = sorted(mm)
print("\n2026 月末策略净值:")
for m in k:
    print("  %s  s=%.4f" % (m, mm[m]))
