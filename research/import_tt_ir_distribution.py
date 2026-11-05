"""Append one TT/IR ownership path; default is a real rollback, never a relock."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import db_sources
import db_prices
import holding_paths
from psycopg2.extras import Json
from research.audit_tt_ir_distribution import review


def preserved(cursor):
    result={'selected':list(db_prices.selected_stamp(cursor))}
    for name,query,params in [
        ('prices','select * from prices_monthly where symbol=any(%s) order by symbol,month,source',(['TT','IR'],)),
        ('nominal','select * from nominal_monthly where symbol=any(%s) order by symbol,month,source',(['TT','IR'],)),
        ('picks','select * from series_pick where symbol=any(%s) order by symbol',(['TT','IR'],)),
        ('members','select * from members where symbol=any(%s) order by symbol,start_date',(['TT','IR'],)),
        ('locks','select * from data_lock where symbol=any(%s) order by id',(['TT','IR'],)),
        ('other_paths',"select * from v_holding_event_paths where not(symbol='TT' and hold_year=2020) order by symbol,hold_year",())]:
        cursor.execute(query,params)
        result[name]=hashlib.sha256(json.dumps(cursor.fetchall(),default=str).encode()).hexdigest()
    result['baseline_b']=hashlib.sha256((BASE/'output/baseline_b.json').read_bytes()).hexdigest()
    return result


def run(write=False):
    connection=db_sources.db()
    try:
        connection.set_session(isolation_level='REPEATABLE READ')
        with connection.cursor() as cursor:
            cursor.execute("set local lock_timeout='5s'")
            cursor.execute("select pg_advisory_xact_lock(hashtext('holding_path:TT:2020')::bigint)")
            cursor.execute("select symbol,hold_year,event_date,policy,monthly_values,evidence,evidence_sha256,calculator_sha256,verified from v_holding_event_paths where symbol='TT' and hold_year=2020")
            existing=cursor.fetchall()
            if existing:
                if len(existing)!=1 or not existing[0][-1]:raise RuntimeError('Unreviewed/conflicting TT path exists')
                keys=('symbol','hold_year','event_date','policy','monthly_values','evidence','evidence_sha256','calculator_sha256','verified')
                holding_paths.validate(dict(zip(keys,existing[0])))
                connection.rollback();print('TT 2020 ownership path already accepted; no quote or document processing');return
            cursor.execute('select symbol from series_pick where symbol=any(%s) order by symbol for share',(['IR','TT'],))
            if [r[0] for r in cursor.fetchall()]!=['IR','TT']:raise RuntimeError('Required source choices missing')
            before=preserved(cursor)
            cursor.execute("select metrics from sys_event where source='historical_identity_review' and subject='IR:lineage-v1' and status='pass'")
            identity=cursor.fetchall()
            if len(identity)!=1:raise RuntimeError('Original/new IR identity evidence missing')
            metrics=identity[0][0];fact=metrics['proof']
            if (holding_paths.evidence_sha(fact)!=metrics['evidence_sha256']
                or fact['historical_index_issuer_cik']!='1466258' or fact['current_issuer_cik']!='1699150'):
                raise RuntimeError('Original/new IR identity proof differs')
            evidence=review();evidence['historical_identity_review_sha256']=metrics['evidence_sha256']
            digest=holding_paths.evidence_sha(evidence);calculator=holding_paths.calculator_sha('tt_ir_distribution')
            row={'symbol':'TT','hold_year':2020,'event_date':'2020-02-29','policy':evidence['holding_event_policy'],
                 'monthly_values':[evidence['retained_monthly_values']['2020-%02d'%m] for m in range(1,13)],
                 'evidence':evidence,'evidence_sha256':digest,'calculator_sha256':calculator}
            holding_paths.validate(row)
            if write:
                prior=json.loads((BASE/'output/tt_ir_2020_rollback.json').read_text())
                if (prior['evidence_sha256']!=digest or prior['calculator_sha256']!=calculator
                    or prior['preserved_sha256']!=before):raise RuntimeError('TT ownership review differs from rollback evidence')
            cursor.execute("""insert into holding_event_paths(symbol,hold_year,event_date,policy,monthly_values,
                evidence,evidence_sha256,calculator_sha256,verified) values('TT',2020,'2020-02-29',%s,%s,%s,%s,%s,true)""",
                (row['policy'],Json(row['monthly_values']),Json(evidence),digest,calculator))
            blocked=[]
            for name,sql in [
                ('value_update',"update holding_event_paths set monthly_values=jsonb_set(monthly_values,'{11}','99'::jsonb) where symbol='TT' and hold_year=2020"),
                ('evidence_update',"update holding_event_paths set evidence=evidence||'{\"probe\":true}'::jsonb where symbol='TT' and hold_year=2020"),
                ('identity_update',"update holding_event_paths set symbol='IR' where symbol='TT' and hold_year=2020"),
                ('path_delete',"delete from holding_event_paths where symbol='TT' and hold_year=2020")]:
                cursor.execute('savepoint tt_path_probe')
                try:cursor.execute(sql)
                except db_sources.psycopg2.Error as error:
                    if 'append-only' not in str(error):raise
                    blocked.append(name)
                else:raise RuntimeError('Accepted TT ownership mutation was not blocked: '+name)
                finally:cursor.execute('rollback to savepoint tt_path_probe')
            if preserved(cursor)!=before:raise RuntimeError('Original prices, membership, locks, paths or baseline changed')
        if write:connection.commit()
        else:connection.rollback()
        result={'write':write,'symbol':'TT','hold_year':2020,'evidence_sha256':digest,
                'calculator_sha256':calculator,'preserved_sha256':before,'blocked_mutations':blocked,
                'monthly_values':row['monthly_values'],'retained_return':row['monthly_values'][-1]-1,
                'new_IR_path_created':False,'membership_accepted':False,'full_strategy_accepted':False}
        name='tt_ir_2020_applied.json' if write else 'tt_ir_2020_rollback.json'
        (BASE/'output'/name).write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'write':write,'symbol':'TT','year':2020,'return':result['retained_return'],
                          'blocked_mutations':len(blocked),'original_data_changed':False}))
    finally:connection.rollback();connection.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--write',action='store_true')
    run(parser.parse_args().write)
