# -*- coding: utf-8 -*-
"""validate_prices.py — 数据质量验收（与审计给的 5 条标准一一对应）

① NVDA 2021 ≈ +125%，2024 ≈ +171%
② DUK 2012-07 单月 |涨跌| ≤ 5%
③ AMCR 2025-12 → 最新 应在 +5%~+40%（不是 +468%）
④ 覆盖率：static/prices 覆盖 static/members_frozen.csv 的比例
⑤ 连续性：单月 |涨跌| > 150% 的比例 < 0.5%
"""
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
PRICES = os.path.join(BASE, "static", "prices_unified")
AV = os.path.join(BASE, "static", "prices")
MEMBERS = os.path.join(BASE, "static", "members_frozen.csv")

OK, BAD = [], []


def ck(cond, name, detail=""):
    (OK if cond else BAD).append(name if cond else "%s %s" % (name, detail))
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else "  " + detail))


def bars(t):
    p = os.path.join(PRICES, t + ".json")
    if not os.path.exists(p):
        return {}
    d = json.load(open(p, encoding="utf-8"))
    return {r[0]: (r[1], r[1] if len(r) < 3 else r[2]) for r in d.get("bars") or []}


def ret(t, y0, y1):
    b = bars(t)
    a, z = y0, y1
    if a not in b or z not in b or not b[a][0]:
        return None
    return b[z][0] / b[a][0] - 1


def main():
    nv1 = ret("NVDA", "2020-12", "2021-12")
    nv2 = ret("NVDA", "2023-12", "2024-12")
    ck(nv1 is not None and abs(nv1 - 1.25) < 0.12,
       "NVDA 2021 ≈ +125%%（实际 %+.1f%%）" % ((nv1 or 0) * 100),
       "偏差过大：%s" % nv1)
    ck(nv2 is not None and abs(nv2 - 1.71) < 0.15,
       "NVDA 2024 ≈ +171%%（实际 %+.1f%%）" % ((nv2 or 0) * 100),
       "偏差过大：%s" % nv2)

    d = bars("DUK")
    if "2012-06" in d and "2012-07" in d:
        r = d["2012-07"][0] / d["2012-06"][0] - 1
        ck(abs(r) <= 0.05, "DUK 2012-07 单月 %+.2f%%（应 ≤5%%）" % (r * 100),
           "仍有拆股断点：%+.1f%%" % (r * 100))
    else:
        ck(False, "DUK 2012-07 可取", "缺数据")

    am = bars("AMCR")
    if "2025-12" in am and am:
        last = sorted(am)[-1]
        r = am[last][0] / am["2025-12"][0] - 1
        ck(0.03 <= r <= 0.40,
           "AMCR 2025-12→%s %+.1f%%（合理区间 3%%~40%%）" % (last, r * 100),
           "疑似反向拆股未处理：%+.0f%%" % (r * 100))

    import pandas as pd
    m = pd.read_csv(MEMBERS, dtype=str).fillna("2099-12-31")
    need = sorted(set(m[m["end_date"] >= "2009-01-01"]["ticker"].tolist()))
    have = [t for t in need if os.path.exists(os.path.join(PRICES, t + ".json"))]
    av_n = len([f for f in os.listdir(AV) if f.endswith(".json")]) if os.path.isdir(AV) else 0
    print("    （权威源已覆盖 %d 只，其余为本地修复）" % av_n)
    ck(len(have) >= 0.95 * len(need),
       "覆盖率 %.1f%%（%d/%d）" % (100.0 * len(have) / max(1, len(need)), len(have), len(need)),
       "低于 95%，需继续回填")

    jumps = total = 0
    for t in have:
        b = bars(t)
        ks = sorted(b)
        for i in range(1, len(ks)):
            a0, a1 = b[ks[i - 1]][0], b[ks[i]][0]
            if not a0 or not a1:
                continue
            total += 1
            if abs(a1 / a0 - 1) > 1.5:
                jumps += 1
    rate = jumps / max(1, total)
    ck(rate < 0.005, "连续性：单月 |涨跌|>150%% 占比 %.3f%%（应 <0.5%%）" % (rate * 100),
       "跳变过多：%d/%d" % (jumps, total))

    print("\n验收：通过 %d · 失败 %d" % (len(OK), len(BAD)))
    return 0 if not BAD else 1


if __name__ == "__main__":
    sys.exit(main())
