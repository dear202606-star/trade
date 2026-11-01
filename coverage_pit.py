# -*- coding: utf-8 -*-
"""coverage_pit.py — 真实覆盖率（只在「当年在指数里」的年份计缺口）

为什么不能用「2012-2025 每月都有」当指标
  · 2010 年退市的票，2012 年后根本不该有数据，缺它不是缺口；
  · 2018 年才进指数的票，2012 年没有数据也正常。
  正确的分母是「标的-年」：某票在当年 12 月末仍在指数内，才要求有当年 12 月价格。

输出
  · 总需求格数、已有全收益、已有但非全收益、完全无数据
  · 按年份拆解
  · 还缺哪些票（用于 AV 排期）
"""
import argparse
import io
import os
import sys

import psycopg2

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
REF = "fnvahsyrzmmxwovbsqpc"
HOST = "aws-0-ap-northeast-1.pooler.supabase.com"
YEARS = list(range(2010, 2026))


def db():
    pw = os.environ.get("SUPABASE_DB_PASSWORD", "")
    if not pw:
        p = os.path.join(BASE, "output", "_supabase_dbpass.txt")
        if os.path.exists(p):
            pw = io.open(p, encoding="utf-8").read().strip()
    c = psycopg2.connect(host=HOST, port=6543, user="postgres." + REF, password=pw,
                         dbname="postgres", connect_timeout=15, sslmode="require")
    c.autocommit = True
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-missing", action="store_true")
    a = ap.parse_args()

    c = db()
    with c.cursor() as cur:
        # 每 (symbol, year)：当年 12 月末在指数内 → 必须有点位
        cur.execute("""
            with yrs as (select unnest(%s::int[]) as y),
            need as (
              select m.symbol, y.y as yr,
                     to_char(make_date(y.y, 12, 31), 'YYYY-MM') as m12
                from yrs y
                join members m
                  on m.start_date <= make_date(y.y, 12, 31)
                 and m.end_date   >= make_date(y.y, 12, 31)
            )
            select n.yr,
                   count(*) as need,
                   count(*) filter (where s.basis = 'total_return') as tr,
                   count(*) filter (where s.symbol is null) as nodata,
                   count(*) filter (where s.symbol is not null and s.basis <> 'total_return') as po
              from need n
              left join series_selected s
                on s.symbol = n.symbol and s.month = n.m12
             group by n.yr order by n.yr
        """, (YEARS,))
        rows = cur.fetchall()

        print("%-6s %6s %6s %7s %7s  %s" % ("年份", "需求", "全收益", "仅价格", "无数据", "覆盖率"))
        tot = tr = po = nod = 0
        for yr, need, t, nd, p in rows:
            tot += need; tr += t; po += p; nod += nd
            print("%-6d %6d %6d %7d %7d  %5.1f%%" % (yr, need, t, p, nd, 100.0 * t / max(1, need)))
        print("-" * 60)
        print("%-6s %6d %6d %7d %7d  %5.1f%%" % ("合计", tot, tr, po, nod, 100.0 * tr / max(1, tot)))
        print("\n结论：真实覆盖率（标的-年口径）%.1f%%；距 100%% 还差 %d 格" % (
            100.0 * tr / max(1, tot), tot - tr))

        # 还缺哪些票（按缺口大小排序，用于 AV 排期）
        cur.execute("""
            with yrs as (select unnest(%s::int[]) as y),
            need as (
              select m.symbol, y.y as yr, to_char(make_date(y.y,12,31),'YYYY-MM') as m12
                from yrs y join members m
                  on m.start_date <= make_date(y.y,12,31) and m.end_date >= make_date(y.y,12,31)
            ),
            miss as (
              select n.symbol, count(*) as gap_years
                from need n
                left join series_selected s on s.symbol=n.symbol and s.month=n.m12
               where s.symbol is null or s.basis <> 'total_return'
               group by n.symbol
            )
            select symbol, gap_years from miss order by gap_years desc, symbol
        """, (YEARS,))
        miss = cur.fetchall()
        print("涉及标的 %d 只，缺口总数 %d" % (len(miss), sum(g for _, g in miss)))
        if a.list_missing:
            print("缺口最多的 30 只：")
            for s, g in miss[:30]:
                print("  %-8s 缺 %2d 年" % (s, g))
    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
