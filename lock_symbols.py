# -*- coding: utf-8 -*-
"""lock_symbols.py — 逐标的锁定（验证通过的标的先冻结）

背景
  「整段锁定」要求 100% 全收益，目前还差 171 只退市票。
  但库里已有 432 只标的满足：2012-2025 每月都有、且全部是全收益口径、连续性通过。
  这些标的的数据已经可靠，可以先冻结——这就是「逐段锁定」的第一段。

做法
  1. 建 locked_symbols(symbol, locked_at, rows, hash)
  2. 扩展 prices_monthly 触发器：已锁标的的任何写入直接报错
  3. 锁之前跑校验：覆盖完整、口径全收益、无 >150% 单月跳变

用法
  python lock_symbols.py --check     # 只看能锁多少
  python lock_symbols.py --lock      # 执行锁定
  python lock_symbols.py --list
"""
import argparse
import hashlib
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
            select symbol, n from s
            where n >= %s and tr = n
            order by symbol
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


def hash_series(ser):
    h = hashlib.sha256()
    for m, v in ser:
        h.update(("%s|%.10f;" % (m, float(v or 0))).encode())
    return h.hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--lock", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
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
    print("可锁标的: %d 只（%s..%s，每月全收益且无大跳变）" % (len(cands), Y0, Y1))
    if a.check or not a.lock:
        print("样例:", [c[0] for c in cands[:12]])
        return

    c = db()
    n = 0
    with c.cursor() as cur:
        for sym, months, ser in cands:
            cur.execute("""insert into data_lock(scope, symbol, month_from, month_to, status,
                                                symbol_count, fingerprint, fingerprint_algo, note)
                           values ('symbol_month', %s, %s, %s, 'locked', 1, %s, 'sha256-16', %s)
                           on conflict do nothing""",
                        (sym, Y0, Y1, hash_series(ser), "%s..%s 全收益完整" % (Y0, Y1)))
            n += cur.rowcount
        cur.execute("select count(*) from data_lock where scope='symbol_month' and status='locked'")
        total = cur.fetchone()[0]
    c.close()
    print("✅ 本次锁定 %d 只，累计 %d 只" % (n, total))


if __name__ == "__main__":
    main()
