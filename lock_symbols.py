# -*- coding: utf-8 -*-
"""lock_symbols.py — 旧历史覆盖初筛，只读诊断。

覆盖月数、口径标签与价格跳变不能证明证券身份、现金流及收益正确，
不得据此创建完整验收锁。新验收使用绑定独立证据的专用验收入口。

用法
  python lock_symbols.py --check     # 初筛，不代表完整验收
  python lock_symbols.py --list
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
Y0, Y1 = "2012-01", "2025-12"
MIN_MONTHS = 160

def db():
    pw = os.environ.get("SUPABASE_DB_PASSWORD", "")
    if not pw:
        p = os.path.join(BASE, "output", "_supabase_dbpass.txt")
        if os.path.exists(p):
            pw = io.open(p, encoding="utf-8").read().strip()
    c = psycopg2.connect(host=HOST, port=6543, user="postgres." + REF, password=pw,
                         dbname="postgres", connect_timeout=20, sslmode="require")
    c.autocommit = True
    return c


def ensure():
    """DDL 已收归 sql/050_data_lock.sql；这里只确认结构就绪"""
    c = db()
    with c.cursor() as cur:
        cur.execute("select to_regclass('public.data_lock') is not null")
        if not cur.fetchone()[0]:
            raise RuntimeError("data_lock 不存在：请先执行 sql/050_data_lock.sql")
    c.close()


def candidates():
    """满足条件的标的：覆盖完整 + 全收益 + 无大跳变"""
    c = db()
    with c.cursor() as cur:
        cur.execute("""
            with s as (
              select symbol,
                     count(*) n,
                     count(*) filter (where basis='total_return') tr,
                     max(month) mx, min(month) mn
              from series_selected
              where month between %s and %s
              group by symbol
            )
            select s.symbol, s.n from s
            where s.n >= %s and s.tr = s.n
              and not exists (select 1 from data_lock l
                               where l.scope='symbol_month' and l.symbol = s.symbol
                                 and l.status='locked'
                                 and not exists (select 1 from data_lock x
                                                  where x.supersedes_id=l.id and x.status='void'))
            order by s.symbol
        """, (Y0, Y1, MIN_MONTHS))
        rows = cur.fetchall()
        out = []
        for sym, n in rows:
            cur.execute("""select month, adj_close from series_selected
                           where symbol=%s and month between %s and %s
                           order by month""", (sym, Y0, Y1))
            ser = cur.fetchall()
            bad = 0
            for a, b in zip(ser, ser[1:]):
                p0, p1 = float(a[1] or 0), float(b[1] or 0)
                if p0 > 0 and p1 > 0 and (p1 / p0 > 4 or p1 / p0 < 0.25):
                    bad += 1
            if bad == 0:
                out.append((sym, n, ser))
    c.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--lock", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.lock:
        ap.error('覆盖初筛不能创建验收锁；请使用绑定独立验收证据的专用入口')
    ensure()

    if a.list:
        c = db()
        with c.cursor() as cur:
            cur.execute("""select count(*), min(locked_at), count(*)*168 from data_lock where scope='symbol_month' and status='locked'""")
            print("已锁标的: %s（%s 起，共 %s 个月）" % cur.fetchone())
            cur.execute("select symbol from locked_symbols order by symbol limit 10")
            print("样例:", [r[0] for r in cur.fetchall()])
        c.close()
        return

    cands = candidates()
    print("覆盖初筛候选: %d 只（%s..%s，非完整验收，不创建锁）" % (len(cands), Y0, Y1))
    print("样例:", [c[0] for c in cands[:12]])


if __name__ == "__main__":
    main()
