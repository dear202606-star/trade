"""Load independently collected nominal observations, never return prices/locks.

Default read-only; --write requires migration 094 and an unchanged review stamp.
"""
import argparse
import hashlib
import json
from pathlib import Path

import psycopg2.extras
import db_prices
import db_select
from research.audit_nominal_prices import parse_daily

BASE = Path(__file__).resolve().parent


def run(write=False):
    report = json.loads((BASE/'output/nominal_price_review.json').read_text())
    stamp = tuple(db_prices._db_stamp() or ())
    if not stamp or stamp != tuple(report['snapshot_stamp']):
        raise RuntimeError('Review snapshot changed; regenerate before importing')
    rows = []
    for symbol, result in report['results'].items():
        if result['status'] != 'crosscheck_pass':
            continue
        payload = json.loads((BASE/'output/_nominal_daily'/(symbol+'.json')).read_text())
        digest = hashlib.sha256(json.dumps(payload['rows'], separators=(',', ':')).encode()).hexdigest()
        if digest != result['daily_sha256'] or payload['endpoint'] != result['endpoint']:
            raise RuntimeError('Daily evidence changed: ' + symbol)
        observed = parse_daily(payload['rows'])
        for month, bar in result['months'].items():
            if observed.get(month) != bar:
                raise RuntimeError('Monthly observation differs from daily evidence')
            rows.append((symbol,month,bar['close'],bar['date'],'sina_daily',result['endpoint'],
                         result['fetched_at'],digest,result['overlap_returns'],
                         result['median_log_deviation'],result['p90_log_deviation']))
    if write:
        connection = db_select.db()
        connection.autocommit = False
        try:
            with connection, connection.cursor() as cursor:
                # Existing proof is immutable during this import. Conflicting
                # vendor revisions require a new review, not an overwrite.
                cursor.execute('create temporary table nominal_incoming (like nominal_monthly including defaults) on commit drop')
                psycopg2.extras.execute_values(cursor,
                    '''insert into nominal_incoming(symbol,month,close,trade_date,source,source_url,
                       fetched_at,evidence_sha256,crosscheck_pairs,median_log_deviation,p90_log_deviation)
                       values %s''', rows, page_size=1000)
                cursor.execute('''select count(*) from nominal_incoming i join nominal_monthly n
                                  using(symbol,month,source) where i.close <> n.close or i.trade_date <> n.trade_date''')
                if cursor.fetchone()[0]:
                    raise RuntimeError('Conflicting stored nominal observations; no changes applied')
                cursor.execute('''insert into nominal_monthly
                                  select i.* from nominal_incoming i
                                  where exists(select 1 from series_selected p
                                               where p.symbol=i.symbol and p.month=i.month)
                                  on conflict(symbol,month,source) do nothing''')
                inserted = cursor.rowcount
        finally:
            connection.close()
    else:
        inserted = 0
    receipt = {'write': write, 'review_stamp': list(stamp), 'prepared_observations': len(rows),
               'inserted': inserted, 'return_prices_and_locks_changed': False}
    (BASE/'output/nominal_price_ingest.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--write', action='store_true')
    run(parser.parse_args().write)
