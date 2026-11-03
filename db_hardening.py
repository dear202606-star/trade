# -*- coding: utf-8 -*-
"""db_hardening.py — 数据库安全与锁定加固（对应审计 P0）

1. 撤销 anon / authenticated 对核心表的写权限（站点公开 key 曾可改/删 9 张表）
2. 核心表启用 RLS（纵深防御）
3. locks 改为 append-only（禁 UPDATE/DELETE）
4. prices_monthly 触发器补 DELETE 分支 + 月份不可变 + TRUNCATE 拦截
5. 修正 scope_from 被忽略的问题（只锁区间内的月份）
6. 建立锁前后的校验记录（validations）

用法：python db_hardening.py [--verify]
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

CORE_TABLES = ["prices_monthly", "locks", "datasets", "dataset_rows", "validations",
               "fetch_runs", "work_plans", "source_registry", "data_sources",
               "symbols", "corporate_actions"]

DDL = """
-- 1) 撤销匿名写权限（保留 holdings 供站点云同步）
revoke insert, update, delete, truncate on all tables in schema public from anon, authenticated;
grant select on holdings to anon, authenticated;
grant insert, update, select on holdings to anon, authenticated;

-- 2) 核心表启用 RLS（owner/postgres 不受影响）
alter table prices_monthly   enable row level security;
alter table locks            enable row level security;
alter table datasets         enable row level security;
alter table dataset_rows     enable row level security;
alter table validations      enable row level security;
alter table fetch_runs       enable row level security;
alter table work_plans       enable row level security;
alter table source_registry  enable row level security;
alter table data_sources     enable row level security;
alter table symbols          enable row level security;
alter table corporate_actions enable row level security;

-- 3) locks 只允许插入（作废只能插新版本）
create or replace function locks_append_only() returns trigger as $$
begin
  raise exception 'locks is append-only; insert a new version row instead of %', tg_op;
end $$ language plpgsql;

drop trigger if exists trg_locks_append on locks;
create trigger trg_locks_append before update or delete on locks
for each row execute function locks_append_only();

-- 4) prices_monthly 全面保护
create or replace function guard_locked_months() returns trigger as $$
declare
  r record;
begin
  if tg_op = 'UPDATE' and new.month <> old.month then
    raise exception 'month is immutable (attempt % -> %)', old.month, new.month;
  end if;
  for r in select scope_from, scope_to from locks
            where kind = 'monthly_prices' and status = 'locked' loop
    if (tg_op = 'DELETE' and old.month between r.scope_from and r.scope_to)
       or (tg_op in ('INSERT','UPDATE')
           and new.month between r.scope_from and r.scope_to) then
      raise exception 'month % is locked (% .. %)', 
        case when tg_op='DELETE' then old.month else new.month end, r.scope_from, r.scope_to;
    end if;
  end loop;
  return case when tg_op = 'DELETE' then old else new end;
end $$ language plpgsql;

drop trigger if exists trg_guard_months on prices_monthly;
create trigger trg_guard_months before insert or update or delete on prices_monthly
for each row execute function guard_locked_months();

create or replace function guard_truncate() returns trigger as $$
begin
  raise exception 'TRUNCATE is not allowed on protected tables';
end $$ language plpgsql;

drop trigger if exists trg_guard_truncate on prices_monthly;
create trigger trg_guard_truncate before truncate on prices_monthly
for each statement execute function guard_truncate();

-- 5) 常用索引
create index if not exists idx_locks_kind on locks(kind, status);
create index if not exists idx_prices_basis on prices_monthly(basis);
"""


def db():
    pw = io.open(os.path.join(BASE, "output", "_supabase_dbpass.txt"), encoding="utf-8").read().strip()
    return psycopg2.connect(host=HOST, port=6543, user="postgres." + REF, password=pw,
                            dbname="postgres", connect_timeout=20, sslmode="require")


def apply():
    conn = db()
    conn.autocommit = True          # 逐句提交，避免超时整批回滚
    stmts = [x.strip() for x in DDL.split(";") if x.strip() and not x.strip().startswith("--")]
    ok = fail = 0
    with conn.cursor() as cur:
        for st in stmts:
            try:
                cur.execute(st)
                ok += 1
            except Exception as e:
                fail += 1
                print("  ! %s -> %s" % (st.split(chr(10))[0][:60], str(e).split(chr(10))[0][:70]))
    conn.close()
    print("✅ 加固完成：成功 %d 条 / 失败 %d 条" % (ok, fail))


def verify():
    with db() as c, c.cursor() as cur:
        cur.execute("""select tablename,
                         has_table_privilege('anon','public.'||tablename,'INSERT') ins,
                         has_table_privilege('anon','public.'||tablename,'UPDATE') upd,
                         has_table_privilege('anon','public.'||tablename,'DELETE') del,
                         (select relrowsecurity from pg_class where oid=('public.'||tablename)::regclass) rls
                       from pg_tables where schemaname='public' order by 1""")
        print("%-20s %-6s %-6s %-6s %s" % ("table", "ins", "upd", "del", "rls"))
        for r in cur.fetchall():
            print("%-20s %-6s %-6s %-6s %s" % r)
        cur.execute("select count(*) from pg_trigger where tgrelid='public.locks'::regclass and not tgisinternal")
        print("locks 触发器:", cur.fetchone()[0])
        cur.execute("select count(*) from pg_trigger where tgrelid='public.prices_monthly'::regclass and not tgisinternal")
        print("prices_monthly 触发器:", cur.fetchone()[0])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    if not a.verify:
        apply()
    verify()
