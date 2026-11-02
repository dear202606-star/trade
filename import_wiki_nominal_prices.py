"""Load WIKI's raw Close, with separate unit and return-series evidence."""
import argparse
import datetime as dt
import json
from pathlib import Path

import psycopg2.extras
import db_prices
import db_select

BASE = Path(__file__).resolve().parent


def run(write=False):
    review = json.loads((BASE/'output/wiki_nominal_review.json').read_text())
    archive = json.loads((BASE/'output/wiki_monthly_archive.json').read_text())
    if review['archive_sha256'] != archive['archive_sha256']:
        raise RuntimeError('Archive fingerprint differs from review')
    if tuple(review['snapshot_stamp']) != tuple(db_prices._db_stamp() or ()):
        raise RuntimeError('Selected return-price snapshot changed')
    # Raw-price discontinuity versus adjusted continuity at an actual split is
    # independent evidence of the archive's Close column share units.
    apple = [r for r in archive['raw_unit_split_checks']
             if r['symbol']=='AAPL' and r['date']=='2014-06-09']
    if len(apple)!=1 or apple[0]['split_ratio']!=7 or not (
        apple[0]['raw_before']>600 and 80<apple[0]['raw_after']<100
        and abs(apple[0]['raw_ratio_times_split']/apple[0]['adjusted_ratio']-1)<.001):
        raise RuntimeError('Raw price-unit split anchor missing or inconsistent')
    rows = []
    for symbol, record in review['results'].items():
        if record['status']!='crosscheck_pass':
            continue
        original = archive['monthly'][record['archive_symbol']]
        for month, bar in record['months'].items():
            source_bar = original[month]
            if source_bar['date']!=bar['date'] or source_bar['close']!=bar['close']:
                raise RuntimeError('Monthly close differs from archive')
            rows.append((symbol,month,bar['close'],bar['date'],'quandl_wiki',review['source_url'],
                         dt.datetime.now(dt.timezone.utc),archive['archive_sha256'],
                         record['overlap_returns'],record['median_log_deviation'],
                         record['p90_log_deviation'],
                         'WIKI raw Close, distinct Adj Close and split_ratio; AAPL 2014-06-09 anchor '+
                         'https://investor.apple.com/investor-relations/faq/default.aspx ; archive SHA256 '+archive['archive_sha256']))
    inserted = 0
    if write:
        connection = db_select.db()
        connection.autocommit = False
        try:
            with connection,connection.cursor() as cursor:
                psycopg2.extras.execute_values(cursor,
                    '''insert into nominal_monthly(symbol,month,close,trade_date,source,source_url,
                       fetched_at,evidence_sha256,crosscheck_pairs,median_log_deviation,
                       p90_log_deviation,unit_evidence) values %s
                       on conflict(symbol,month,source) do nothing''',rows,page_size=1000)
                cursor.execute("select count(*) from nominal_monthly where source='quandl_wiki'")
                inserted=cursor.fetchone()[0]
        finally:
            connection.close()
    receipt = {'write':write,'prepared_observations':len(rows),'stored_wiki_observations':inserted,
               'raw_unit_anchor':apple[0],'archive_sha256':archive['archive_sha256'],
               'return_prices_locks_and_published_baselines_changed':False}
    (BASE/'output/wiki_nominal_ingest.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--write',action='store_true')
    run(parser.parse_args().write)
