"""Default: exercise exact identity exclusions and roll back. --write commits."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import db_sources

SYMBOLS = ['DTV','LSI']
FACTS = {
    'LSI': {'original_cik':'703360','original_issuer':'LSI Corporation',
        'original_2013_q4_price_range':[7.39,11.04],
        'price_range_url':'https://www.sec.gov/Archives/edgar/data/703360/000119312514082531/d649519dprer14a.htm',
        'price_range_location':'Market Price Data and Dividend Information, 2013 Fourth Quarter',
        'other_issuer_cik':'944314','other_issuer':'Sovran Self Storage / Life Storage',
        'other_issuer_reported_2013_year_end_price':65.17,
        'other_price_url':'https://www.sec.gov/Archives/edgar/data/944314/000115752314000793/a50811660ex99_1.htm',
        'other_price_location':'Fourth quarter 2013 report, debt to enterprise value per-share price',
        'reuse_effective_date':'2016-08-15',
        'reuse_url':'https://www.sec.gov/Archives/edgar/data/944314/000119312516681926/d243423dex991.htm',
        'original_exit_date':'2014-05-06','cash_per_share':11.15,
        'exit_url':'https://www.sec.gov/Archives/edgar/data/1441634/000144163414000045/R21.htm',
        'action':'Quarantine the entire current series, including dates before original exit; cap future reviewed original history at 2014-04'},
    'DTV': {'original_cik':'1465112','original_issuer':'DIRECTV',
        'original_exit_date':'2015-07-24','cash_per_share':28.50,
        'successor_symbol':'T','successor_shares_per_share':1.892,
        'exit_url':'https://www.sec.gov/Archives/edgar/data/1465112/000104746915006714/a2225634z10-q.htm',
        'exit_location':'Note 12: Subsequent Events',
        'action':'Exclude original-ticker month ends after 2015-06; actual consideration path still requires acceptance'},
}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',', ':'),default=str).encode()).hexdigest()


def run(write=False):
    connection = db_sources.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute("set local statement_timeout='45s'")
            cursor.execute("set local lock_timeout='5s'")
            cursor.execute('lock table prices_monthly,nominal_monthly,series_pick,security_history_window,series_quarantine,data_lock in share row exclusive mode')
            cursor.execute('select symbol,month_to from security_history_window where symbol=any(%s) order by symbol',(SYMBOLS,))
            windows = cursor.fetchall()
            cursor.execute("select reason from series_quarantine where symbol='LSI'")
            quarantine = cursor.fetchone()
            if windows or quarantine:
                if windows != [('DTV','2015-06'),('LSI','2014-04')] or not quarantine:
                    raise RuntimeError('Existing identity boundary conflicts; no overwrite')
                cursor.execute("select count(*) from series_selected where symbol='LSI' or (symbol='DTV' and month>'2015-06')")
                if cursor.fetchone()[0]:
                    raise RuntimeError('Existing identity boundary not effective')
                cursor.execute("""select subject,metrics from sys_event where kind='validation'
                    and source='archive_security_identity' and status='pass' and subject=any(%s)""",(SYMBOLS,))
                receipts = cursor.fetchall()
                if {s for s,m in receipts} != set(SYMBOLS) or any(
                        m.get('proof',{}).get('issuer_facts') != FACTS[s]
                        or m.get('evidence_sha256') != digest(m['proof']) for s,m in receipts):
                    raise RuntimeError('Existing exclusion lacks reviewed identity proof')
                connection.rollback()
                print('Reviewed identity boundaries already effective; no reprocessing')
                return
            cursor.execute('select symbol from locked_symbols where symbol=any(%s)',(SYMBOLS,))
            if cursor.fetchall():raise RuntimeError('Protected symbol needs explicit version review')
            cursor.execute('select symbol from series_pick where symbol=any(%s) and frozen',(SYMBOLS,))
            if cursor.fetchall():raise RuntimeError('Frozen choice needs explicit version review')
            cursor.execute("select id from data_lock where symbol=any(%s) and status<>'voided'",(SYMBOLS,))
            if cursor.fetchall():raise RuntimeError('Accepted history needs explicit version review')
            cursor.execute("""select subject from sys_event where kind='validation' and status='pass'
                and source in ('verified_closing_fact','fund_native_closing_fact')
                and split_part(subject,':',1)=any(%s)""",(SYMBOLS,))
            if cursor.fetchall():raise RuntimeError('Accepted nominal fact needs explicit version review')
            cursor.execute("select adj_close,raw_close,source,basis from series_selected where symbol='LSI' and month='2013-12'")
            sample = cursor.fetchone()
            if not sample or tuple(map(float,sample[:2])) != (65.17,65.17) or sample[2:] != ('sina_repaired','price_only'):
                raise RuntimeError('Reviewed contamination sample changed')
            preserved = {}
            for table,order in (('prices_monthly','symbol,month,source'),
                                ('nominal_monthly','symbol,month,source'),
                                ('series_pick','symbol'),('data_lock','id')):
                cursor.execute('select * from '+table+' where symbol=any(%s) order by '+order,(SYMBOLS,))
                preserved[table] = digest(cursor.fetchall())
            baseline = hashlib.sha256((BASE/'output/baseline_b.json').read_bytes()).hexdigest()
            for view in ('series_selected','v_nominal_monthly'):
                cursor.execute('create temporary table before_'+view+' on commit drop as select * from '+view)
            cursor.execute((BASE/'sql/109_lsi_dtv_identity_boundaries.sql').read_text())
            cursor.execute('select symbol,month_to from security_history_window where symbol=any(%s) order by symbol',(SYMBOLS,))
            if cursor.fetchall() != [('DTV','2015-06'),('LSI','2014-04')]:
                raise RuntimeError('Unexpected identity windows')
            changes = {}
            excluded = "symbol='LSI' or (symbol='DTV' and month>'2015-06')"
            for view in ('series_selected','v_nominal_monthly'):
                # All retained values and all unrelated securities must match,
                # in both directions, on every exposed column.
                cursor.execute('''select count(*) from (
                    (select * from before_'''+view+' where not ('+excluded+''') except select * from '''+view+''')
                    union all (select * from '''+view+' except select * from before_'+view+' where not ('+excluded+'''))
                    ) differences''')
                if cursor.fetchone()[0]:raise RuntimeError('Unexpected view changes: '+view)
                cursor.execute('select symbol,count(*),min(month),max(month) from before_'+view+' where '+excluded+' group by symbol order by symbol')
                changes[view] = [{'symbol':s,'removed_rows':n,'from':a,'through':b} for s,n,a,b in cursor.fetchall()]
            for table,order in (('prices_monthly','symbol,month,source'),
                                ('nominal_monthly','symbol,month,source'),
                                ('series_pick','symbol'),('data_lock','id')):
                cursor.execute('select * from '+table+' where symbol=any(%s) order by '+order,(SYMBOLS,))
                if digest(cursor.fetchall()) != preserved[table]:raise RuntimeError('Original rows changed: '+table)
            if hashlib.sha256((BASE/'output/baseline_b.json').read_bytes()).hexdigest() != baseline:
                raise RuntimeError('Published baseline changed')
            result = {'write':write,'facts':FACTS,'view_exclusions':changes,
                      'preserved_physical_sha256':preserved,'baseline_b_sha256':baseline,
                      'physical_observations_picks_locks_and_baseline_changed':False,
                      'unrelated_or_retained_view_rows_changed':0,
                      'strategy_rebuild_and_missing_original_history_acceptance':'pending'}
            if write:
                reviewed = json.loads((BASE/'output/lsi_dtv_identity_rollback.json').read_text())
                if {k:v for k,v in result.items() if k!='write'} != {k:v for k,v in reviewed.items() if k!='write'}:
                    raise RuntimeError('Rollback evidence differs; review again before applying')
            for symbol in SYMBOLS:
                proof = {'issuer_facts':FACTS[symbol],'exclusions':changes,
                         'preserved_physical_sha256':preserved,'baseline_b_sha256':baseline}
                cursor.execute("""insert into sys_event(kind,source,subject,status,metrics,finished_at)
                    values ('validation','archive_security_identity',%s,'pass',%s,now())""",
                    (symbol,json.dumps({'proof':proof,'evidence_sha256':digest(proof)})))
        if write:connection.commit()
        else:connection.rollback()
        filename = 'lsi_dtv_identity_applied.json' if write else 'lsi_dtv_identity_rollback.json'
        (BASE/'output'/filename).write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'write':write,'view_exclusions':changes,'unrelated_or_retained_view_rows_changed':0}))
    finally:
        connection.rollback();connection.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write',action='store_true')
    run(parser.parse_args().write)
