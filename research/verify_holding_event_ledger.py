"""Real DB/runtime and rollback checks for actual-cash/successor holdings."""
import hashlib
import json
import sys
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import psycopg2.extras
import db_select
import holding_paths
import strategy_history
import variant_b


def run():
    baseline=(BASE/'output/baseline_b.json').read_bytes()
    runtime=[]
    for symbol,year,expected in [('LB',2021,1.3881764890763368),
                                 ('BBWI',2021,1.3881764890763368),
                                 ('CVC',2016,.09404388714733547),
                                 ('WDC',2025,3.2267010212216665)]:
        values=holding_paths.values(symbol,year,lambda _:None)
        b=variant_b.year_perf([{'ticker':symbol,'weight':1}],year)
        a=strategy_history.year_perf(year,[{'ticker':symbol,'weight':1}])
        if len(values)!=12 or abs(b[0]-expected)>1e-12 or abs(a[0]-expected)>.00005:
            raise RuntimeError('Real holding-event runtime disagrees: '+symbol)
        base='%d-12'%(year-1);end='%d-12'%year
        momentum=holding_paths.momentum_bars(symbol,{},base,end)
        ranked_return=momentum[end][0]/momentum[base][0]-1
        if abs(ranked_return-expected)>1e-12:
            raise RuntimeError('Ranking and held returns disagree: '+symbol)
        runtime.append({'symbol':symbol,'year':year,'variant_b_return':b[0],
                        'variant_a_return':a[0],'ranking_return':ranked_return,
                        'monthly_values':values})
    connection=db_select.db();connection.autocommit=False
    rejected=0
    try:
        with connection.cursor() as cursor:
            cursor.execute("select id from v_holding_event_paths where symbol='LB' and hold_year=2021")
            target=cursor.fetchone()[0]
            for sql,args in [('update holding_event_paths set verified=false where id=%s',(target,)),
                             ('delete from holding_event_paths where id=%s',(target,)),
                             ('truncate holding_event_paths',None)]:
                cursor.execute('savepoint immutable_probe')
                try:cursor.execute(sql,args)
                except Exception as exc:
                    if getattr(exc,'pgcode',None)!='P0001':raise
                    rejected+=1;cursor.execute('rollback to savepoint immutable_probe')
                else:raise AssertionError('append-only evidence could be altered')
            cursor.execute('''select symbol,hold_year,event_date,policy,monthly_values,evidence,calculator_sha256
                              from holding_event_paths where id=%s''',(target,))
            symbol,year,date,policy,values,evidence,calculator=cursor.fetchone()
            evidence=dict(evidence,review_status='rollback-only unverified review probe')
            cursor.execute('''insert into holding_event_paths
                (symbol,hold_year,event_date,policy,monthly_values,evidence,evidence_sha256,calculator_sha256,verified)
                values (%s,%s,%s,%s,%s,%s,%s,%s,false)''',
                (symbol,year,date,policy,psycopg2.extras.Json(values),psycopg2.extras.Json(evidence),
                 holding_paths.evidence_sha(evidence),calculator))
            cursor.execute('select verified from v_holding_event_paths where symbol=%s and hold_year=%s',(symbol,year))
            if cursor.fetchone()!=(False,):raise AssertionError('latest unverified review fell back to old approval')
        connection.rollback()
        with connection.cursor() as cursor:
            cursor.execute('select id,verified from v_holding_event_paths where symbol=%s and hold_year=%s',(symbol,year))
            if cursor.fetchone()!=(target,True):raise AssertionError('probe changes were not rolled back')
    finally:
        connection.rollback();connection.close()
    if baseline!=(BASE/'output/baseline_b.json').read_bytes():raise RuntimeError('published baseline changed')
    result={'read_from':'v_holding_event_paths','verified_paths':runtime,
            'holding_event_input_sha':holding_paths.input_sha(),'append_only_rejections':rejected,
            'latest_unverified_review_blocks_old_approval':True,'all_probe_changes_rolled_back':True,
            'baseline_b_sha256':hashlib.sha256(baseline).hexdigest(),'published_outputs_changed':False}
    (BASE/'output/holding_runtime_verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS: 4 real A/B/ranking event paths; 3 immutable guards; latest-review block; all probes rolled back')


if __name__=='__main__':run()
