"""Stage the reviewed BK→BNY exact copies; never promote or change picks."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import db_sources
from import_verified_renames import check_prices


def run(write=False):
    review = json.loads((BASE / 'output/bk_exact_repair_review.json').read_text())
    connection = db_sources.db()
    connection.set_session(isolation_level='REPEATABLE READ')
    try:
        with connection, connection.cursor() as cursor:
            proof = check_prices(cursor, 'BK', 'BNY', '2026-12')
            cursor.execute("select month,adj_close::text,raw_close::text,source,basis from series_selected where symbol='BNY' and month<='2026-12' order by month")
            rows = cursor.fetchall()
            digest = hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest()
            if digest != review['canonical_rows_sha256'] or proof != review['proof']:
                raise RuntimeError('Reviewed canonical input changed')
            cursor.execute("select old_symbol,current_symbol,effective_date::text,evidence_url from v_verified_ticker_renames where old_symbol='BK' and current_symbol='BNY'")
            if [list(row) for row in cursor.fetchall()] != review['online_identity']:
                raise RuntimeError('Reviewed online identity changed or was revoked')
            cursor.execute("select source,basis,frozen from series_pick where symbol='BK' for share")
            if cursor.fetchone() != ('sina_repaired', 'price_only', False):
                raise RuntimeError('BK source decision changed')
            cursor.execute("select exists(select 1 from locked_symbols where symbol='BK'),exists(select 1 from series_quarantine where symbol='BK')")
            if any(cursor.fetchone()):
                raise RuntimeError('BK is protected or quarantined')
            cursor.execute("select count(*) from prices_monthly where symbol='BK' and source='yahoo_relay'")
            if cursor.fetchone()[0]:
                raise RuntimeError('Target prices already exist; review before retrying')
            cursor.execute("select count(*) from ingest_staging where symbol='BK' and source='yahoo_relay'")
            if cursor.fetchone()[0]:
                raise RuntimeError('Target staging already exists; review before retrying')
            staged = 0
            if write:
                cursor.execute("""insert into ingest_staging
                    (symbol,month,adj_close,raw_close,source,quality,basis)
                    select 'BK',p.month,p.adj_close,p.raw_close,p.source,origin.quality,p.basis
                    from series_selected p join prices_monthly origin using(symbol,month,source)
                    where p.symbol='BNY' and p.month<='2026-12'
                      and p.source='yahoo_relay' and p.basis='total_return' and origin.quality='ok'""")
                staged = cursor.rowcount
                if staged != len(rows):
                    raise RuntimeError('Incomplete staging; transaction will roll back')
    finally:
        connection.close()
    result = {'write': write, 'staged': staged, 'canonical_rows_sha256': digest,
              'proof': proof, 'promoted_or_source_decisions_changed': False}
    if write:
        (BASE / 'output/bk_rename_staged.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true')
    run(parser.parse_args().write)
