# -*- coding: utf-8 -*-
"""lock_stage.py — 分阶段锁定历史数据（锁定后不再抓取、不允许改写）

设计（对应「历史异步锁定、当年实时更新」）
  · locks 表记录每个阶段/年份区间的锁定状态、行数与内容哈希
  · 数据库触发器：一旦某月区间被锁，prices_monthly / dataset_rows 对该区间的写入直接报错
  · 锁定前必须通过校验（覆盖率、连续性、口径）
  · 锁定后重跑流水线只读不写；修复必须新建锁版本

用法
  python lock_stage.py --check 2010,2025      # 只校验，不锁
  python lock_stage.py --lock 2010,2025       # 只读核对既有锁；不创建新验收
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

TRIGGER = ""  # 守卫函数只由 sql/010_guard_locked.sql 管理（审计 P0-02）


def db():
    pw = io.open(os.path.join(BASE, "output", "_supabase_dbpass.txt"), encoding="utf-8").read().strip()
    return psycopg2.connect(host=HOST, port=6543, user="postgres." + REF, password=pw,
                            dbname="postgres", connect_timeout=20, sslmode="require")


def ensure_table():
    with db() as c, c.cursor() as cur:
        cur.execute("select to_regclass('public.locks') is not null")
        if not cur.fetchone()[0]:
            raise RuntimeError('locks 不存在；只读诊断入口不创建数据库结构')


def measure(y0, y1):
    """度量对象是 series_selected（每票单源后的唯一序列），不是多源并存的原始表"""
    with db() as c, c.cursor() as cur:
        cur.execute("""select count(*), count(distinct symbol), min(month), max(month),
                              count(*) filter (where basis='total_return')
                       from series_selected where month between %s and %s""",
                    ("%d-01" % y0, "%d-12" % y1))
        rows, syms, mn, mx, tr = cur.fetchone()
        cur.execute("""select count(distinct symbol) from series_selected
                       where month = %s""", ("%d-12" % y1,))
        dec = cur.fetchone()[0]
        cur.execute("""select md5(string_agg(symbol||month||coalesce(adj_close::text,''), '|'
                              order by symbol, month))
                       from series_selected where month between %s and %s""",
                    ("%d-01" % y0, "%d-12" % y1))
        h = cur.fetchone()[0]
        cur.execute("""select to_char(d,'YYYY-MM') from generate_series(
                         %s::date,%s::date,interval '1 month') d
                       where not exists(select 1 from series_selected s
                                        where s.month=to_char(d,'YYYY-MM'))
                       order by d""", ("%d-01-01" % y0, "%d-12-01" % y1))
        missing_months = [row[0] for row in cur.fetchall()]
    return {"rows": rows, "symbols": syms, "dec": dec, "total_return": tr,
            "from": mn, "to": mx, "hash": h, "missing_months": missing_months}


def _measure_raw(y0, y1):
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
    problems = []
    if m["rows"] == 0:
        problems.append("区间无数据")
    if m["dec"] < 480:
        problems.append("末年 12 月标的仅 %d（预期 ≥480）" % m["dec"])
    if m["total_return"] != m["rows"]:
        problems.append("仍有 %d 行不是全收益口径（要求逐行 100%%）" % (m["rows"]-m["total_return"]))
    if m["missing_months"]:
        problems.append("区间缺少整月数据：%s" % ", ".join(m["missing_months"]))
    print("区间 %d-%d：%d 行 / %d 只 / 12月 %d 行 / 全收益 %d 行（%.1f%%）/ hash %s" % (
        y0, y1, m["rows"], m["symbols"], m["dec"], m["total_return"],
        100.0 * m["total_return"] / max(1, m["rows"]), m["hash"]))
    for p in problems:
        print("   ✗", p)
    return 0 if not problems else 1, m


def lock(y0, y1, force=False):
    scope=("%d-01" % y0,"%d-12" % y1)
    with db() as c, c.cursor() as cur:
        cur.execute("""select hash from locks where kind='monthly_prices'
                       and scope_from=%s and scope_to=%s and status='locked'""",scope)
        existing=cur.fetchone()
    if existing is not None:
        print("✅ 历史区间已锁定，直接保留，不重新加工（hash %s）" % existing[0])
        return 0
    print("✗ 覆盖和口径初筛不能创建完整验收锁；需要绑定身份、价格、现金流及收益的独立验收证据。")
    return 1


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
