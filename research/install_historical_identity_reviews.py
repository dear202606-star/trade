"""Record original/current equity distinctions; default rolls back all changes."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import db_sources
from historical_lineage import digest

FACTS = {
    'IR': {'symbol':'IR','selected_source':'yahoo_relay','comparison_from':'2020-03',
        'current_issuer_cik':'1699150','historical_index_issuer_cik':'1466258',
        'current_equity_predecessor':'Gardner Denver Holdings, Inc. (GDI)',
        'historical_index_equity_successor':'Trane Technologies plc (TT)',
        'new_ir_index_admission':'2020-03-03','old_ir_tt_continuity_unresolved':True,
        'full_history_accepted':False,
        'evidence_urls':[
            'https://www.spglobal.com/spdji/en/documents/indexnews/announcements/20200227-1098962/1098962_Final5Ingersol4Cimar.pdf',
            'https://www.sec.gov/Archives/edgar/data/1699150/000114036120014177/nt10012610x3_424b7.htm',
            'https://www.sec.gov/Archives/edgar/data/1466258/000114036120004470/nc10009348x1_ex99-1.htm']},
    'JCI': {'symbol':'JCI','selected_source':'yahoo_relay','comparison_from':'2016-09',
        'current_issuer_cik':'833444','historical_index_issuer_cik':'53669',
        'current_equity_predecessor':'Tyco International plc',
        'historical_index_equity':'Johnson Controls, Inc.',
        'merger_date':'2016-09-02','default_cash_usd_per_original_share':'5.7293',
        'default_current_shares_per_original_share':'0.8357','tyco_consolidation_ratio':'0.955',
        'holding_path_accepted':False,'full_history_accepted':False,
        'evidence_urls':[
            'https://www.sec.gov/Archives/edgar/data/53669/000110465916143072/a16-17944_18k.htm',
            'https://www.sec.gov/Archives/edgar/data/833444/000083344417000010/ex991fy2016recastof10-k.htm']},
}


def preserved(cursor):
    result = {}
    for table, order in [('prices_monthly','symbol,month,source'),('nominal_monthly','symbol,month,source'),
                         ('series_pick','symbol'),('data_lock','id'),('members','symbol,start_date')]:
        cursor.execute('select * from '+table+' where symbol=any(%s) order by '+order, (['IR','JCI','TT'],))
        result[table] = hashlib.sha256(json.dumps(cursor.fetchall(),default=str).encode()).hexdigest()
    result['baseline_b'] = hashlib.sha256((BASE/'output/baseline_b.json').read_bytes()).hexdigest()
    return result


def run(write=False):
    connection = db_sources.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute("set local lock_timeout='5s'")
            cursor.execute('lock table prices_monthly,nominal_monthly,series_pick,data_lock,members,sys_event in share row exclusive mode')
            cursor.execute("select subject,metrics from sys_event where source='historical_identity_review' and status='pass' order by subject,id")
            existing = cursor.fetchall()
            expected = [(s+':lineage-v1', {'proof':p,'evidence_sha256':digest(p)}) for s,p in sorted(FACTS.items())]
            if existing:
                if existing != expected:
                    raise RuntimeError('Historical lineage review conflicts; preserve existing evidence')
                connection.rollback();print('Historical identity reviews already online; no reprocessing');return
            cursor.execute('select symbol,source from series_pick where symbol=any(%s) order by symbol', (sorted(FACTS),))
            if dict(cursor.fetchall()) != {s:p['selected_source'] for s,p in FACTS.items()}:
                raise RuntimeError('Reviewed selected sources changed')
            before = preserved(cursor)
            if write:
                prior = json.loads((BASE/'output/historical_identity_reviews_rollback.json').read_text())
                if prior['preserved_sha256'] != before or prior['proofs'] != FACTS:
                    raise RuntimeError('Historical identity review differs from rollback evidence')
            cursor.execute((BASE/'sql/121_historical_identity_review_guards.sql').read_text())
            for subject, metrics in expected:
                cursor.execute("""insert into sys_event(kind,source,subject,status,metrics,finished_at)
                    values('validation','historical_identity_review',%s,'pass',%s,now())""", (subject,json.dumps(metrics)))
            if preserved(cursor) != before:
                raise RuntimeError('Original prices, locks, members or baseline changed')
        if write:connection.commit()
        else:connection.rollback()
        result = {'write':write,'proofs':FACTS,'preserved_sha256':before,
                  'prices_picks_members_locks_and_baseline_changed':False,'full_strategy_accepted':False}
        name = 'historical_identity_reviews_applied.json' if write else 'historical_identity_reviews_rollback.json'
        (BASE/'output'/name).write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'write':write,'reviews':len(FACTS),'financial_data_changed':False}))
    finally:connection.rollback();connection.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser();parser.add_argument('--write',action='store_true')
    run(parser.parse_args().write)
