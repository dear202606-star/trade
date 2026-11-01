# -*- coding: utf-8 -*-
"""探针 #3：写入量/索引/锁/权限/默认权限（只读）。"""
import io, os, sys, time
import psycopg2
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = "fnvahsyrzmmxwovbsqpc"; HOST = "aws-0-ap-northeast-1.pooler.supabase.com"
PW = io.open(os.path.join(BASE, "output", "_supabase_dbpass.txt"), encoding="utf-8").read().strip()
def conn(port=6543, autocommit=True, retries=5):
    for i in range(retries):
        try:
            c = psycopg2.connect(host=HOST, port=port, user="postgres."+REF, password=PW,
                                 dbname="postgres", connect_timeout=10, sslmode="require")
            c.autocommit = autocommit; return c
        except Exception as e:
            last = e; time.sleep(2+i*3)
    raise last
def pr(*a): print("  " + " ".join(str(x) for x in a), flush=True)
c = conn(); cur = c.cursor()

print("### 4. 写入量 + autovacuum")
cur.execute("""select relname, n_tup_ins, n_tup_upd, n_tup_del, n_live_tup, n_dead_tup,
                      round(100.0*n_dead_tup/greatest(n_live_tup+n_dead_tup,1),1),
                      last_autovacuum, last_autoanalyze
               from pg_stat_user_tables where schemaname='public'
               order by n_tup_ins+n_tup_upd+n_tup_del desc limit 10""")
pr("%-18s %10s %10s %9s %9s %9s %6s %-19s %s" % ("rel","ins","upd","del","live","dead","dead%","last_autovac","last_autoanalyze"))
for x in cur.fetchall():
    print("  %-18s %10s %10s %9s %9s %9s %6s %-19s %s" % (x[0], x[1], x[2], x[3], x[4], x[5], x[6], str(x[7])[:19], str(x[8])[:19]))

print("\n### 5. 索引扫描计数（找冗余/无用索引）")
cur.execute("""select relname, indexrelname, idx_scan, pg_size_pretty(pg_relation_size(indexrelid))
               from pg_stat_user_indexes where schemaname='public' order by idx_scan, relname""")
for x in cur.fetchall(): print("  %-18s %-38s scans=%-8s %s" % x)

print("\n### 6. 当前锁（迁移前必须清场）")
cur.execute("""select c.relname, l.mode, l.granted, a.pid, a.state, now()-a.xact_start, left(a.query,50)
               from pg_locks l join pg_class c on c.oid=l.relation
               left join pg_stat_activity a on a.pid=l.pid
               where c.relname in ('prices_monthly','series_selected','dataset_rows') order by 1,2""")
rs = cur.fetchall(); pr("持锁行数:", len(rs))
for x in rs: pr(x)

print("\n### 7. prices_monthly 20 秒写入观察")
cur.execute("select count(*), max(fetched_at) from prices_monthly"); a = cur.fetchone()
time.sleep(20)
cur.execute("select count(*), max(fetched_at) from prices_monthly"); b = cur.fetchone()
pr("count/max(fetched_at):", a, "->", b, "(写入活动：%s)" % ("有" if a != b else "无"))

print("\n### 8. RLS 与策略总表")
cur.execute("""select c.relname, c.relrowsecurity,
                      (select count(*) from pg_policies p where p.tablename=c.relname) npol,
                      has_table_privilege('anon','public.'||quote_ident(c.relname),'INSERT') a_ins,
                      has_table_privilege('anon','public.'||quote_ident(c.relname),'UPDATE') a_upd
               from pg_class c join pg_namespace n on n.oid=c.relnamespace
               where n.nspname='public' and c.relkind='r' order by 1""")
for x in cur.fetchall(): pr("%-18s rls=%-6s policies=%-2s anon_ins=%-6s anon_upd=%s" % x)

print("\n### 9. 默认权限（新表会不会自动给 anon）")
cur.execute("""select pg_get_userbyid(defaclrole), coalesce(defaclnamespace::regnamespace::text,'-'), defaclobjtype, defaclacl::text
               from pg_default_acl order by 1,2""")
for x in cur.fetchall(): pr(x)

print("\n### 10. prices_monthly 中 (symbol,month) 多源重复的分布（PK 设计依据）")
cur.execute("""select nsrc, count(*) from (
                 select symbol, month, count(*) nsrc from prices_monthly group by 1,2) z
               group by 1 order by 1""")
for x in cur.fetchall(): pr("每月 %s 个源的行组数 = %s" % x)

print("\n### 11. 触发器在每个 batch 上的真实开销（对比 disable 前后）")
cur.execute("set statement_timeout='120s'")
for label, disable in (("触发器开启", False), ("触发器关闭", True)):
    cE = conn(autocommit=False); q = cE.cursor()
    q.execute("set local lock_timeout='8s'")
    try:
        if disable:
            q.execute("alter table prices_monthly disable trigger trg_guard_months")
        q.execute("create temp table _perf (like prices_monthly) on commit drop")
        t0 = time.time()
        q.execute("""insert into _perf select symbol,month,adj_close,raw_close,source,quality,fetched_at,basis
                     from prices_monthly where symbol in (select symbol from prices_monthly limit 200)""")
        pr("%-12s 200 只 -> %6d 行 %.0f ms" % (label, q.rowcount, (time.time()-t0)*1000))
    except Exception as e:
        pr(label, "ERR", str(e)[:120])
    cE.rollback(); cE.close()

print("\n### 12. 是否有 pg_temp 表残留（会话模式下的坑）")
cur.execute("select nspname from pg_namespace where nspname like 'pg_temp%'")
pr("pg_temp schemas:", [x[0] for x in cur.fetchall()])

print("\n### 13. 大对象/序列/依赖")
cur.execute("select count(*) from pg_largeobject_metadata"); pr("large objects:", cur.fetchone())
cur.execute("""select relname from pg_class where relkind='S' and relnamespace='public'::regnamespace order by 1""")
pr("sequences:", [x[0] for x in cur.fetchall()])

print("\n### 14. PostgREST 暴露面（哪些表在 API 上）")
cur.execute("""select n.nspname, c.relname, c.relkind from pg_class c join pg_namespace n on n.oid=c.relnamespace
               where n.nspname in ('public') and c.relkind in ('r','v','m','f') order by 2""")
pr("public 下可被 PostgREST 暴露的对象数:", len(cur.fetchall()))
cur.execute("select current_setting('pgrst.db_schemas', true)")
pr("pgrst.db_schemas:", cur.fetchone())
cur.execute("select rolname, rolconfig from pg_roles where rolname in ('authenticator','anon')")
pr("roles config:", cur.fetchall())

c.close(); print("\nDONE probe3")
