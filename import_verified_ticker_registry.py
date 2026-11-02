"""Seed reviewed identity facts; never copy prices or change source decisions."""
import argparse
import hashlib
import json
from pathlib import Path

import db_select

BASE=Path(__file__).resolve().parent


def run(write=False):
    aliases=json.loads((BASE/'static/verified_ticker_renames.json').read_text())['aliases']
    inserted=0
    if write:
        connection=db_select.db();connection.autocommit=False
        try:
            with connection,connection.cursor() as cursor:
                for row in aliases:
                    digest=hashlib.sha256(json.dumps(row,sort_keys=True,separators=(',',':')).encode()).hexdigest()
                    cursor.execute('''insert into verified_ticker_renames
                      (old_symbol,current_symbol,effective_date,evidence_url,evidence_sha256,verified)
                      values (%s,%s,%s,%s,%s,true) on conflict do nothing''',
                      (row['old'],row['current'],row['effective_date'],row['evidence'],digest))
                    inserted+=cursor.rowcount
                    cursor.execute('''select evidence_sha256,verified from verified_ticker_renames
                      where old_symbol=%s and current_symbol=%s and effective_date=%s''',
                      (row['old'],row['current'],row['effective_date']))
                    if cursor.fetchone()!=(digest,True):raise RuntimeError('Existing identity fact differs or was revoked')
        finally:connection.close()
    result={'write':write,'prepared':len(aliases),'inserted':inserted,'aliases':aliases,
            'prices_sources_locks_and_published_outputs_changed':False}
    name='membership_alias_ingest.json' if write else 'membership_alias_review.json'
    (BASE/'output'/name).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--write',action='store_true')
    run(parser.parse_args().write)
