# -*- coding: utf-8 -*-
import io, os, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import psycopg2
BASE = r"C:\Users\Administrator\Desktop\量化"
PW = io.open(os.path.join(BASE, "output", "_supabase_dbpass.txt"), encoding="utf-8").read().strip()

def conn(auto=True):
    for i in range(5):
        try:
            c = psycopg2.connect(host="aws-0-ap-northeast-1.pooler.supabase.com", port=6543,
                                 user="postgres.fnvahsyrzmmxwovbsqpc", password=PW,
                                 dbname="postgres", connect_timeout=10, sslmode="require")
            c.autocommit = auto
            return c
        except Exception:
            time.sleep(2 + i * 2)
    raise RuntimeError("no conn")

c = conn(); cur = c.cursor()
cur.execute("set statement_timeout='120s'")

print("### RLS 与策略总表")
cur.execute("""select c.relname, c.relrowsecurity,
  (select count(*) from pg_policies p where p.tablename=c.relname) npol,
  has_table_privilege('anon','public.'||quote_ident(c.relname),'INSERT'),
  has_table_privilege('anon','public.'||quote_ident(c.relname),'UPDATE')
  from pg_class c join pg_namespace n on n.oid=c.relnamespace
  where n.nspname='public' and c.relkind='r' order by 1""")
for x in cur.fetchall():
    print("  %-18s rls=%-6s policies=%-2s anon_ins=%-6s anon_upd=%s" % x)

print("\n### 默认权限 pg_default_acl")
cur.execute("""select pg_get_userbyid(defaclrole), coalesce(defaclnamespace::regnamespace::text,'-'),
                      defaclobjtype, defaclacl::text from pg_default_acl order by 1,2""")
for x in cur.fetchall():
    print("  ", x)

print("\n### (symbol,month) 多源重复分布")
cur.execute("""select nsrc, count(*) from (select symbol, month, count(*) nsrc
               from prices_monthly group by 1,2) z group by 1 order by 1""")
for x in cur.fetchall():
    print("  每月 %s 个源的行组数 = %s" % x)

print("\n### 触发器开销：200 只票灌数，开/关触发器对比")
for label, disable in (("触发器开启", False), ("触发器关闭", True)):
    c2 = conn(auto=False); q = c2.cursor()
    q.execute("set local lock_timeout='8s'")
    try:
        if disable:
            q.execute("alter table prices_monthly disable trigger trg_guard_months")
        q.execute("create temp table _perf (like prices_monthly) on commit drop")
        t0 = time.time()
        q.execute("""insert into _perf select symbol,month,adj_close,raw_close,source,quality,fetched_at,basis
                     from prices_monthly
                     where symbol in (select symbol from prices_monthly group by 1 order by 1 limit 200)""")
        print("  %-12s 200 只 -> %6d 行 %.0f ms" % (label, q.rowcount, (time.time()-t0)*1000))
    except Exception as e:
        print("  %s ERR %s" % (label, str(e)[:130]))
    c2.rollback(); c2.close()

print("\n### 触发器开/关：INSERT 到真表（回滚）的每行开销")
for label, disable in (("触发器开启", False), ("触发器关闭", True)):
    c3 = conn(auto=False); q = c3.cursor()
    q.execute("set local lock_timeout='8s'")
    try:
        if disable:
            q.execute("alter table prices_monthly disable trigger trg_guard_months")
        t0 = time.time()
        q.execute("""insert into prices_monthly(symbol,month,adj_close,raw_close,source,quality,basis)
                     select symbol, month, adj_close, raw_close, source||'_probe', quality, basis
                     from prices_monthly
                     where symbol in (select symbol from prices_monthly group by 1 order by 1 limit 50)""")
        print("  %-12s 50 只 -> %6d 行 %.0f ms" % (label, q.rowcount, (time.time()-t0)*1000))
    except Exception as e:
        print("  %s ERR %s" % (label, str(e)[:130]))
    c3.rollback(); c3.close()

print("\n### PostgREST / 角色")
cur.execute("select current_setting('pgrst.db_schemas', true)")
print("  pgrst.db_schemas:", cur.fetchone())
cur.execute("select rolname, rolconfig from pg_roles where rolname in ('authenticator','anon')")
for x in cur.fetchall():
    print("  role:", x)

print("\n### prices_monthly 30 秒写入活动")
def snap():
    for i in range(4):
        try:
            cur.execute("select count(*), max(fetched_at)::text from prices_monthly")
            return cur.fetchone()
        except Exception:
            time.sleep(2)
            c = conn(); cur = c.cursor()
    return None
a = snap(); time.sleep(30); b = snap()
print("  t0:", a)
print("  t30:", b)

print("\n### 近 24h 是否有写入（用 fetched_at 分布看）")
cur.execute("""select date_trunc('hour', fetched_at) h, count(*) from prices_monthly
               where fetched_at > now() - interval '48 hours' group by 1 order by 1 desc limit 12""")
for x in cur.fetchall():
    print("  ", x)

print("\n### validations 最近记录（谁在跑校验）")
cur.execute("""select id, dataset_id, check_name, status, left(detail,60), ran_at
               from validations order by id desc limit 12""")
for x in cur.fetchall():
    print("  ", x)

c.close()
print("\nDONE probe3b")
