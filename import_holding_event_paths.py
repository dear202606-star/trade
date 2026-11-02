"""Recompute verified event evidence and append holding paths, never quotes."""
import argparse
import json
from pathlib import Path

import psycopg2.extras
import db_select
import holding_paths
from holding_consideration import POLICY
from research import audit_lb_distribution, audit_cvc_cash_merger, audit_wdc_distribution

BASE=Path(__file__).resolve().parent


def run(write=False):
    originals=[audit_lb_distribution.run(write=False),audit_cvc_cash_merger.run(),audit_wdc_distribution.run()]
    reports=[]
    for evidence in originals:
        for symbol in evidence.get('same_common_equity_tickers',[evidence['symbol']]):
            reports.append(dict(evidence,symbol=symbol,original_symbol=evidence['symbol']))
    prepared=[]
    for evidence in reports:
        year=evidence['hold_year']
        row={'symbol':evidence['symbol'],'hold_year':year,'event_date':evidence['event_date'],
             'policy':POLICY,'monthly_values':[evidence['retained_monthly_values']['%d-%02d'%(year,m)] for m in range(1,13)],
             'evidence':evidence,'evidence_sha256':holding_paths.evidence_sha(evidence),
             'calculator_sha256':holding_paths.calculator_sha(evidence['producer'])}
        holding_paths.validate(row)
        prepared.append(row)
    inserted=0
    if write:
        connection=db_select.db();connection.autocommit=False
        try:
            with connection,connection.cursor() as cursor:
                for row in prepared:
                    cursor.execute('''insert into holding_event_paths
                        (symbol,hold_year,event_date,policy,monthly_values,evidence,evidence_sha256,calculator_sha256,verified)
                        values (%s,%s,%s,%s,%s,%s,%s,%s,true) on conflict do nothing''',
                        (row['symbol'],row['hold_year'],row['event_date'],row['policy'],
                         psycopg2.extras.Json(row['monthly_values']),psycopg2.extras.Json(row['evidence']),
                         row['evidence_sha256'],row['calculator_sha256']))
                    inserted+=cursor.rowcount
                cursor.execute('select count(*) from v_holding_event_paths')
                active=cursor.fetchone()[0]
        finally:connection.close()
    else:active=None
    receipt={'write':write,'prepared_paths':len(prepared),'inserted_versions':inserted,
             'active_verified_paths':active,'policy':POLICY,
             'paths':[{k:v for k,v in r.items() if k not in ('evidence','monthly_values')} for r in prepared],
             'quoted_prices_locks_and_published_baselines_changed':False}
    (BASE/'output/holding_event_ingest.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--write',action='store_true')
    run(parser.parse_args().write)
