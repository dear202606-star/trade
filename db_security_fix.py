# -*- coding: utf-8 -*-
"""db_security_fix.py — 真正收紧权限（上一次 revoke 语句超时没落地）

实测现状（2026-11-01）：
  17 张表对 anon 全部 ins/upd/del/truncate = True  ← 拿着站点公开 key 就能改行情
  prices_monthly 的 RLS 甚至是 false
  pg_default_acl 让 public 下新建表自动获得 anon 全部权限

目标状态
  · 默认权限：public 新表不再自动授权给 anon/authenticated
  · 业务表：anon 只读（select）
  · holdings：站点云同步需要 insert/update/select
  · ingest_staging：CI 需要 insert（append-only），不给 update/delete
"""
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

READ_ONLY = ["prices_monthly", "series_selected", "datasets", "dataset_rows",
             "validations", "fetch_runs", "members", "sectors", "locked_symbols",
             "locks", "source_registry", "symbols", "corporate_actions", "work_plans"]

STEPS = [
    # 1) 先断默认权限，防止后面新建表又自动开放
    "alter default privileges in schema public revoke all on tables from anon, authenticated",
    "alter default privileges in schema public revoke all on sequences from anon, authenticated",
    "alter default privileges in schema public revoke all on functions from anon, authenticated",
    # 2) 收掉所有表的写权限（含 truncate）
    "revoke insert, update, delete, truncate, references, trigger on all tables in schema public from anon, authenticated",
    "revoke all on all sequences in schema public from anon, authenticated",
    # 3) 只读授权（供 CI 的 REST 读取与站点展示）
    *["grant select on %s to anon, authenticated" % t for t in READ_ONLY],
    # 4) 站点云同步表：需要写
    "grant select, insert, update on holdings to anon, authenticated",
    # 5) CI 暂存表：只允许追加
    "grant select, insert on ingest_staging to anon, authenticated",
    # 6) 补上漏掉的 RLS
    "alter table prices_monthly enable row level security",
]


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


def apply():
    c = db()
    ok = fail = 0
    with c.cursor() as cur:
        cur.execute("set statement_timeout = '120s'")
        for st in STEPS:
            try:
                cur.execute(st)
                ok += 1
            except Exception as e:
                fail += 1
                print("  ! %s -> %s" % (st[:60], str(e).split("\n")[0][:70]))
    c.close()
    print("执行完成：成功 %d / 失败 %d" % (ok, fail))


def verify():
    c = db()
    with c.cursor() as cur:
        cur.execute("""
          select tablename,
            has_table_privilege('anon','public.'||tablename,'INSERT') ins,
            has_table_privilege('anon','public.'||tablename,'UPDATE') upd,
            has_table_privilege('anon','public.'||tablename,'DELETE') del,
            has_table_privilege('anon','public.'||tablename,'TRUNCATE') trc,
            has_table_privilege('anon','public.'||tablename,'SELECT') sel,
            (select relrowsecurity from pg_class where oid=('public.'||tablename)::regclass) rls
          from pg_tables where schemaname='public' order by 1
        """)
        print("%-20s %-6s %-6s %-6s %-6s %-6s %s" % ("table", "ins", "upd", "del", "trc", "sel", "rls"))
        bad = []
        for r in cur.fetchall():
            print("%-20s %-6s %-6s %-6s %-6s %-6s %s" % r)
            if r[1] or r[2] or r[3] or r[4]:
                bad.append(r[0])
        print("\n仍可写的表:", bad if bad else "无 ✅")
    c.close()


if __name__ == "__main__":
    apply()
    verify()
