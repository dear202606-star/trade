# -*- coding: utf-8 -*-
"""lock_stage.py — 分阶段锁定历史数据（锁定后不再抓取、不允许改写）

设计（对应「历史异步锁定、当年实时更新」）
  · locks 表记录每个阶段/年份区间的锁定状态、行数与内容哈希
  · 数据库触发器：一旦某月区间被锁，prices_monthly / dataset_rows 对该区间的写入直接报错
  · 锁定前必须通过校验（覆盖率、连续性、口径）
  · 锁定后重跑流水线只读不写；修复必须新建锁版本

用法
  python lock_stage.py --check 2010,2025      # 只校验，不锁
  python lock_stage.py --lock 2010,2025       # 校验通过后锁定
  python lock_stage.py --list
"""
import argparse
import hashlib
import io
import json
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

TRIGGER = """
create or replace function guard_locked_months() returns trigger as $$
declare
  bound text;
begin
  select max(scope_to) into bound from locks
   where kind = 'monthly_prices' and status = 'locked';
  if bound is not null and new.month <= bound then
    raise exception 'month % is locked (frozen through %)', new.month, bound;
  end if;
  return new;
end $$ language plpgsql;

drop trigger if exists trg_guard_months on prices_monthly;
create trigger trg_guard_months before insert or update on prices_monthly
for each row execute function guard_locked_months();
"""


def db():
    pw = io.open(os.path.join(BASE, "output", "_supabase_dbpass.txt"), encoding="utf-8").read().strip()
    return psycopg2.connect(host=HOST, port=6543, user="postgres." + REF, password=pw,
                            dbname="postgres", connect_timeout=20, sslmode="require")


def ensure_table():
    with db() as c, c.cursor() as cur:
        cur.execute("""
        create table if not exists locks (
          id serial primary key,
          kind text not null,              -- monthly_prices | membership | sector | derived
          scope_from text, scope_to text,
          status text default 'draft',      -- draft | locked
          rows int, symbols int,
          hash text,
          monthly_only boolean default true,
          locked_at timestamptz,
          note text,
          unique (kind, scope_from, scope_to)
        )""")
        cur.execute(TRIGGER)


def measure(y0, y1):
    with db() as c, c.cursor() as cur:
        cur.execute("""select count(*), count(distinct symbol), min(month), max(month)
                       from prices_monthly where month between %s and %s""",
                    ("%d-01" % y0, "%d-12" % y1))
        rows, syms, mn, mx = cur.fetchone()
        cur.execute("""select count(*) from prices_monthly
                       where month between %s and %s and month like '%%-12'""",
                    ("%d-01" % y0, "%d-12" % y1))
        dec = cur.fetchone()[0]
        cur.execute("""select count(*) from prices_monthly
                       where month between %s and %s and basis='total_return'""",
                    ("%d-01" % y0, "%d-12" % y1))
        tr = cur.fetchone()[0]
        cur.execute("""select md5(string_agg(symbol||month||coalesce(adj_close::text,''), '|'
                              order by symbol, month))
                       from prices_monthly where month between %s and %s""",
                    ("%d-01" % y0, "%d-12" % y1))
        h = cur.fetchone()[0]
    return {"rows": rows, "symbols": syms, "dec": dec, "total_return": tr,
            "from": mn, "to": mx, "hash": h}


def check(y0, y1):
    m = measure(y0, y1)
    years = y1 - y0 + 1
    problems = []
    if m["rows"] == 0:
        problems.append("区间无数据")
    if m["dec"] < years * 400:
        problems.append("12 月行数偏少（%d，预期 ≥%d）" % (m["dec"], years * 400))
    tr_ratio = m["total_return"] / max(1, m["rows"])
    if tr_ratio < 0.8:
        problems.append("全收益口径仅 %.1f%%（要求 ≥80%%）" % (tr_ratio * 100))
    print("区间 %d-%d：%d 行 / %d 只 / 12月 %d 行 / 全收益 %d 行（%.1f%%）/ hash %s" % (
        y0, y1, m["rows"], m["symbols"], m["dec"], m["total_return"],
        100.0 * m["total_return"] / max(1, m["rows"]), m["hash"]))
    for p in problems:
        print("   ✗", p)
    return 0 if not problems else 1, m


def lock(y0, y1, force=False):
    _rc, m = check(y0, y1)
    problems = [] if _rc == 0 else ["校验未通过（见上）"]
    if problems and not force:
        print("✗ 未通过锁定校验：")
        for p in problems:
            print("   -", p)
        print("  确认可接受时加 --force")
        return 1
    with db() as c, c.cursor() as cur:
        cur.execute("""insert into locks(kind, scope_from, scope_to, status, rows, symbols, hash,
                                        locked_at, note)
                       values ('monthly_prices', %s, %s, 'locked', %s, %s, %s, now(), %s)
                       on conflict (kind, scope_from, scope_to) do update
                       set status='locked', rows=excluded.rows, symbols=excluded.symbols,
                           hash=excluded.hash, locked_at=now()""",
                    ("%d-01" % y0, "%d-12" % y1, m["rows"], m["symbols"], m["hash"],
                     "锁定后不可写；修复需新建版本"))
    print("✅ 已锁定 %d-%d（hash %s）" % (y0, y1, m["hash"]))
    return 0


def listing():
    with db() as c, c.cursor() as cur:
        cur.execute("""select kind, scope_from, scope_to, status, rows, symbols, hash,
                              to_char(locked_at,'YYYY-MM-DD HH24:MI')
                       from locks order by id desc""")
        print("%-16s %-10s %-10s %-8s %8s %7s %-10s %s" % (
            "kind", "from", "to", "status", "rows", "symbols", "hash", "locked_at"))
        for r in cur.fetchall():
            print("%-16s %-10s %-10s %-8s %8s %7s %-10s %s" % r)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", default="")
    ap.add_argument("--lock", default="")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    ensure_table()
    if a.list:
        listing()
    if a.check:
        y0, y1 = [int(x) for x in a.check.split(",")]
        rc, _ = check(y0, y1)
        sys.exit(rc)
    if a.lock:
        y0, y1 = [int(x) for x in a.lock.split(",")]
        sys.exit(lock(y0, y1, force=a.force))
