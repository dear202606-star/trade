"""Verify live window reads, independent raw daily sums, and rollback guards."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import psycopg2.extras

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import db_select
import liquidity_prices as liquidity


def run():
    baseline=(BASE/'output/baseline_b.json').read_bytes()
    liquidity._WINDOWS=None
    windows=liquidity.load_all();verified=[]
    if not windows:raise RuntimeError('No live liquidity evidence')
    for (symbol,base,rank),row in sorted(windows.items()):
        if row is None:raise RuntimeError('Unverified latest window: '+symbol)
        proof=row['evidence']['sources']['primary_payload_sha256'];matching=[]
        for path in (BASE/'output/_liquidity_windows').glob(symbol+'-*.json'):
            raw=json.loads(path.read_text())
            if liquidity.evidence_sha(raw['payload'])==proof:matching.append(raw)
        if len(matching)!=1:raise RuntimeError('Missing/ambiguous raw daily evidence: '+symbol)
        start,end=liquidity.window_dates(base,rank)
        daily=[line.split(',') for line in matching[0]['payload']['data']['klines']
               if start<=line.split(',')[0]<=end]
        total=sum(float(fields[6]) for fields in daily)
        average=liquidity.average_daily(symbol,base,rank,20e6)
        if len(daily)!=row['trading_days'] or abs(total/len(daily)-average)>max(1e-6,abs(average)*1e-12):
            raise RuntimeError('Runtime differs from independently summed daily amounts')
        verified.append({'symbol':symbol,'base_month':base,'rank_month':rank,
                         'days':len(daily),'average_daily_dollars':average})
    direct_sha=liquidity.input_sha()
    liquidity._WINDOWS=None
    with patch.object(db_select,'db',side_effect=RuntimeError('forced direct-read failure probe')):
        fallback=liquidity.load_all()
        if fallback!=windows or liquidity.input_sha()!=direct_sha:
            raise RuntimeError('REST differs from exact direct-read evidence')
    connection=db_select.db();connection.autocommit=False
    rejected=0
    try:
        with connection.cursor() as cursor:
            cursor.execute('set local extra_float_digits=3')
            cursor.execute('select id from v_liquidity_windows order by symbol,rank_month limit 1')
            target=cursor.fetchone()[0]
            for sql,args in [('update liquidity_windows set verified=false where id=%s',(target,)),
                             ('delete from liquidity_windows where id=%s',(target,)),
                             ('truncate liquidity_windows',None)]:
                cursor.execute('savepoint immutable_probe')
                try:cursor.execute(sql,args)
                except Exception as exc:
                    if getattr(exc,'pgcode',None)!='P0001':raise
                    rejected+=1;cursor.execute('rollback to savepoint immutable_probe')
                else:raise AssertionError('Liquidity evidence could be changed')
            cursor.execute('''select symbol,base_month,rank_month,trading_days,primary_amount,
                 independent_lower,independent_upper,calculator_sha256,evidence
                 from liquidity_windows where id=%s''',(target,))
            symbol,base,rank,days,amount,low,high,calculator,evidence=cursor.fetchone()
            changed=dict(evidence,review_status='rollback-only inconsistent totals probe')
            cursor.execute('savepoint totals_probe')
            try:
                cursor.execute('''insert into liquidity_windows
                 (symbol,base_month,rank_month,trading_days,primary_amount,independent_lower,independent_upper,
                  calculator_sha256,evidence,evidence_sha256,verified)
                 values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true)''',
                 (symbol,base,rank,days,amount+1,low,high,calculator,psycopg2.extras.Json(changed),liquidity.evidence_sha(changed)))
            except Exception as exc:
                if getattr(exc,'pgcode',None)!='23514':raise
                cursor.execute('rollback to savepoint totals_probe')
            else:raise AssertionError('Inconsistent amount/evidence was accepted')
            evidence=dict(evidence,review_status='rollback-only pending verification')
            cursor.execute('''insert into liquidity_windows
             (symbol,base_month,rank_month,trading_days,primary_amount,independent_lower,independent_upper,
              calculator_sha256,evidence,evidence_sha256,verified)
             values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,false)''',
             (symbol,base,rank,days,amount,low,high,calculator,psycopg2.extras.Json(evidence),liquidity.evidence_sha(evidence)))
            cursor.execute('select verified from v_liquidity_windows where symbol=%s and base_month=%s and rank_month=%s',
                           (symbol,base,rank))
            if cursor.fetchone()!=(False,):raise AssertionError('Pending version fell back to old approval')
        connection.rollback()
        with connection.cursor() as cursor:
            cursor.execute('select id,verified from v_liquidity_windows where symbol=%s and base_month=%s and rank_month=%s',
                           (symbol,base,rank))
            if cursor.fetchone()!=(target,True):raise AssertionError('Probe changes remain')
    finally:connection.rollback();connection.close()
    if baseline!=(BASE/'output/baseline_b.json').read_bytes():raise RuntimeError('Published baseline changed')
    result={'policy':liquidity.POLICY,'windows':verified,'input_sha':liquidity.input_sha(),
            'immutable_guard_rejections':rejected,'pending_version_blocks_old_approval':True,
            'inconsistent_totals_rejected':True,
            'forced_rest_fallback_exact_match':True,
            'all_probe_changes_rolled_back':True,'published_outputs_changed':False}
    (BASE/'output/liquidity_runtime_verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS: %d real runtime windows; independent daily sums; 3 immutable guards; pending version; rolled back'%len(verified))


if __name__=='__main__':run()
