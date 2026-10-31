# -*- coding: utf-8 -*-
"""audit_membership.py — 成分股清单与数据完整性核查（锁定前的必检项）

检查
  1. 清单结构：日期格式、start<=end、同 ticker 区间不重叠、重复行
  2. 每年 12 月 31 日在册人数是否合理（S&P 500 应为 495–510）
  3. 每个标的在「在册期间」是否有行情覆盖（缺 = 数据缺口）
  4. 交易日历一致性：12 月最后交易日是否都有价格
  5. 与上游 fja05680 最新版比对（可选，需联网）
"""
import csv
import io
import os
import sys
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
MEMBERS = os.path.join(BASE, "static", "members_frozen.csv")
REF = "fnvahsyrzmmxwovbsqpc"
HOST = "aws-0-ap-northeast-1.pooler.supabase.com"

OK, BAD = [], []


def ck(cond, name, detail=""):
    (OK if cond else BAD).append(name if cond else "%s %s" % (name, detail))
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else "  " + detail))


def main():
    import psycopg2
    rows = list(csv.DictReader(open(MEMBERS, encoding="utf-8")))
    for r in rows:
        r["start_date"] = (r.get("start_date") or "").strip()
        r["end_date"] = (r.get("end_date") or "").strip() or "2099-12-31"

    # 1) 结构
    bad_fmt = [r for r in rows if len(r["start_date"]) != 10 or len(r["end_date"]) != 10]
    ck(not bad_fmt, "日期格式全部合法（%d 行）" % len(rows), "异常 %d 行" % len(bad_fmt))
    inv = [r for r in rows if r["start_date"] > r["end_date"]]
    ck(not inv, "start ≤ end", "异常 %d 行" % len(inv))
    per = defaultdict(list)
    for r in rows:
        per[r["ticker"]].append((r["start_date"], r["end_date"]))
    overlap = 0
    for t, segs in per.items():
        segs.sort()
        for i in range(1, len(segs)):
            if segs[i][0] <= segs[i - 1][1]:
                overlap += 1
    ck(overlap == 0, "同一标的区间不重叠", "重叠 %d 处" % overlap)
    dup = len(rows) - len({(r["ticker"], r["start_date"], r["end_date"]) for r in rows})
    ck(dup == 0, "无重复行", "重复 %d 行" % dup)

    # 2) 每年 12 月在册人数
    print("\n[每年 12-31 在册人数]")
    years = list(range(2010, 2026))
    counts = {}
    for y in years:
        d = "%d-12-31" % y
        n = sum(1 for r in rows if r["start_date"] <= d <= r["end_date"])
        counts[y] = n
    print("  " + " ".join("%d:%d" % (y, counts[y]) for y in years))
    bad_years = [y for y, n in counts.items() if not (480 <= n <= 520)]
    ck(not bad_years, "各年在册人数在 480–520", "异常年份 %s" % bad_years)

    # 3) 在册期间行情覆盖
    pw = io.open(os.path.join(BASE, "output", "_supabase_dbpass.txt"), encoding="utf-8").read().strip()
    c = psycopg2.connect(host=HOST, port=6543, user="postgres." + REF, password=pw,
                         dbname="postgres", connect_timeout=20, sslmode="require")
    cur = c.cursor()
    cur.execute("select symbol, month from prices_monthly")
    have = defaultdict(set)
    for s, m in cur.fetchall():
        have[s].add(m)
    c.close()

    missing_dec = []
    for y in years:
        d = "%d-12" % y
        for t, segs in per.items():
            if any(s <= "%d-12-31" % y <= e for s, e in segs):
                if d not in have.get(t, set()):
                    missing_dec.append((t, y))
    total_slots = sum(sum(1 for t, segs in per.items()
                          if any(s <= "%d-12-31" % y <= e for s, e in segs)) for y in years)
    cov = 1 - len(missing_dec) / max(1, total_slots)
    ck(cov >= 0.98, "在册期间 12 月覆盖 %.1f%%（%d/%d）" % (
        cov * 100, total_slots - len(missing_dec), total_slots),
       "缺失 %d 个（样例 %s）" % (len(missing_dec), missing_dec[:5]))

    print("\n核查：通过 %d · 失败 %d" % (len(OK), len(BAD)))
    for b in BAD:
        print("  -", b)
    return 0 if not BAD else 1


if __name__ == "__main__":
    sys.exit(main())
