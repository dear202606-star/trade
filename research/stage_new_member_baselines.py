"""Archive missing-member source observations online, without approving returns."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import db_sources
from collect_liquidity_windows import SINA,daily_records
from research.apply_recent_index_changes import digest

FACTS=[
    {'symbol':'BE','date':'2025-12-31','close':86.89,'cik':'1664703','share_class':'Class A common stock',
     'url':'https://www.sec.gov/Archives/edgar/data/1664703/000162828026024237/be-20260408.htm',
     'location':'Outstanding Equity Awards, footnote 2; Potential Payments, Calculation of Potential Payments'},
    {'symbol':'TWLO','date':'2025-12-31','close':142.24,'cik':'1447669','share_class':'Class A common stock',
     'url':'https://www.sec.gov/Archives/edgar/data/1447669/000114036126017444/ny20050676x1_def14a.htm',
     'location':'Outstanding Equity Awards, footnote 4, proxy page 77'},
]
SOURCE='new_member_baseline_review'


def review(fact,record):
    if record.get('endpoint')!=SINA+fact['symbol']:
        raise ValueError('Daily source identity mismatch')
    daily=daily_records(record['rows'],'sina','2025-01-01','2025-12-31')
    if daily.get(fact['date'],{}).get('close')!=fact['close']:
        raise ValueError('Issuer/date/source close mismatch')
    if len(daily)<24:raise ValueError('Insufficient source observations')
    return {'issuer_fact':fact,'daily_url':record['endpoint'],'observed_daily_2025':daily,
            'scope':'Issuer disclosure and one feed match; selected return history and second feed remain unaccepted',
            'price_selection_accepted':False,'nominal_fact_accepted':False}


def run(write=False):
    connection=db_sources.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute('select pg_advisory_xact_lock(2026,1231)')
            subjects=[f['symbol']+':'+f['date'] for f in FACTS]
            cursor.execute("select subject,metrics from sys_event where kind='validation' and source=%s and subject=any(%s)",(SOURCE,subjects))
            existing=cursor.fetchall()
            if existing:
                if len(existing)!=len(FACTS) or {s for s,m in existing}!=set(subjects):
                    raise RuntimeError('Partial prior observation review; no overwrite')
                for subject,metrics in existing:
                    proof=metrics['proof'];fact=next(f for f in FACTS if subject==f['symbol']+':'+f['date'])
                    record={'endpoint':proof['daily_url'],'rows':[
                        {'d':d,**{key:v[name] for key,name in [('o','open'),('c','close'),('h','high'),('l','low'),('v','volume')]}}
                        for d,v in proof['observed_daily_2025'].items()]}
                    if proof!=review(fact,record) or metrics['evidence_sha256']!=digest(proof):
                        raise RuntimeError('Existing source review conflicts')
                connection.rollback();print('Online observations already archived; no source request or reprocessing');return
            records=[]
            for fact in FACTS:
                raw=(BASE/'output/_nominal_daily'/(fact['symbol']+'.json')).read_bytes()
                proof=review(fact,json.loads(raw))
                metrics={'proof':proof,'evidence_sha256':digest(proof),'source_archive_sha256':hashlib.sha256(raw).hexdigest()}
                cursor.execute("insert into sys_event(kind,source,subject,status,metrics,finished_at) values('validation',%s,%s,'warn',%s,now())",
                    (SOURCE,fact['symbol']+':'+fact['date'],json.dumps(metrics)))
                records.append({'subject':fact['symbol']+':'+fact['date'],'evidence_sha256':digest(proof),'observations':len(proof['observed_daily_2025'])})
        result={'write':write,'records':records,'database_price_writes':0,'price_selection_accepted':False}
        if write:
            previous=json.loads((BASE/'output/new_member_baselines_rollback.json').read_text())
            if {k:v for k,v in previous.items() if k!='write'}!={k:v for k,v in result.items() if k!='write'}:
                raise RuntimeError('Source review changed after rollback')
            connection.commit()
        else:connection.rollback()
        (BASE/'output'/('new_member_baselines_applied.json' if write else 'new_member_baselines_rollback.json')).write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result))
    finally:connection.rollback();connection.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--write',action='store_true')
    run(parser.parse_args().write)
