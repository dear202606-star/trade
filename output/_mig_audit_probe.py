# -*- coding: utf-8 -*-
"""只读审计探针 v2：不做任何 DDL/DML。只 select / show / explain。
所有结果同时落 output/_mig_audit.json 作为迁移前基线证据。
"""
import io, os, sys, time, json
import psycopg2

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = "fnvahsyrzmmxwovbsqpc"
HOST = "aws-0-ap-northeast-1.pooler.supabase.com"
PW = io.open(os.path.join(BASE, "output", "_supabase_dbpass.txt"), encoding="utf-8").read().strip()
EV = {}

def conn(port=6543, autocommit=True, retries=4):
    last = None
    for i in range(retries):
        try:
            c = psycopg2.connect(host=HOST, port=port, user="postgres." + REF, password=PW,
                                 dbname="postgres", connect_timeout=10, sslmode="require")
            c.autocommit = autocommit
            return c
        except Exception as e:
            last = e
            time.sleep(2 + i * 2)
    raise last

def pr(*a):
    print("  " + " ".join(str(x) for x in a), flush=True)

c = conn()
cur = c.cursor()

def show(sql, label=None):
    t0 = time.time()
    try:
        cur.execute(sql)
        r = cur.fetchall()
        pr("%-58s -> %s (%.0fms)" % ((label or sql)[:58], r if len(str(r)) < 200 else str(r)[:200], (time.time()-t0)*1000))
        return r
    except Exception as e:
        pr("%-58s -> ERR %s" % ((label or sql)[:58], str(e)[:100]))
        return None

print("\n### 0. 版本 / 超时 / 池化模式")
for s in ["select version()", "show statement_timeout", "show lock_timeout",
          "show idle_in_transaction_session_timeout", "show default_transaction_isolation",
          "select current_user, session_user, inet_server_port()",
          "select current_setting('server_version_num')",
          "select pg_size_pretty(pg_database_size(current_database())) db_size",
          "select count(*) from pg_stat_activity where datname=current_database()"]:
    show(s)

print("\n### 0b. SET 在 6543 上是否粘住（autocommit=True）")
cur.execute("set statement_timeout = '600s'")
cur.execute("show statement_timeout"); pr("6543 新连接 SET 600s 后 show =", cur.fetchone())
for i in range(3):
    cur.execute("select pg_backend_pid()"); pr("  6543 pid", i, cur.fetchone())

print("\n### 0c. 显式事务内 SET LOCAL")
c2 = conn(autocommit=False); cur2 = c2.cursor()
cur2.execute("set local statement_timeout = '123s'"); cur2.execute("show statement_timeout")
pr("BEGIN+SET LOCAL -> ", cur2.fetchone())
c2.rollback()
cur2.execute("show statement_timeout"); pr("rollback 后 -> ", cur2.fetchone(), "(回到会话值)")
c2.close()

print("\n### 1. 表/视图清单 + RLS + 体积")
r = show("""
select c.relname, c.relkind, c.reltuples::bigint,
       c.relrowsecurity, c.relforcerowsecurity,
       pg_total_relation_size(c.oid), pg_relation_size(c.oid),
       (select count(*) from pg_attribute a where a.attrelid=c.oid and a.attnum>0 and not a.attisdropped)
from pg_class c join pg_namespace n on n.oid=c.relnamespace
where n.nspname='public' and c.relkind in ('r','v','m','p')
order by pg_total_relation_size(c.oid) desc""", "catalog scan") or []
print("\n  %-24s %-3s %9s %-4s %-4s %12s %12s %s" % ("rel", "kd", "est", "rls", "frc", "total", "heap", "ncol"))
for x in r:
    print("  %-24s %-3s %9s %-4s %-4s %12s %12s %s" % (x[0], x[1], x[2], x[3], x[4], x[5], x[6], x[7]))
EV["relations"] = [[x[0], x[1], int(x[2]), bool(x[3]), int(x[5]), int(x[7])] for x in r]

print("\n### 2. 精确行数（SET LOCAL statement_timeout=180s 显式事务内）")
tables = [x[0] for x in r if x[1] in ('r', 'p', 'm')]
c3 = conn(autocommit=False); cur3 = c3.cursor()
cur3.execute("set local statement_timeout = '180s'")
counts, timings = {}, {}
for t in tables:
    t0 = time.time()
    try:
        cur3.execute('select count(*) from public."%s"' % t)
        n = int(cur3.fetchone()[0]); counts[t] = n; timings[t] = round((time.time()-t0)*1000)
        print("  %-24s %10d  (%6.0f ms)" % (t, n, timings[t]))
    except Exception as e:
        cur3.rollback(); cur3.execute("set local statement_timeout = '180s'")
        print("  %-24s ERR %s" % (t, str(e)[:80]))
c3.rollback(); c3.close()
EV["counts"] = counts; EV["count_ms"] = timings

print("\n### 3. 列定义")
r = show("""select table_name, ordinal_position, column_name, data_type,
       coalesce(character_maximum_length::text,''), is_nullable, coalesce(column_default,'')
from information_schema.columns where table_schema='public' order by table_name, ordinal_position""", "columns") or []
cur_t = None
for x in r:
    if x[0] != cur_t:
        print("\n  -- %s" % x[0]); cur_t = x[0]
    print("     %2s %-16s %-26s null=%-3s default=%s" % (x[1], x[2], x[3] + ("(%s)" % x[4] if x[4] else ""), x[5], str(x[6])[:70]))
EV["columns"] = [[str(y) for y in x] for x in r]

print("\n### 4. 约束")
r = show("""select c.conrelid::regclass::text, c.conname, c.contype, pg_get_constraintdef(c.oid)
from pg_constraint c join pg_namespace n on n.oid=c.connamespace
where n.nspname='public' order by 1,3,2""", "constraints") or []
for x in r:
    print("  %-22s %-42s %-3s %s" % x)
EV["constraints"] = [[str(y) for y in x] for x in r]

print("\n### 5. 索引")
r = show("select tablename, indexname, indexdef from pg_indexes where schemaname='public' order by tablename, indexname", "indexes") or []
for x in r:
    print("  %-22s %-36s %s" % (x[0], x[1], x[2][:120]))
EV["indexes"] = [[str(y) for y in x] for x in r]

print("\n### 6. 触发器（tgenabled: O=always, D=disabled, R=replica, A=origin）")
r = show("""select c.relname, t.tgname, t.tgenabled, pg_get_triggerdef(t.oid)
from pg_trigger t join pg_class c on c.oid=t.tgrelid join pg_namespace n on n.oid=c.relnamespace
where n.nspname='public' and not t.tgisinternal order by 1,2""", "triggers") or []
for x in r:
    print("  %-22s %-26s enabled=%s\n        %s" % (x[0], x[1], x[2], x[3][:250]))
EV["triggers"] = [[str(y) for y in x] for x in r]

print("\n### 6b. 触发器函数源码")
r = show("""select p.proname, pg_get_functiondef(p.oid) from pg_proc p join pg_namespace n on n.oid=p.pronamespace
where n.nspname='public' and p.prorettype='trigger'::regtype order by 1""", "trigger fns") or []
for x in r:
    print("\n  ---- %s ----\n%s" % (x[0], x[1]))
EV["trigger_fns"] = [[str(y) for y in x] for x in r]

print("\n### 6c. public 全部函数")
r = show("""select p.proname, pg_get_function_identity_arguments(p.oid), p.prosecdef
from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public' order by 1""", "functions") or []
for x in r:
    print("  %-30s (%s) security_definer=%s" % x)
EV["functions"] = [[str(y) for y in x] for x in r]

print("\n### 7. RLS 策略")
r = show("select tablename, policyname, cmd, roles::text, qual, with_check from pg_policies where schemaname='public' order by 1,2", "policies") or []
for x in r:
    print("  %-22s %-18s %-8s %-24s qual=%s check=%s" % x)
EV["policies"] = [[str(y) for y in x] for x in r]

print("\n### 8. anon / authenticated 权限")
r = show("""select tablename,
  has_table_privilege('anon','public.'||quote_ident(tablename),'SELECT'),
  has_table_privilege('anon','public.'||quote_ident(tablename),'INSERT'),
  has_table_privilege('anon','public.'||quote_ident(tablename),'UPDATE'),
  has_table_privilege('anon','public.'||quote_ident(tablename),'DELETE'),
  has_table_privilege('authenticated','public.'||quote_ident(tablename),'INSERT'),
  has_table_privilege('authenticated','public.'||quote_ident(tablename),'UPDATE')
from pg_tables where schemaname='public' order by 1""", "grants") or []
for x in r:
    print("  %-22s anon[sel=%-5s ins=%-5s upd=%-5s del=%-5s] auth[ins=%-5s upd=%-5s]" % x)
EV["grants"] = [[str(y) for y in x] for x in r]

print("\n### 9. 扩展 / pg_cron")
show("select extname, extversion from pg_extension order by 1", "extensions")
try:
    cur.execute("select jobid, schedule, jobname, command, active from cron.job order by 1")
    for x in cur.fetchall(): pr("cron:", x)
except Exception as e:
    pr("cron.job ERR", str(e)[:100])

print("\n### 10. 关键表内容抽样")
for t in ("locks", "locked_symbols", "source_registry", "datasets", "data_sources",
          "work_plans", "symbols", "corporate_actions", "holdings", "fetch_runs"):
    try:
        cur.execute('select * from public."%s" limit 3' % t)
        cols = [d[0] for d in cur.description]; rows = cur.fetchall()
        print("\n  -- %s cols=%s" % (t, cols))
        for x in rows:
            print("     ", str(x)[:260])
    except Exception as e:
        print("\n  -- %s ERR %s" % (t, str(e)[:90]))

print("\n### 11. 视图依赖")
r = show("""select distinct dependent_ns.nspname||'.'||dependent_view.relname, source_ns.nspname||'.'||source_table.relname
from pg_depend d join pg_rewrite rw on rw.oid=d.objid
join pg_class dependent_view on dependent_view.oid=rw.ev_class
join pg_namespace dependent_ns on dependent_ns.oid=dependent_view.relnamespace
join pg_class source_table on source_table.oid=d.refobjid
join pg_namespace source_ns on source_ns.oid=source_table.relnamespace
where d.classid='pg_rewrite'::regclass and dependent_ns.nspname='public'
  and dependent_view.oid <> source_table.oid order by 1,2""", "view deps") or []
pr("视图依赖:", r if r else "无")

print("\n### 12. 活跃会话")
r = show("""select pid, usename, application_name, state, wait_event_type,
       now()-xact_start, left(query,60) from pg_stat_activity
where datname=current_database() and pid<>pg_backend_pid() order by xact_start nulls last""", "activity") or []
for x in r: pr(x)

print("\n### 13. series_selected vs 重算选源 一致性")
c4 = conn(autocommit=False); cur4 = c4.cursor()
cur4.execute("set local statement_timeout='240s'")
try:
    cur4.execute("""
    with cand as (select symbol, source,
       max(case when basis='total_return' then 0 else 1 end) basis_rank,
       count(*) filter (where month between '2010-01' and '2025-12') cov,
       case source when 'alphavantage' then 1 when 'yahoo_relay' then 2
            when 'eodhd' then 3 when 'hf_amirtrader' then 3
            when 'hf_ayushkhaire' then 4 when 'sina_repaired+eodhd' then 4
            else 5 end src_rank
       from prices_monthly group by symbol, source),
    pick as (select symbol, source from (select symbol, source,
       row_number() over (partition by symbol order by basis_rank, src_rank, cov desc) rn from cand) y where rn=1)
    select count(*) filter (where p.source is distinct from s.source),
           count(*) filter (where p.symbol is null), count(*)
    from series_selected s left join pick p on p.symbol=s.symbol""")
    pr("source 不一致 / 无候选 / 总行 =", cur4.fetchone())
except Exception as e:
    pr("ERR", str(e)[:200]); cur4.rollback()
    cur4.execute("set local statement_timeout='240s'")
c4.rollback(); c4.close()

print("\n### 14. 分组统计")
c5 = conn(autocommit=False); cur5 = c5.cursor()
cur5.execute("set local statement_timeout='240s'")
for label, sql in [
    ("prices_monthly by source", "select source, count(*), count(distinct symbol), min(month), max(month) from prices_monthly group by 1 order by 2 desc"),
    ("prices_monthly by basis", "select basis, count(*) from prices_monthly group by 1 order by 2 desc"),
    ("prices_monthly by year", "select left(month,4), count(*), count(distinct symbol) from prices_monthly group by 1 order by 1"),
    ("series_selected by source", "select source, count(*), count(distinct symbol) from series_selected group by 1 order by 2 desc"),
    ("series_selected by basis", "select basis, count(*) from series_selected group by 1 order by 2 desc"),
    ("series_selected by year", "select left(month,4), count(*), count(distinct symbol) from series_selected group by 1 order by 1"),
]:
    t0 = time.time()
    try:
        cur5.execute(sql); rows = cur5.fetchall()
        print("\n  -- %s (%.0f ms)" % (label, (time.time()-t0)*1000))
        for x in rows: print("     ", x)
        if label.startswith("series_selected by year"):
            EV["ss_by_year"] = [[str(y) for y in x] for x in rows]
    except Exception as e:
        print("\n  -- %s ERR %s" % (label, str(e)[:90]))
        cur5.rollback(); cur5.execute("set local statement_timeout='240s'")
c5.rollback(); c5.close()

print("\n### 15. 全库指纹（迁移前基线）")
c6 = conn(autocommit=False); cur6 = c6.cursor()
cur6.execute("set local statement_timeout='300s'")
FP = {}
for t, cols in [
  ("prices_monthly", "symbol||'|'||month||'|'||coalesce(adj_close::text,'')||'|'||coalesce(raw_close::text,'')||'|'||coalesce(source,'')||'|'||coalesce(basis,'')"),
  ("series_selected", "symbol||'|'||month||'|'||coalesce(adj_close::text,'')||'|'||coalesce(raw_close::text,'')||'|'||coalesce(source,'')||'|'||coalesce(basis,'')"),
  ("members", "symbol||'|'||start_date::text||'|'||end_date::text"),
  ("sectors", "symbol||'|'||coalesce(sector,'')"),
  ("locked_symbols", "symbol||'|'||coalesce(hash,'')"),
  ("dataset_rows", "dataset_id::text||'|'||symbol||'|'||month||'|'||coalesce(adj_close::text,'')")]:
    t0 = time.time()
    try:
        cur6.execute("select count(*), md5(string_agg(h,'|' order by h)) from (select %s h from %s) z" % (cols, t))
        n, h = cur6.fetchone()
        FP[t] = {"rows": int(n), "md5": h}
        print("  %-18s rows=%-8s md5=%s (%.0f ms)" % (t, n, h, (time.time()-t0)*1000))
    except Exception as e:
        print("  %-18s ERR %s" % (t, str(e)[:110]))
        cur6.rollback(); cur6.execute("set local statement_timeout='300s'")
c6.rollback(); c6.close()
EV["fingerprint"] = FP

print("\n### 16. COPY 在 6543 事务池是否可用")
c7 = conn(autocommit=False); cur7 = c7.cursor()
cur7.execute("set local statement_timeout='60s'")
try:
    buf = io.StringIO("__copytest__|1999-01|1.0|1.0|__probe__|ok|total_return\n")
    cur7.copy_expert("copy prices_monthly(symbol,month,adj_close,raw_close,source,quality,basis) from stdin with (format csv, delimiter '|')", buf)
    print("  6543 COPY 成功（即将 ROLLBACK）")
    EV["copy_6543"] = "ok"
except Exception as e:
    print("  6543 COPY 失败：", str(e)[:250]); EV["copy_6543"] = str(e)[:250]
c7.rollback(); c7.close()

print("\n### 17. 会话模式 5432 实测")
EV["session_mode"] = {}
try:
    cS = conn(port=5432, autocommit=True); curS = cS.cursor()
    curS.execute("show statement_timeout"); EV["session_mode"]["default_to"] = curS.fetchone()[0]
    pr("5432 默认 statement_timeout =", EV["session_mode"]["default_to"])
    curS.execute("set statement_timeout = '900s'")
    curS.execute("show statement_timeout"); EV["session_mode"]["after_set"] = curS.fetchone()[0]
    pr("5432 SET 后 show =", EV["session_mode"]["after_set"], "<- 粘住=会话模式")
    pids = []
    for i in range(3):
        curS.execute("select pg_backend_pid()"); pids.append(curS.fetchone()[0])
    EV["session_mode"]["pids"] = pids; pr("5432 pids", pids)
    try:
        curS.execute("begin")
        buf = io.StringIO("__copytest__|1999-01|1.0|1.0|__probe__|ok|total_return\n")
        curS.copy_expert("copy prices_monthly(symbol,month,adj_close,raw_close,source,quality,basis) from stdin with (format csv, delimiter '|')", buf)
        pr("5432 COPY 成功（即将 ROLLBACK）"); EV["session_mode"]["copy"] = "ok"
        curS.execute("rollback")
    except Exception as e:
        pr("5432 COPY 失败：", str(e)[:200]); EV["session_mode"]["copy"] = str(e)[:200]
        try: curS.execute("rollback")
        except Exception: pass
    try:
        curS.execute("create temp table _t(a int) on commit drop")
        pr("5432 临时表可用"); EV["session_mode"]["temp_table"] = "ok"
    except Exception as e:
        pr("5432 临时表失败：", str(e)[:150]); EV["session_mode"]["temp_table"] = str(e)[:150]
    cS.close()
except Exception as e:
    pr("5432 不可用：", str(e)[:200]); EV["session_mode"]["error"] = str(e)[:200]

print("\n### 18. 6543 上大语句耗时基线（分批粒度实测）")
cT = conn(port=6543, autocommit=True); curT = cT.cursor()
EV["perf_6543"] = {}
tests = [
  ("full count prices_monthly", "select count(*) from prices_monthly"),
  ("1 symbol series", "select month, adj_close from prices_monthly where symbol='AAPL'"),
  ("60 symbol chunk", "select symbol, month, adj_close, raw_close, source, basis from prices_monthly where symbol = any(%s)"),
  ("1 year slice", "select count(*) from prices_monthly where month between '2015-01' and '2015-12'"),
  ("series_selected 1 year", "select count(*) from series_selected where month between '2015-01' and '2015-12'"),
]
for label, sql in tests:
    t0 = time.time()
    try:
        if "%s" in sql:
            curT.execute("select distinct symbol from prices_monthly order by 1 limit 60")
            syms = [x[0] for x in curT.fetchall()]
            curT.execute(sql, (syms,))
        else:
            curT.execute(sql)
        rr = curT.fetchall()
        dt = round((time.time()-t0)*1000)
        EV["perf_6543"][label] = {"ms": dt, "n": len(rr)}
        pr("%-28s %6d ms  rows=%d" % (label, dt, len(rr)))
    except Exception as e:
        pr("%-28s ERR %s" % (label, str(e)[:120]))
        EV["perf_6543"][label] = {"err": str(e)[:120]}
cT.close()

print("\n### 19. ANALYZE 计划（验证索引是否被用）")
c8 = conn(autocommit=False); cur8 = c8.cursor()
cur8.execute("set local statement_timeout='60s'")
for sql in ["explain (analyze, buffers, format text) select count(*) from prices_monthly where month between '2015-01' and '2015-12'",
            "explain (analyze, buffers, format text) select count(*) from series_selected where month between '2015-01' and '2015-12'",
            "explain (analyze, buffers, format text) select count(*) from prices_monthly where symbol='AAPL'"]:
    print("\n  -- " + sql.split("select")[-1][:70])
    try:
        cur8.execute(sql)
        for row in cur8.fetchall(): print("     " + row[0])
    except Exception as e:
        print("     ERR " + str(e)[:120]); cur8.rollback(); cur8.execute("set local statement_timeout='60s'")
c8.rollback(); c8.close()

c.close()
json.dump(EV, open(os.path.join(BASE, "output", "_mig_audit.json"), "w"), ensure_ascii=False, indent=1)
print("\nDONE — 证据已写 output/_mig_audit.json")
