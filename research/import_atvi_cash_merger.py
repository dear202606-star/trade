"""Append the independently reviewed ATVI cash path; default really rolls back."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import db_sources
import holding_paths
from research.audit_atvi_cash_merger import review
from research.apply_recent_index_changes import digest


def run(write=False):
    connection=db_sources.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute("set local statement_timeout='30s'")
            cursor.execute("set local lock_timeout='5s'")
            cursor.execute('lock table holding_event_paths,prices_monthly,nominal_monthly,series_pick,data_lock in share row exclusive mode')
            cursor.execute("select symbol,hold_year,event_date,policy,monthly_values,evidence,evidence_sha256,calculator_sha256,verified from v_holding_event_paths where symbol='ATVI' and hold_year=2023")
            rows=cursor.fetchall()
            if rows:
                if len(rows)!=1:raise RuntimeError('Ambiguous prior path')
                keys=('symbol','hold_year','event_date','policy','monthly_values','evidence','evidence_sha256','calculator_sha256','verified')
                row=dict(zip(keys,rows[0]))
                if not row['verified']:raise RuntimeError('Prior unaccepted path requires explicit version review')
                holding_paths.validate(row)
                connection.rollback();print('ATVI cash path already accepted; no recomputation or collection');return
            preserved={}
            for table,order in [('prices_monthly','symbol,month,source'),('nominal_monthly','symbol,month,source'),
                                ('series_pick','symbol'),('data_lock','id')]:
                cursor.execute('select * from '+table+" where symbol='ATVI' order by "+order)
                preserved[table]=digest(cursor.fetchall())
            cursor.execute('select id,evidence_sha256,calculator_sha256,verified from holding_event_paths order by id')
            prior_paths=cursor.fetchall()
            baseline=hashlib.sha256((BASE/'output/baseline_b.json').read_bytes()).hexdigest()
            evidence=review(cursor)
            row={'symbol':'ATVI','hold_year':2023,'event_date':'2023-10-13','policy':holding_paths.POLICY,
                 'monthly_values':[evidence['retained_monthly_values']['2023-%02d'%m] for m in range(1,13)],
                 'evidence':evidence,'evidence_sha256':holding_paths.evidence_sha(evidence),
                 'calculator_sha256':holding_paths.calculator_sha('atvi_cash_merger')}
            holding_paths.validate(row)
            result={'write':write,'path':row,'preserved_inputs':preserved,'prior_paths_sha256':digest(prior_paths),
                    'baseline_b_sha256':baseline,'price_writes':0,'baseline_writes':0}
            if write:
                prior=json.loads((BASE/'output/atvi_cash_path_rollback.json').read_text())
                if {k:v for k,v in prior.items() if k!='write'}!={k:v for k,v in result.items() if k!='write'}:
                    raise RuntimeError('Accepted candidate differs from rollback review')
            cursor.execute("insert into holding_event_paths(symbol,hold_year,event_date,policy,monthly_values,evidence,evidence_sha256,calculator_sha256,verified) values(%s,%s,%s,%s,%s,%s,%s,%s,true)",
                (row['symbol'],row['hold_year'],row['event_date'],row['policy'],json.dumps(row['monthly_values']),
                 json.dumps(evidence),row['evidence_sha256'],row['calculator_sha256']))
            for table,order in [('prices_monthly','symbol,month,source'),('nominal_monthly','symbol,month,source'),
                                ('series_pick','symbol'),('data_lock','id')]:
                cursor.execute('select * from '+table+" where symbol='ATVI' order by "+order)
                if digest(cursor.fetchall())!=preserved[table]:raise RuntimeError('Underlying inputs changed')
            cursor.execute("select id,evidence_sha256,calculator_sha256,verified from holding_event_paths where not(symbol='ATVI' and hold_year=2023) order by id")
            if cursor.fetchall()!=prior_paths:raise RuntimeError('Existing paths changed')
            if hashlib.sha256((BASE/'output/baseline_b.json').read_bytes()).hexdigest()!=baseline:raise RuntimeError('Old baseline changed')
        if write:connection.commit()
        else:connection.rollback()
        name='atvi_cash_path_applied.json' if write else 'atvi_cash_path_rollback.json'
        (BASE/'output'/name).write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'write':write,'symbol':'ATVI','year':2023,'return':evidence['return_2023'],'price_writes':0}))
    finally:connection.rollback();connection.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--write',action='store_true')
    run(parser.parse_args().write)
