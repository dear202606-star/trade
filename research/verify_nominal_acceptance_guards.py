"""Exercise nominal protection in temporary tables; --apply deploys atomically."""
import argparse
import json
import sys
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import psycopg2
import db_sources


def blocked(cursor,sql,params=None):
    cursor.execute('savepoint guard_probe')
    try:
        cursor.execute(sql,params)
    except psycopg2.errors.RaiseException:
        cursor.execute('rollback to savepoint guard_probe')
        cursor.execute('release savepoint guard_probe')
        return
    cursor.execute('rollback to savepoint guard_probe')
    raise AssertionError('Guard allowed a protected mutation')


def shadow_probe(sql):
    c=db_sources.db()
    try:
        with c.cursor() as q:
            q.execute("set local statement_timeout='30s'")
            q.execute('create temp table nominal_monthly (like public.nominal_monthly including all)')
            q.execute('create temp table sys_event (like public.sys_event including all)')
            q.execute("insert into pg_temp.nominal_monthly select * from public.nominal_monthly where symbol='ABNB' and month='2025-12'")
            q.execute("insert into pg_temp.sys_event(kind,source,subject,status,metrics) select kind,source,subject,status,metrics from public.sys_event where source='fund_native_closing_fact' and subject='ABNB:2025-12-31' and status='pass'")
            q.execute("create temp table series_selected as select * from public.series_selected where symbol='ABNB' and month='2025-12'")
            q.execute(sql.replace('public.','pg_temp.').replace('set search_path=public,pg_temp','set search_path=pg_temp'))
            q.execute("select close,source,evidence_sha256 from pg_temp.v_nominal_monthly where symbol='ABNB'")
            original=q.fetchone()
            assert original and original[1]=='eastmoney_native'
            q.execute('savepoint unreviewed_probe')
            q.execute("update pg_temp.nominal_monthly set close=close*2 where source='sina_daily'")
            assert q.rowcount>0
            q.execute("select close,source,evidence_sha256 from pg_temp.v_nominal_monthly where symbol='ABNB'")
            assert q.fetchone()==original
            q.execute('rollback to savepoint unreviewed_probe');q.execute('release savepoint unreviewed_probe')
            blocked(q,"update pg_temp.nominal_monthly set close=close*2 where source='eastmoney_native'")
            blocked(q,"update pg_temp.nominal_monthly set unit_evidence=null where source='eastmoney_native'")
            blocked(q,"delete from pg_temp.nominal_monthly where source='eastmoney_native'")
            blocked(q,"update pg_temp.sys_event set metrics='{}'::jsonb")
            blocked(q,'delete from pg_temp.sys_event')
            blocked(q,'truncate pg_temp.nominal_monthly')
            blocked(q,'truncate pg_temp.sys_event')
            # A newer, higher-priority unaccepted observation must not switch
            # the already accepted value, even with a unit_evidence string.
            q.execute("""insert into pg_temp.nominal_monthly
                select symbol,month,close*2,trade_date,'alphavantage_native',source_url,fetched_at,
                       repeat('f',64),crosscheck_pairs,median_log_deviation,p90_log_deviation,'probe'
                from pg_temp.nominal_monthly where source='eastmoney_native'""")
            q.execute("select close,source,evidence_sha256 from pg_temp.v_nominal_monthly where symbol='ABNB'")
            assert q.fetchone()==original
            blocked(q,"""insert into pg_temp.sys_event(kind,source,subject,status,metrics)
                values ('validation','verified_closing_fact','ABNB:2025-12-31','pass',
                        jsonb_build_object('proof','{}'::jsonb,'evidence_sha256',repeat('f',64)))""")
            # Unrelated validation logs remain writable.
            q.execute("insert into pg_temp.sys_event(kind,source,subject,status,metrics) values ('validation',null,'other','pass','{}')")
        return {'blocked_mutations':8,'higher_priority_cannot_replace_accepted':True,
                'ordinary_validation_logs_unaffected':True,'unreviewed_rows_not_prematurely_locked':True,
                'temporary_changes_rolled_back':True}
    finally:
        c.rollback();c.close()


def run(apply=False):
    sql=(BASE/'sql/108_accepted_nominal_fact_guards.sql').read_text()
    result={'shadow_probe':shadow_probe(sql),'applied':False}
    if apply:
        c=db_sources.db()
        try:
            with c,c.cursor() as q:
                q.execute("set local lock_timeout='5s'")
                q.execute("set local statement_timeout='30s'")
                q.execute('create temp table before_nominal on commit drop as select * from public.v_nominal_monthly')
                q.execute('select count(*) from before_nominal')
                assert q.fetchone()[0]>0,'Cannot accept an empty nominal snapshot'
                q.execute(sql)
                q.execute("""select count(*) from (
                    (select * from public.v_nominal_monthly except select * from before_nominal)
                    union all (select * from before_nominal except select * from public.v_nominal_monthly)
                    ) changes""")
                assert q.fetchone()[0]==0,'Migration changed selected nominal data'
                q.execute("""select count(distinct subject) from sys_event where kind='validation'
                    and status='pass' and source in ('verified_closing_fact','fund_native_closing_fact')""")
                result.update(applied=True,selected_rows_changed=0,explicitly_accepted_points=q.fetchone()[0])
        finally:c.close()
    name='nominal_acceptance_guard_applied.json' if apply else 'nominal_acceptance_guard_shadow.json'
    (BASE/'output'/name).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true')
    run(parser.parse_args().apply)
