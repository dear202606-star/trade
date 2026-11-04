"""Exclude unaccepted post-conversion quotes; default exercises and rolls back."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import db_sources

FACTS={
    'BCR':{'issuer':'C. R. Bard, Inc.','event_date':'2017-12-29','month_to':'2017-11',
           'cash_per_share':222.93,'successor':'BDX','successor_shares':0.5077,
           'url':'https://investors.bd.com/sec-filings/all-sec-filings/content/0001193125-17-383523/d517273dex991.htm',
           'location':'Exhibit 99.1, completion and consideration paragraphs'},
    'CAM':{'issuer':'Cameron International Corporation','event_date':'2016-04-01','month_to':'2016-03',
           'cash_per_share':14.44,'successor':'SLB','successor_shares':0.716,
           'url':'https://www.sec.gov/Archives/edgar/data/87347/000156459016026174/R12.htm',
           'location':'Note 4: Acquisition of Cameron, effective date and per-share consideration'},
}
SYMBOLS=sorted(FACTS)
EXCLUDED="(symbol='BCR' and month>'2017-11') or (symbol='CAM' and month>'2016-03')"


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()


def originals(cursor):
    result={}
    for table,order in [('prices_monthly','symbol,month,source'),('nominal_monthly','symbol,month,source'),
                        ('series_pick','symbol'),('data_lock','id')]:
        cursor.execute('select * from '+table+' where symbol=any(%s) order by '+order,(SYMBOLS,))
        rows=cursor.fetchall();result[table]={'count':len(rows),'sha256':digest(rows)}
    result['baseline_b_sha256']=hashlib.sha256((BASE/'output/baseline_b.json').read_bytes()).hexdigest()
    return result


def run(write=False):
    connection=db_sources.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute("set local statement_timeout='45s'")
            cursor.execute("set local lock_timeout='5s'")
            cursor.execute('lock table prices_monthly,nominal_monthly,series_pick,security_history_window,series_quarantine,data_lock,holding_event_paths in share row exclusive mode')
            cursor.execute('select symbol,month_to,evidence_url from security_history_window where symbol=any(%s) order by symbol',(SYMBOLS,))
            existing=cursor.fetchall()
            expected=[(s,FACTS[s]['month_to'],FACTS[s]['url']) for s in SYMBOLS]
            if existing:
                if existing!=expected:raise RuntimeError('Conflicting or partial existing boundaries')
                for view in ('series_selected','v_nominal_monthly'):
                    cursor.execute('select count(*) from '+view+' where '+EXCLUDED)
                    if cursor.fetchone()[0]:raise RuntimeError('Existing exclusion is ineffective')
                cursor.execute("select subject,metrics from sys_event where kind='validation' and source='archive_security_identity' and status='pass' and subject=any(%s)",(SYMBOLS,))
                receipts=cursor.fetchall()
                if {s for s,m in receipts}!=set(SYMBOLS) or any(
                        m.get('proof',{}).get('issuer_facts')!=FACTS[s]
                        or m.get('evidence_sha256')!=digest(m['proof']) for s,m in receipts):
                    raise RuntimeError('Existing exclusion has no matching reviewed proof')
                connection.rollback()
                print('Reviewed BCR/CAM boundaries already effective; no reprocessing')
                return
            for query in [
                'select symbol from locked_symbols where symbol=any(%s)',
                'select symbol from series_pick where symbol=any(%s) and frozen',
                "select symbol from data_lock where symbol=any(%s) and status<>'voided'",
                'select symbol from v_holding_event_paths where symbol=any(%s) and verified',
                "select subject from sys_event where kind='validation' and status='pass' and source in ('verified_closing_fact','fund_native_closing_fact') and split_part(subject,':',1)=any(%s)",
            ]:
                cursor.execute(query,(SYMBOLS,))
                if cursor.fetchall():raise RuntimeError('Accepted/frozen history requires explicit version review')
            preserved=originals(cursor)
            for view in ('series_selected','v_nominal_monthly'):
                cursor.execute('create temporary table before_'+view+' on commit drop as select * from '+view)
            cursor.execute((BASE/'sql/112_bcr_cam_merger_boundaries.sql').read_text())
            cursor.execute('select symbol,month_to,evidence_url from security_history_window where symbol=any(%s) order by symbol',(SYMBOLS,))
            if cursor.fetchall()!=expected:raise RuntimeError('Unexpected boundaries')
            changes={}
            for view in ('series_selected','v_nominal_monthly'):
                cursor.execute('''select count(*) from (
                    (select * from before_'''+view+' where not ('+EXCLUDED+''') except select * from '''+view+''')
                    union all (select * from '''+view+' except select * from before_'+view+' where not ('+EXCLUDED+'''))
                    ) differences''')
                if cursor.fetchone()[0]:raise RuntimeError('Unexpected retained/unrelated view change: '+view)
                cursor.execute('select symbol,count(*),min(month),max(month) from before_'+view+' where '+EXCLUDED+' group by symbol order by symbol')
                changes[view]=[{'symbol':s,'count':n,'from':a,'through':b} for s,n,a,b in cursor.fetchall()]
            if originals(cursor)!=preserved:raise RuntimeError('Original observations, choices, locks or baseline changed')
            result={'write':write,'issuer_facts':FACTS,'view_exclusions':changes,'preserved_originals':preserved,
                    'unrelated_or_retained_view_changes':0,'historical_price_writes':0,
                    'consideration_paths_and_prior_history_acceptance':'pending'}
            if write:
                reviewed=json.loads((BASE/'output/bcr_cam_merger_rollback.json').read_text())
                if {k:v for k,v in result.items() if k!='write'}!={k:v for k,v in reviewed.items() if k!='write'}:
                    raise RuntimeError('Rollback review differs from current state')
            for symbol in SYMBOLS:
                proof={'issuer_facts':FACTS[symbol],'view_exclusions':changes,'preserved_originals':preserved}
                cursor.execute("""insert into sys_event(kind,source,subject,status,metrics,finished_at)
                    values('validation','archive_security_identity',%s,'pass',%s,now())""",
                    (symbol,json.dumps({'proof':proof,'evidence_sha256':digest(proof)})))
        if write:connection.commit()
        else:connection.rollback()
        name='bcr_cam_merger_applied.json' if write else 'bcr_cam_merger_rollback.json'
        (BASE/'output'/name).write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'write':write,'view_exclusions':changes,'historical_price_writes':0}))
    finally:
        connection.rollback();connection.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write',action='store_true')
    run(parser.parse_args().write)
