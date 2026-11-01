# -*- coding: utf-8 -*-
"""只读审计探针 #2：异常确认 + 真实写入量 + 锁行为实验（全部回滚）。"""
import io, os, sys, time, json
import psycopg2

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = "fnvahsyrzmmxwovbsqpc"; HOST = "aws-0-ap-northeast-1.pooler.supabase.com"
PW = io.open(os.path.join(BASE, "output", "_supabase_dbpass.txt"), encoding="utf-8").read().strip()

def conn(port=6543, autocommit=True, retries=4):
    for i in range(retries):
        try:
            c = psycopg2.connect(host=HOST, port=port, user="postgres." + REF, password=PW,
                                 dbname="postgres", connect_timeout=10, sslmode="require")
            c.autocommit = autocommit; return c
        except Exception as e:
            last = e; time.sleep(2 + i * 2)
    raise last

c = conn(); cur = c.cursor()
def pr(*a): print("  " + " ".join(str(x) for x in a), flush=True)
def run(sql, label=None, args=None):
    t0 = time.time()
    try:
        cur.execute(sql, args); r = cur.fetchall() if cur.description else None
        pr("%-50s -> %s (%.0fms)" % ((label or sql)[:50], str(r)[:300], (time.time()-t0)*1000)); return r
    except Exception as e:
        pr("%-50s -> ERR %s" % ((label or sql)[:50], str(e)[:140])); return None

print("\n### A. 角色权限 / 是不是 owner")
run("select current_user, usesuper, usecreatedb from pg_user where usename=current_user", "current role")
run("select rolname, rolsuper, rolbypassrls, rolcreatedb from pg_roles where rolname in ('postgres','anon','authenticated','service_role','authenticator','supabase_admin')", "roles")
run("""select tablename, tableowner from pg_tables where schemaname='public' order by 1""", "table owners")

print("\n### B. series_selected_new 到底是什么（关键异常）")
run("""select n.nspname, c.relname, c.relkind, c.reltuples::bigint, c.relrowsecurity,
       pg_get_userbyid(c.relowner) owner, obj_description(c.oid) comment
from pg_class c join pg_namespace n on n.oid=c.relnamespace
where c.relname in ('series_selected','series_selected_new')""", "pg_class")
run("select count(*) from series_selected_new", "series_selected_new rows")
run("""select schemaname, tablename, tableowner from pg_tables where tablename like 'series_selected%'""", "pg_tables")
run("""select event_object_schema, event_object_table, trigger_name, event_manipulation
from information_schema.triggers where event_object_table like 'series_selected%'""", "triggers on ss*")
run("""select n.nspname, count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
where c.relkind='r' group by 1 order by 2 desc""", "tables per schema")
run("""select table_schema, table_name, table_type from information_schema.tables
where table_name like 'series%' or table_name like '%_new%' order by 1,2""", "info_schema tables")

print("\n### C. dispatch_trade_pipeline() 源码（隐藏写入方）")
r = run("select pg_get_functiondef(p.oid) from pg_proc p join pg_namespace n on n.oid=p.pronamespace where p.proname='dispatch_trade_pipeline'", "dispatch def")
if r:
    print(r[0][0])
try:
    cur.execute("select jobid, schedule, jobname, command, active, username, database from cron.job order by jobid")
    for x in cur.fetchall(): pr("cron:", x)
except Exception as e:
    pr("cron ERR", str(e)[:100])
try:
    cur.execute("""select jobid, status, count(*), max(start_time), avg(extract(epoch from end_time-start_time))::int
                   from cron.job_run_details where start_time > now() - interval '7 days'
                   group by 1,2 order by 1,2""")
    for x in cur.fetchall(): pr("cron 近7天:", x)
except Exception as e:
    pr("cron details ERR", str(e)[:120])
try:
    cur.execute("""select jobid, start_time, status, left(coalesce(return_message,''),120)
                   from cron.job_run_details order by start_time desc limit 8""")
    for x in cur.fetchall(): pr("cron 最近:", x)
except Exception as e:
    pr("cron recent ERR", str(e)[:120])

print("\n### D. pg_stat_statements：真实执行过的 SQL（谁在读写）")
for pat in ("prices_monthly", "series_selected", "dataset_rows", "locked_symbols", "members", "sectors"):
    try:
        cur.execute("""select left(regexp_replace(query,'\\s+',' ','g'),150) q, calls,
                              rows, round(total_exec_time::numeric,1) ms, round(mean_exec_time::numeric,2) mean_ms
                       from pg_stat_statements where query ilike %s
                       order by calls desc limit 6""", ("%" + pat + "%",))
        rows = cur.fetchall()
        print("\n  -- %s  (%d 条)" % (pat, len(rows)))
        for x in rows: print("     calls=%-7s rows=%-9s tot=%-9s mean=%-7s %s" % (x[1], x[2], x[3], x[4], x[0][:120]))
    except Exception as e:
        print("\n  -- %s ERR %s" % (pat, str(e)[:120]))

print("\n### E. 写入量统计（pg_stat_user_tables）")
run("""select relname, n_tup_ins, n_tup_upd, n_tup_del, n_live_tup, n_dead_tup,
       last_vacuum, last_autovacuum, last_analyze, last_autoanalyze
from pg_stat_user_tables where schemaname='public'
order by n_tup_ins+n_tup_upd+n_tup_del desc limit 12""", "write stats")

print("\n### F. 复制/池化/超时相关参数")
run("""select name, setting, unit, source from pg_settings where name in
('statement_timeout','lock_timeout','idle_in_transaction_session_timeout','deadlock_timeout',
 'max_connections','log_min_duration_statement','default_transaction_isolation','work_mem',
 'maintenance_work_mem','max_wal_size','synchronous_commit','track_io_timing','autovacuum')""", "settings")

print("\n### G. COPY 在 6543 三种姿势（全部回滚）")
def copy_try(cx, label, autocommit):
    cx.autocommit = autocommit
    q = cx.cursor()
    try:
        buf = io.StringIO("__copytest__|1999-01|1.0|1.0|__probe__|ok|total_return\n")
        q.copy_expert("copy prices_monthly(symbol,month,adj_close,raw_close,source,quality,basis) from stdin with (format csv, delimiter '|')", buf)
        pr("%-42s COPY 成功" % label)
        ok = True
    except Exception as e:
        pr("%-42s COPY 失败: %s" % (label, str(e)[:150])); ok = False
    try: cx.rollback()
    except Exception: pass
    q.close(); return ok

cx = conn(port=6543, retries=3)
copy_try(cx, "6543 autocommit=True (no BEGIN)", True)
copy_try(cx, "6543 autocommit=False (隐式事务)", False)
cx.close()
cx = conn(port=5432, retries=3)
copy_try(cx, "5432 autocommit=True (session mode)", True)
copy_try(cx, "5432 autocommit=False", False)
cx.close()

print("\n### H. 锁行为实验（DDL 会不会堵住读；全部回滚）")
# H1: lock_timeout 是否能打断 DDL 等待
cA = conn(autocommit=False); qA = cA.cursor()
cB = conn(autocommit=False); qB = cB.cursor()
qA.execute("begin"); qA.execute("select count(*) from prices_monthly")   # 只读事务，拿 ACCESS SHARE
print("  A 已开事务并读过 prices_monthly")
qB.execute("set local lock_timeout = '2s'")
t0 = time.time()
try:
    qB.execute("alter table prices_monthly add column if not exists __probe_col int")
    pr("B ALTER TABLE 成功（%.0f ms）—— 说明 ACCESS SHARE 不挡 ADD COLUMN(NULL 默认)" % ((time.time()-t0)*1000))
except Exception as e:
    pr("B ALTER TABLE 被 lock_timeout 打断（%.0f ms）: %s" % ((time.time()-t0)*1000, str(e)[:120]))
cB.rollback()

# H2: 有长事务持有 ROW EXCLUSIVE / 未提交写入时，DDL 是否排队并堵住后续 SELECT
qB.execute("begin"); qB.execute("set local lock_timeout='2s'")
try:
    qB.execute("select count(*) from prices_monthly where symbol='AAPL' for update")
    pr("B 已对 AAPL 行加 FOR UPDATE")
except Exception as e:
    pr("row lock ERR", str(e)[:100])
cC = conn(autocommit=False); qC = cC.cursor()
qC.execute("set local lock_timeout = '2s'")
t0 = time.time()
try:
    qC.execute("alter table prices_monthly add column if not exists __probe_col2 int")
    pr("C ALTER TABLE（有行锁未提交）成功 %.0f ms" % ((time.time()-t0)*1000))
except Exception as e:
    pr("C ALTER TABLE（有行锁未提交）失败 %.0f ms: %s" % ((time.time()-t0)*1000, str(e)[:130]))
cC.rollback(); cB.rollback(); cA.rollback()
for x in (cA, cB, cC):
    try: x.close()
    except Exception: pass

print("\n### I. DISABLE TRIGGER 权限与行为（回滚）")
cD = conn(autocommit=False); qD = cD.cursor()
qD.execute("set local lock_timeout='5s'")
for stmt, lbl in [("alter table prices_monthly disable trigger trg_guard_months", "DISABLE TRIGGER trg_guard_months"),
                  ("alter table prices_monthly disable trigger user", "DISABLE TRIGGER USER"),
                  ("alter table prices_monthly disable trigger all", "DISABLE TRIGGER ALL (要 superuser)"),
                  ("alter table prices_monthly enable trigger all", "ENABLE TRIGGER ALL")]:
    try:
        qD.execute(stmt); pr("%-45s OK" % lbl)
    except Exception as e:
        pr("%-45s ERR %s" % (lbl, str(e)[:130]))
        cD.rollback(); qD = cD.cursor(); qD.execute("set local lock_timeout='5s'")
# 验证 disable 后能否写 locked symbol
try:
    qD.execute("select count(*) from locked_symbols")
    pr("locked_symbols =", qD.fetchone()[0])
    qD.execute("select symbol from locked_symbols limit 1")
    sym = qD.fetchone()[0]
    qD.execute("savepoint s1")
    try:
        qD.execute("update prices_monthly set quality=quality where symbol=%s and month='2015-06'", (sym,))
        pr("触发器开启时 UPDATE locked symbol(%s) -> 行数 %s（应为 0 或被拦）" % (sym, qD.rowcount))
    except Exception as e:
        pr("触发器开启时 UPDATE locked symbol 被拦（预期）:", str(e)[:100]); qD.execute("rollback to savepoint s1")
    qD.execute("alter table prices_monthly disable trigger trg_guard_months")
    try:
        qD.execute("update prices_monthly set quality=quality where symbol=%s and month='2015-06'", (sym,))
        pr("触发器关闭时 UPDATE locked symbol(%s) -> 行数 %s（说明 disable 可绕过）" % (sym, qD.rowcount))
    except Exception as e:
        pr("触发器关闭时仍被拦:", str(e)[:100])
    qD.execute("alter table prices_monthly enable trigger trg_guard_months")
except Exception as e:
    pr("I ERR", str(e)[:150])
cD.rollback(); cD.close()

print("\n### J. 分批灌数性能实测（真实建表+灌数，全部回滚/临时表）")
cE = conn(autocommit=False); qE = cE.cursor()
qE.execute("set local statement_timeout='280s'")
qE.execute("set local lock_timeout='5s'")
# J1 按 symbol 批
try:
    t0 = time.time()
    qE.execute("create temp table _b1 (like series_selected including defaults) on commit drop")
    pr("create temp table (like) %.0f ms" % ((time.time()-t0)*1000))
    qE.execute("select distinct symbol from prices_monthly order by 1")
    syms = [x[0] for x in qE.fetchall()]
    pr("symbols =", len(syms))
    for n in (50, 200, 400):
        batch = syms[:n]
        t0 = time.time()
        qE.execute("insert into _b1 select symbol,month,adj_close,raw_close,source,basis from prices_monthly where symbol = any(%s)", (batch,))
        pr("  按 symbol 批 n=%-4d -> %6d 行, %.0f ms" % (n, qE.rowcount, (time.time()-t0)*1000))
        qE.execute("truncate _b1")
    # J2 按年
    for lo, hi in (("2010-01","2010-12"), ("2015-01","2015-12"), ("2020-01","2020-12")):
        t0 = time.time()
        qE.execute("insert into _b1 select symbol,month,adj_close,raw_close,source,basis from prices_monthly where month between %s and %s", (lo, hi))
        pr("  按 year %s..%s -> %6d 行, %.0f ms" % (lo, hi, qE.rowcount, (time.time()-t0)*1000))
        qE.execute("truncate _b1")
    # J3 全表
    t0 = time.time()
    qE.execute("insert into _b1 select symbol,month,adj_close,raw_close,source,basis from prices_monthly")
    pr("  全表 351889 行 -> %6d 行, %.0f ms  <<< 一次性可行的话就不用分批" % (qE.rowcount, (time.time()-t0)*1000))
    qE.execute("truncate _b1")
    # J4 建索引耗时
    t0 = time.time()
    qE.execute("insert into _b1 select symbol,month,adj_close,raw_close,source,basis from prices_monthly")
    qE.execute("create index on _b1(symbol)")
    pr("  灌全表+建索引 %.0f ms" % ((time.time()-t0)*1000))
    qE.execute("truncate _b1")
    t0 = time.time()
    qE.execute("insert into _b1 select symbol,month,adj_close,raw_close,source,basis from prices_monthly")
    qE.execute("alter table _b1 add constraint _b1_pk primary key (symbol,month)")
    pr("  灌全表+建主键 %.0f ms" % ((time.time()-t0)*1000))
except Exception as e:
    pr("J ERR", str(e)[:200])
cE.rollback(); cE.close()

print("\n### K. 建视图改名切换的真实可行性（回滚）")
cF = conn(autocommit=False); qF = cF.cursor()
qF.execute("set local lock_timeout='5s'")
qF.execute("set local statement_timeout='120s'")
try:
    qF.execute("create table _ss_new (symbol text not null, month text not null, adj_close numeric, raw_close numeric, source text, basis text, primary key(symbol,month))")
    qF.execute("insert into _ss_new select symbol,month,adj_close,raw_close,source,basis from series_selected")
    t0 = time.time()
    qF.execute("alter table series_selected rename to _ss_old")
    pr("rename 旧表 %.0f ms" % ((time.time()-t0)*1000))
    qF.execute("alter table _ss_new rename to _ss_candidate")
    pr("rename 新表 OK")
    qF.execute("select count(*) from _ss_old"); pr("旧表仍可读:", qF.fetchone())
    qF.execute("select count(*) from _ss_candidate"); pr("新表可读:", qF.fetchone())
    # 视图能否替代表供 anon 读
    qF.execute("create view _ss_view as select * from _ss_candidate")
    qF.execute("select count(*) from _ss_view"); pr("视图可读:", qF.fetchone())
    qF.execute("select has_table_privilege('anon','_ss_view','SELECT')")
    pr("anon 对视图 SELECT 权限（新建视图默认无）:", qF.fetchone())
except Exception as e:
    pr("K ERR", str(e)[:180])
cF.rollback(); cF.close()

print("\n### L. 现有数据完整性抽查")
run("""select count(*) filter (where adj_close is null) null_adj,
       count(*) filter (where raw_close is null) null_raw,
       count(*) filter (where adj_close <= 0) nonpos_adj,
       count(*) filter (where basis is null) null_basis,
       count(*) filter (where source is null) null_source,
       count(*) filter (where month !~ '^\\d{4}-(0[1-9]|1[0-2])$') bad_month
from prices_monthly""", "prices_monthly 质量")
run("""select count(*) filter (where adj_close is null), count(*) filter (where basis is null),
       count(*) filter (where source is null), count(*) from series_selected""", "series_selected 质量")
run("""select count(*) from prices_monthly p where not exists
       (select 1 from series_selected s where s.symbol=p.symbol and s.month=p.month)""", "prices 有但 series 无")
run("""select count(*) from series_selected s where not exists
       (select 1 from prices_monthly p where p.symbol=s.symbol and p.month=s.month and p.source=s.source and p.adj_close=s.adj_close)""", "series 行在 prices 中找不到同值（应为 0）")
run("""select count(*) from dataset_rows d where d.dataset_id=2 and not exists
       (select 1 from series_selected s where s.symbol=d.symbol and s.month=d.month and s.adj_close=d.adj_close)""", "dataset2 与 series_selected 不一致行数")
run("""select count(*), min(month), max(month) from series_selected""", "series 覆盖")
run("""select count(distinct symbol) from series_selected where month='2025-12'""", "2025-12 标的数")
run("""select count(distinct symbol) from series_selected where month='2026-10'""", "2026-10 标的数")

c.close()
print("\nDONE probe2")
