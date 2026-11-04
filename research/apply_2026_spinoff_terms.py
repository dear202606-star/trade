"""Record reviewed issuer terms only; default rolls back, --write appends online."""
import argparse
import json
import sys
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import db_sources
from research.apply_recent_index_changes import digest

TERMS=[
    {'symbol':'FDX','event_date':'2026-06-01','kind':'spinoff',
     'retained_parent_symbol':'FDX','retained_parent_units_per_original_share':'1',
     'distributed_symbol':'FDXF','distributed_units_per_original_share':'0.5',
     'record_date':'2026-05-15','distributed_regular_way_start':'2026-06-01',
     'fractional_shares':'Cash in lieu; exact proceeds not accepted',
     'url':'https://investor.fedex.com/news-and-events/investor-news/investor-news-details/2026/FedEx-Completes-Spin-Off-of-FedEx-Freight/default.aspx'},
    {'symbol':'HON','event_date':'2026-06-29','kind':'spinoff_and_reverse_split',
     'retained_parent_symbol':'HON','retained_parent_units_per_original_share':'0.5',
     'distributed_symbol':'HONA','distributed_units_per_original_share':'0.5',
     'record_date':'2026-06-15','distributed_regular_way_start':'2026-06-29',
     'fractional_shares':'Cash in lieu for both securities; exact proceeds not accepted',
     'url':'https://www.honeywell.com/us/en/news/press-releases/2026/06/honeywell-technologies-launches-independent-pure-play-automation-company-following-honeywell-aerospace-spin-off'},
]


def proof(term):
    return {'terms':term,'scope':'Verified corporate terms only; no replacement market quote or accepted return path',
            'holding_valuation_accepted':False}


def verify_existing(rows):
    expected={t['symbol']+':'+t['event_date']:proof(t) for t in TERMS}
    if len(rows)!=len(expected) or {s for s,m in rows}!=set(expected):
        raise RuntimeError('Partial or duplicate corporate terms; no overwrite')
    for subject,metrics in rows:
        if metrics.get('proof')!=expected[subject] or metrics.get('evidence_sha256')!=digest(expected[subject]):
            raise RuntimeError('Corporate terms conflict; no overwrite')


def run(write=False):
    connection=db_sources.db()
    connection.autocommit=False
    connection.set_session(isolation_level='REPEATABLE READ')
    try:
        with connection.cursor() as cursor:
            cursor.execute("set local statement_timeout='45s'")
            cursor.execute("set local lock_timeout='5s'")
            cursor.execute('select pg_advisory_xact_lock(2026,629)')
            subjects=[t['symbol']+':'+t['event_date'] for t in TERMS]
            cursor.execute("select subject,metrics from sys_event where kind='validation' and source='verified_corporate_terms' and status='pass' and subject=any(%s)",(subjects,))
            existing=cursor.fetchall()
            if existing:
                verify_existing(existing)
                connection.rollback()
                print('Reviewed spin-off terms already recorded; no reprocessing')
                return
            preserved={}
            symbols=sorted({t['symbol'] for t in TERMS}|{t['distributed_symbol'] for t in TERMS})
            # No price/selection/lock/member write is part of this importer.
            # Hold stable snapshots for before/after receipts without locking history.
            for table,order in [('prices_monthly','symbol,month,source'),('nominal_monthly','symbol,month,source'),
                                ('series_pick','symbol'),('data_lock','id'),('members','symbol,start_date')]:
                cursor.execute('select * from '+table+' where symbol=any(%s) order by '+order,(symbols,))
                preserved[table]=digest(cursor.fetchall())
            result={'write':write,'terms':TERMS,'preserved_input_symbols':symbols,'preserved_inputs':preserved,
                    'price_writes':0,'strategy_output_writes':0,'holding_valuation_accepted':False}
            if write:
                reviewed=json.loads((BASE/'output/spinoff_terms_rollback.json').read_text())
                if {k:v for k,v in result.items() if k!='write'}!={k:v for k,v in reviewed.items() if k!='write'}:
                    raise RuntimeError('Reviewed rollback inputs changed')
            for term in TERMS:
                value=proof(term)
                cursor.execute("insert into sys_event(kind,source,subject,status,metrics,finished_at) values('validation','verified_corporate_terms',%s,'pass',%s,now())",
                    (term['symbol']+':'+term['event_date'],json.dumps({'proof':value,'evidence_sha256':digest(value)})))
            for table,order in [('prices_monthly','symbol,month,source'),('nominal_monthly','symbol,month,source'),
                                ('series_pick','symbol'),('data_lock','id'),('members','symbol,start_date')]:
                cursor.execute('select * from '+table+' where symbol=any(%s) order by '+order,(symbols,))
                if digest(cursor.fetchall())!=preserved[table]:raise RuntimeError('Unexpected input change: '+table)
        if write:connection.commit()
        else:connection.rollback()
        name='spinoff_terms_applied.json' if write else 'spinoff_terms_rollback.json'
        (BASE/'output'/name).write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'write':write,'new_terms':len(TERMS),'price_writes':0}))
    finally:
        connection.rollback();connection.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write',action='store_true')
    run(parser.parse_args().write)
