# -*- coding: utf-8 -*-
"""清理审计探针遗留的 __copytest__ 行 + 复核基线指纹 + 补充取证。"""
import io, os, sys, time, json
import psycopg2

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = "fnvahsyrzmmxwovbsqpc"; HOST = "aws-0-ap-northeast-1.pooler.supabase.com"
PW = io.open(os.path.join(BASE, "output", "_supabase_dbpass.txt"), encoding="utf-8").read().strip()

def conn(port=6543, autocommit=True, retries=5):
    for i in range(retries):
        try:
            c = psycopg2.connect(host=HOST, port=port, user="postgres." + REF, password=PW,
                                 dbname="postgres", connect_timeout=10, sslmode="require")
            c.autocommit = autocommit; return c
        except Exception as e:
            last = e; time.sleep(2 + i * 3)
    raise last

def pr(*a): print("  " + " ".join(str(x) for x in a), flush=True)

c = conn(); cur = c.cursor()

print("### 0. 清理探针垃圾行（symbol='__copytest__'）")
cur.execute("select symbol, month, source, adj_close, fetched_at from prices_monthly where symbol like '__%probe%' or symbol like '__copy%'")
rows = cur.fetchall()
pr("命中行:", rows)
if rows:
    cur.execute("delete from prices_monthly where symbol = '__copytest__' and source = '__probe__' and month = '1999-01'")
    pr("已删除行数:", cur.rowcount)
else:
    pr("无残留")

print("\n### 0b. 复核 prices_monthly 指纹是否回到基线 ffd63ae8f4069a1a8b343e0318a3fa65")
cur.execute("""select count(*), md5(string_agg(h,'|' order by h)) from (
   select symbol||'|'||month||'|'||coalesce(adj_close::text,'')||'|'||coalesce(raw_close::text,'')||'|'||
          coalesce(source,'')||'|'||coalesce(basis,'') h from prices_monthly) z""")
n, h = cur.fetchone()
pr("现在: rows=%s md5=%s" % (n, h))
pr("基线: rows=351889 md5=ffd63ae8f4069a1a8b343e0318a3fa65")
pr("一致？", "✅ 完全一致（审计未留下任何痕迹）" if (n == 351889 and h == "ffd63ae8f4069a1a8b343e0318a3fa65") else "❌ 不一致，需排查")

print("\n### 1. series_selected_new 复核")
for sql in ["select n.nspname, c.relname, c.relkind from pg_class c join pg_namespace n on n.oid=c.relnamespace where c.relname like 'series_selected%'",
            "select count(*) from pg_indexes where schemaname='public' and tablename='series_selected_new'",
            "select count(*) from pg_constraint where conname like 'series_selected_new%'",
            "select count(*) from pg_policies where tablename='series_selected_new'",
            "select count(*) from information_schema.columns where table_schema='public' and table_name='series_selected_new'",
            "select nspname from pg_namespace order by 1"]:
    try:
        cur.execute(sql); pr(sql[:70], "->", cur.fetchall())
    except Exception as e:
        pr(sql[:70], "-> ERR", str(e)[:100])

print("\n### 2. pg_stat_statements 里的 DDL 痕迹（谁在改建/加固）")
for pat in ("series_selected_new", "enable row level security", "revoke", "disable trigger",
            "create or replace function", "drop table", "create table"):
    try:
        cur.execute("""select calls, left(regexp_replace(query,'\\s+',' ','g'),130) q
                       from pg_stat_statements where query ilike %s
                       order by calls desc limit 8""", ("%" + pat + "%",))
        rs = cur.fetchall()
        print("\n  -- %s (%d 条)" % (pat, len(rs)))
        for x in rs: print("     calls=%-6s %s" % (x[0], x[1]))
    except Exception as e:
        print("\n  -- %s ERR %s" % (pat, str(e)[:100]))

print("\n### 3. dataset_rows 的 15 秒查询 / UPDATE 来自哪（按耗时找）")
try:
    cur.execute("""select calls, rows, round(mean_exec_time::numeric,1) mean_ms,
                          left(regexp_replace(query,'\\s+',' ','g'),170) q
                   from pg_stat_statements
                   where query ilike '%dataset_rows%' order by mean_exec_time desc limit 10""")
    for x in cur.fetchall(): print("     calls=%-5s rows=%-8s mean=%-9s %s" % x)
except Exception as e:
    pr("ERR", str(e)[:120])

print("\n### 4. 写入量 + autovacuum 全量")
cur.execute("""select relname, n_tup_ins, n_tup_upd, n_tup_del, n_live_tup, n_dead_tup,
                      round(100.0*n_dead_tup/greatest(n_live_tup+n_dead_tup,1),1) dead_pct,
                      last_autovacuum, last_autoanalyze
               from pg_stat_user_tables where schemaname='public'
               order by n_tup_ins+n_tup_upd+n_tup_del desc limit 10""")
pr("%-18s %10s %10s %9s %9s %9s %7s %s" % ("rel", "ins", "upd", "del", "live", "dead", "dead%", "last_autovacuum"))
for x in cur.fetchall():
    print("  %-18s %10s %10s %9s %9s %9s %7s %s" % x)

print("\n### 5. 索引冗余/无用检查")
cur.execute("""select relname, indexrelname, idx_scan, pg_size_pretty(pg_relation_size(indexrelid)) sz
               from pg_stat_user_indexes where schemaname='public' order by idx_scan, relname""")
pr("%-18s %-36s %8s %8s" % ("table", "index", "scans", "size"))
for x in cur.fetchall():
    print("  %-18s %-36s %8s %8s" % x)

print("\n### 6. 表级锁被谁持有（迁移前必须清场）")
cur.execute("""select l.locktype, l.mode, l.granted, c.relname, a.pid, a.state,
                      now()-a.xact_start xact_age, left(a.query,60)
               from pg_locks l left join pg_class c on c.oid=l.relation
               left join pg_stat_activity a on a.pid=l.pid
               where c.relname in ('prices_monthly','series_selected','dataset_rows')
               order by c.relname, l.mode""")
rs = cur.fetchall()
pr("当前锁数:", len(rs))
for x in rs: pr(x)

print("\n### 7. promote_staging 是否真的在写（增量观察）")
cur.execute("select count(*), max(inserted_at) from ingest_staging")
pr("ingest_staging:", cur.fetchone())
cur.execute("select count(*) from prices_monthly")
a = cur.fetchone()[0]
time.sleep(20)
cur.execute("select count(*) from prices_monthly")
b = cur.fetchone()[0]
pr("20 秒内 prices_monthly 行数变化:", a, "->", b, "(差值 %+d)" % (b - a))

print("\n### 8. 关键：anon 能不能真写 prices_monthly（只读验证权限面）")
cur.execute("""select has_table_privilege('anon','public.prices_monthly','INSERT') ins,
                      has_table_privilege('anon','public.prices_monthly','UPDATE') upd,
                      has_table_privilege('anon','public.prices_monthly','DELETE') del,
                      (select relrowsecurity from pg_class where oid='public.prices_monthly'::regclass) rls,
                      (select count(*) from pg_policies where tablename='prices_monthly') npol""")
pr("prices_monthly  anon[ins,upd,del] / rls / 策略数 =", cur.fetchone())
cur.execute("""select relname, relrowsecurity, (select count(*) from pg_policies p where p.tablename=c.relname)
               from pg_class c join pg_namespace n on n.oid=c.relnamespace
               where n.nspname='public' and c.relkind='r' order by 1""")
for x in cur.fetchall(): pr("  %-18s rls=%-6s policies=%s" % x)

print("\n### 9. 默认权限（新表会不会自动给 anon）")
cur.execute("""select pg_get_userbyid(defaclrole) role, defaclnamespace::regnamespace::text ns,
                      defaclobjtype, defaclacl::text
               from pg_default_acl order by 1,2""")
for x in cur.fetchall(): pr(x)

c.close()
print("\nDONE cleanup")
