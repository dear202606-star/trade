"""Import independently checked, event-reconstructed screening observations only."""
import argparse
import datetime as dt
import json
from pathlib import Path

import psycopg2.extras
import db_prices
import db_select

BASE=Path(__file__).resolve().parent


def run(write=False):
    report=json.loads((BASE/'output/yfinance_actions2022_review.json').read_text())
    if report['version']!=89 or tuple(report['snapshot_stamp'])!=tuple(db_prices._db_stamp() or ()):
        raise RuntimeError('Reviewed archive/version or selected input changed')
    for symbol,month,expected in [('NVDA','2016-12',106.74),('AAPL','2014-06',92.93),('MSFT','2016-12',62.14)]:
        result=report['results'][symbol]
        if result['status']!='crosscheck_pass' or abs(result['months'][month]['close']-expected)>.001:
            raise RuntimeError('Independent raw-unit anchor failed: '+symbol)
    rows=[]
    for symbol,record in report['results'].items():
        if record['status']!='crosscheck_pass':
            continue
        for month,bar in record['months'].items():
            if abs(bar['close']-5)<.01:
                raise RuntimeError('Unresolved $5 threshold ambiguity')
            rows.append((symbol,month,bar['close'],bar['date'],'kaggle_yfinance2022',report['source_url'],
                         dt.datetime.now(dt.timezone.utc),report['archive_sha256'],record['overlap_returns'],
                         record['median_log_deviation'],record['p90_log_deviation'],
                         'Same-vintage inverse dividend/split events; terminal quote 2022-07-12; '+
                         'AAPL/NVDA/MSFT native-price anchors; https://investor.apple.com/investor-relations/faq/default.aspx ; '+
                         'archive '+report['archive_sha256']))
    stored=0
    if write:
        connection=db_select.db()
        connection.autocommit=False
        try:
            with connection,connection.cursor() as cursor:
                psycopg2.extras.execute_values(cursor,
                    '''insert into nominal_monthly(symbol,month,close,trade_date,source,source_url,
                       fetched_at,evidence_sha256,crosscheck_pairs,median_log_deviation,p90_log_deviation,
                       unit_evidence) values %s on conflict(symbol,month,source) do nothing''',rows,page_size=1000)
                cursor.execute("select count(*) from nominal_monthly where source='kaggle_yfinance2022'")
                stored=cursor.fetchone()[0]
        finally:
            connection.close()
    receipt={'write':write,'prepared_observations':len(rows),'stored_observations':stored,
             'archive_sha256':report['archive_sha256'],'prices_locks_and_baselines_changed':False}
    (BASE/'output/yfinance_actions2022_ingest.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--write',action='store_true')
    run(parser.parse_args().write)
