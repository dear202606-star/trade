"""Bound the WIKI dividend gap against actual online holding inputs, read-only."""
import hashlib
import json
import math
import sys
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import db_sources
import holding_paths

NO_DIVIDENDS='https://www.sec.gov/Archives/edgar/data/1702780/000104746918003930/R41.htm'


def check_cvc(evidence,values,archive):
    bars=archive['monthly']['CVC']
    if hashlib.sha256(json.dumps(bars,sort_keys=True).encode()).hexdigest()!=evidence['monthly_evidence_sha256']:
        raise ValueError('Cached CVC monthly evidence differs from its accepted version')
    if (evidence['hold_year']!=2016 or evidence['native_base']!=31.90
        or evidence['cash_per_old_share']!=34.90 or evidence['archive_sha256']!=archive['archive_sha256']):
        raise ValueError('CVC original-equity cash evidence differs')
    actions=[r for r in archive['events'].get('CVC',[])
             if '2015-12-31'<r['date']<='2016-06-20' and (r['dividend'] or r['split_ratio']!=1)]
    if actions:raise ValueError('CVC actions contradict issuer no-dividend evidence')
    if len(values)!=12:raise ValueError('Incomplete CVC holding year')
    for month,value in enumerate(values,1):
        row=bars['2016-%02d'%month] if month<6 else None
        if row and row['adj_close']!=row['close']:raise ValueError('CVC unexplained share/cash adjustment')
        expected=(row['close'] if row else 34.90)/31.90
        if not math.isfinite(value) or abs(value-expected)>1e-12:
            raise ValueError('CVC path differs from independent no-dividend cash calculation')
    return {'issuer_no_dividend_source':NO_DIVIDENDS,'issuer_period':'2016-01-01 through 2016-06-20',
            'verified_months':12,'cash_conversion_from_month':'2016-06','cash_per_original_share':34.90}


def run():
    connection=db_sources.db()
    try:
        connection.set_session(isolation_level='REPEATABLE READ',readonly=True)
        with connection.cursor() as cursor:
            cursor.execute("select count(*) from series_selected where source='quandl_wiki'")
            selected_returns=cursor.fetchone()[0]
            if selected_returns:raise RuntimeError('Additional WIKI adjusted-return users need review')
            cursor.execute("select count(*) from v_nominal_monthly where source='quandl_wiki'")
            nominal=cursor.fetchone()[0]
            cursor.execute('select symbol,hold_year,event_date,policy,monthly_values,evidence,evidence_sha256,calculator_sha256,verified from v_holding_event_paths order by symbol,hold_year')
            rows=cursor.fetchall()
    finally:connection.close()
    archive=json.loads((BASE/'output/wiki_monthly_archive.json').read_text());paths=[];cash_review=None
    keys=('symbol','hold_year','event_date','policy','monthly_values','evidence','evidence_sha256','calculator_sha256','verified')
    for values in rows:
        row=dict(zip(keys,values))
        if not row['verified']:continue
        holding_paths.validate(row);producer=row['evidence']['producer']
        if producer=='cvc_cash_merger':
            cash_review=check_cvc(row['evidence'],row['monthly_values'],archive)
            use='WIKI actual 2016 return input; issuer confirms no ordinary dividends before cash exit'
        elif producer=='lb_distribution':use='WIKI earlier unit/identity crosscheck only; 2021 holdings use the separate 2022 Yahoo vintage and VSCO inputs'
        elif producer in ('atvi_cash_merger','twtr_cash_merger','wdc_distribution','tt_ir_distribution'):
            use='No WIKI holding-return input'
        else:raise RuntimeError('Unclassified accepted holding producer: '+producer)
        paths.append({'symbol':row['symbol'],'year':row['hold_year'],'producer':producer,
                      'evidence_sha256':row['evidence_sha256'],'calculator_sha256':row['calculator_sha256'],'wiki_usage':use})
    if cash_review is None:raise RuntimeError('Expected CVC accepted path missing')
    result={'read_only':True,'selected_WIKI_return_rows':selected_returns,
            'selected_WIKI_raw_nominal_observations_not_acceptance_count':nominal,
            'accepted_holding_paths_checked':paths,'CVC_independent_cash_review':cash_review,
            '2017_dividend_gap_enters_current_accepted_holding_values':False,
            'raw_close_and_full_return_acceptance_are_separate':True,'full_history_accepted':False}
    (BASE/'output/wiki_holding_dependency_audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'accepted_paths_checked':len(paths),'WIKI_return_rows':selected_returns,
                      'CVC_cash_months_verified':cash_review['verified_months'],'financial_writes':0}))


if __name__=='__main__':run()
