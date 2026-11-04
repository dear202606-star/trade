"""Exclude partial exit-month quotes; record cash terms, not accepted returns."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import db_sources
from research.apply_recent_index_changes import digest

FACTS=[
    {'symbol':'ATVI','event_date':'2023-10-13','month_to':'2023-09','cash_usd_per_share':'95.00',
     'last_trading_date':'2023-10-12','last_observed_quote':94.42,
     'url':'https://www.sec.gov/Archives/edgar/data/718877/000110465923108985/tm2328253d1_8k.htm',
     'location':'Items 2.01 and 3.01; common shares converted to cash, trading halted before opening'},
    {'symbol':'TWTR','event_date':'2022-10-27','month_to':'2022-09','cash_usd_per_share':'54.20',
     'last_trading_date':'2022-10-27','last_observed_quote':53.70,
     'url':'https://www.sec.gov/Archives/edgar/data/1035410/000112329222000109/twitter13da.htm',
     'location':'Item 4; cash conversion October 27, trading suspended before opening October 28'},
]
SYMBOLS=sorted(f['symbol'] for f in FACTS)


def evidence(fact):
    return {'terms':fact,'scope':'Verified cash exit only; no accepted holding return path','holding_valuation_accepted':False}


def preserved(cursor):
    result={}
    for table,order in [('prices_monthly','symbol,month,source'),('nominal_monthly','symbol,month,source'),
                        ('series_pick','symbol'),('data_lock','id'),('holding_event_paths','id')]:
        cursor.execute('select * from '+table+' where symbol=any(%s) order by '+order,(SYMBOLS,))
        result[table]=digest(cursor.fetchall())
    result['baseline_b']=hashlib.sha256((BASE/'output/baseline_b.json').read_bytes()).hexdigest()
    return result


def run(write=False):
    connection=db_sources.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute("set local statement_timeout='45s'")
            cursor.execute("set local lock_timeout='5s'")
            cursor.execute('lock table prices_monthly,nominal_monthly,series_pick,security_history_window,data_lock,holding_event_paths in share row exclusive mode')
            cursor.execute('select symbol,month_to,evidence_url from security_history_window where symbol=any(%s) order by symbol',(SYMBOLS,))
            existing=cursor.fetchall()
            expected=sorted((f['symbol'],f['month_to'],f['url']) for f in FACTS)
            subjects=[f['symbol']+':'+f['event_date'] for f in FACTS]
            cursor.execute("select subject,metrics from sys_event where kind='validation' and source='verified_corporate_terms' and status='pass' and subject=any(%s)",(subjects,))
            receipts=cursor.fetchall()
            if existing or receipts:
                if existing!=expected or len(receipts)!=len(FACTS):raise RuntimeError('Partial or conflicting cash-exit review')
                for fact in FACTS:
                    matches=[m for s,m in receipts if s==fact['symbol']+':'+fact['event_date']]
                    if len(matches)!=1 or matches[0]['proof']!=evidence(fact) or matches[0]['evidence_sha256']!=digest(matches[0]['proof']):
                        raise RuntimeError('Cash-exit review evidence differs')
                for view in ('series_selected','v_nominal_monthly'):
                    for fact in FACTS:
                        cursor.execute('select count(*) from '+view+' where symbol=%s and month>%s',(fact['symbol'],fact['month_to']))
                        if cursor.fetchone()[0]:raise RuntimeError('Cash-exit exclusion ineffective')
                connection.rollback();print('Cash-exit boundaries already reviewed; no reprocessing');return
            cursor.execute('select symbol from locked_symbols where symbol=any(%s) union select symbol from series_pick where symbol=any(%s) and frozen',(SYMBOLS,SYMBOLS))
            if cursor.fetchall():raise RuntimeError('Frozen security requires explicit version review')
            cursor.execute("select id from data_lock where symbol=any(%s) and status<>'voided'",(SYMBOLS,))
            if cursor.fetchall():raise RuntimeError('Locked history requires explicit version review')
            cursor.execute("select subject from sys_event where kind='validation' and status='pass' and source in ('verified_closing_fact','fund_native_closing_fact') and split_part(subject,':',1)=any(%s)",(SYMBOLS,))
            if cursor.fetchall():raise RuntimeError('Accepted closing fact requires explicit version review')
            before=preserved(cursor)
            changes={}
            for view in ('series_selected','v_nominal_monthly'):
                cursor.execute('create temporary table before_'+view+' on commit drop as select * from '+view)
            for fact in FACTS:
                cursor.execute('insert into security_history_window(symbol,month_to,evidence_url,reason) values(%s,%s,%s,%s)',
                    (fact['symbol'],fact['month_to'],fact['url'],'Cash conversion '+fact['event_date']+'; partial month quote is not month-end common-stock value; cash holding path still unaccepted'))
                proof=evidence(fact)
                cursor.execute("insert into sys_event(kind,source,subject,status,metrics,finished_at) values('validation','verified_corporate_terms',%s,'pass',%s,now())",
                    (fact['symbol']+':'+fact['event_date'],json.dumps({'proof':proof,'evidence_sha256':digest(proof)})))
            predicate="(symbol='ATVI' and month>'2023-09') or (symbol='TWTR' and month>'2022-09')"
            for view in ('series_selected','v_nominal_monthly'):
                cursor.execute('select symbol,month from before_'+view+' where '+predicate+' order by symbol,month')
                changes[view]=cursor.fetchall()
                cursor.execute('select count(*) from ((select * from before_'+view+' where not ('+predicate+') except select * from '+view+') union all (select * from '+view+' except select * from before_'+view+' where not ('+predicate+'))) differences')
                if cursor.fetchone()[0]:raise RuntimeError('Unexpected retained/unrelated view changes')
            if preserved(cursor)!=before:raise RuntimeError('Original inputs changed')
            result={'write':write,'facts':FACTS,'excluded_view_rows':changes,'preserved_sha256':before,
                    'retained_and_unrelated_view_rows_changed':0,'raw_price_writes':0,'holding_valuation_accepted':False}
            if write:
                prior=json.loads((BASE/'output/atvi_twtr_cash_exits_rollback.json').read_text())
                if {k:v for k,v in prior.items() if k!='write'}!=json.loads(json.dumps({k:v for k,v in result.items() if k!='write'},default=str)):
                    raise RuntimeError('Review differs from rollback receipt')
        if write:connection.commit()
        else:connection.rollback()
        name='atvi_twtr_cash_exits_applied.json' if write else 'atvi_twtr_cash_exits_rollback.json'
        (BASE/'output'/name).write_text(json.dumps(result,indent=2,default=str)+'\n')
        print(json.dumps({'write':write,'excluded_view_rows':changes,'raw_price_writes':0},default=str))
    finally:connection.rollback();connection.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--write',action='store_true')
    run(parser.parse_args().write)
