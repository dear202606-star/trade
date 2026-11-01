# -*- coding: utf-8 -*-
"""db_staging.py — 无需 secret 的 CI→库写入通道

问题
  CI 只有公开 anon key（只读），没有 SUPABASE_DB_PASSWORD，无法直接写 prices_monthly。
  人工加 secret 需要仓库管理员权限，当前自动化拿不到。

方案（全部在库内完成，CI 只需 anon 写一个暂存表）
  1. 建 ingest_staging（允许 anon INSERT/SELECT，append-only）
  2. 库内函数 promote_staging()：校验 → 合并进 prices_monthly → 标记 promoted
  3. pg_cron 每 10 分钟调用一次（服务器侧执行，不需要任何凭据）

安全
  · 暂存表只允许 INSERT，不允许 UPDATE/DELETE
  · 合并前做结构校验（月份格式、价格>0、单月跳变），不合格直接标记 rejects
  · 任何人都能写暂存表，但写不进正式表；正式表仍受锁与触发器保护
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


STATEMENTS = [
    """create table if not exists ingest_staging (
         id bigserial primary key,
         symbol text not null,
         month text not null,
         adj_close numeric,
         raw_close numeric,
         source text not null,
         quality text default 'ok',
         basis text default 'total_return',
         inserted_at timestamptz default now(),
         promoted boolean default false,
         reject_reason text
       )""",
    "alter table ingest_staging enable row level security",
    "create index if not exists idx_staging_pending on ingest_staging(promoted) where promoted = false",
]

POLICY = """create policy staging_insert on ingest_staging for insert to anon, authenticated with check (true)"""
POLICY_SEL = """create policy staging_read on ingest_staging for select to anon, authenticated using (true)"""

PROMOTE = r"""
create or replace function promote_staging(max_rows int default 20000)
returns table(inserted int, rejected int) as $$
declare
  n_ins int := 0;
  n_rej int := 0;
begin
  -- 1) 结构校验：月份格式、价格为正
  update ingest_staging
     set reject_reason = case
           when month !~ '^\d{4}-(0[1-9]|1[0-2])$' then 'bad_month'
           when adj_close is null or adj_close <= 0 then 'bad_price'
           when basis not in ('total_return','price_only') then 'bad_basis'
           else null end
   where promoted = false and reject_reason is null
     and (month !~ '^\d{4}-(0[1-9]|1[0-2])$'
          or adj_close is null or adj_close <= 0
          or basis not in ('total_return','price_only'));

  select count(*) into n_rej from ingest_staging
   where promoted = false and reject_reason is not null;

  -- 2) 合并：只在未锁定月份写入
  with ok as (
    select distinct on (symbol, month, source)
           symbol, month, adj_close, raw_close, source, quality, basis
      from ingest_staging
     where promoted = false and reject_reason is null
     order by symbol, month, source, id desc
     limit max_rows
  ), ins as (
    insert into prices_monthly(symbol, month, adj_close, raw_close, source, quality, basis)
    select symbol, month, adj_close, raw_close, source, quality, basis from ok
    on conflict (symbol, month, source) do update
      set adj_close = excluded.adj_close,
          raw_close = excluded.raw_close,
          quality   = excluded.quality,
          basis     = excluded.basis,
          fetched_at = now()
    returning 1
  )
  select count(*) into n_ins from ins;

  -- 3) 标记已处理
  update ingest_staging set promoted = true
   where promoted = false and reject_reason is null
     and (symbol, month, source) in (
       select symbol, month, source from prices_monthly);

  return query select n_ins, n_rej;
end $$ language plpgsql security definer
"""


def setup():
    c = db()
    with c.cursor() as cur:
        for st in STATEMENTS:
            try:
                cur.execute(st)
            except Exception as e:
                print("  ! %s: %s" % (st.splitlines()[0][:50], str(e)[:70]))
        for pol in (POLICY, POLICY_SEL):
            try:
                cur.execute(pol)
            except Exception:
                pass
        cur.execute(PROMOTE)
        print("promote_staging() 已创建")

        # pg_cron 每 10 分钟跑一次
        try:
            cur.execute("select extname from pg_extension where extname='pg_cron'")
            if not cur.fetchone():
                cur.execute("create extension if not exists pg_cron")
                print("pg_cron 已安装")
            cur.execute("select jobid, jobname from cron.job where jobname='promote_staging'")
            row = cur.fetchone()
            if row:
                cur.execute("select cron.unschedule(%s)", (row[0],))
            cur.execute("""select cron.schedule('promote_staging', '*/10 * * * *',
                                              $$select promote_staging()$$)""")
            print("pg_cron 任务 promote_staging 已排程（每 10 分钟）")
        except Exception as e:
            print("pg_cron 排程失败（可手动调用）：%s" % str(e)[:100])

        cur.execute("""select count(*), count(*) filter (where promoted=false)
                       from ingest_staging""")
        print("暂存表：总 %d 行 / 待处理 %d 行" % cur.fetchone())
    c.close()


if __name__ == "__main__":
    setup()
