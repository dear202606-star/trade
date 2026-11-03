"""Apply only the reviewed BK batch, transactionally; default rolls back."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import db_sources
from import_verified_renames import check_prices


def digest(rows):
    return hashlib.sha256(json.dumps(rows, separators=(',', ':'), default=str).encode()).hexdigest()


def run(write=False):
    review = json.loads((BASE / 'output/bk_exact_repair_review.json').read_text())
    impact = json.loads((BASE / 'output/bk_rename_impact.json').read_text())
    if impact['changes'] or impact['reviewed_replacement'] != review['proof']:
        raise RuntimeError('BK strategy impact needs review')
    connection = db_sources.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute("set local lock_timeout='5s'")
            cursor.execute("set local statement_timeout='30s'")
            cursor.execute("select pg_advisory_xact_lock(hashtext('trade_promote_staging')::bigint)")
            # Briefly prevent input/lock changes during exact copying and verification.
            cursor.execute('lock table ingest_staging,prices_monthly,series_pick,security_history_window,verified_ticker_renames,data_lock,series_quarantine in share row exclusive mode')
            cursor.execute("select count(*),coalesce(max(fetched_at)::text,''),count(distinct symbol) from prices_monthly")
            price_stamp = cursor.fetchone()
            cursor.execute("select count(*),count(distinct symbol),coalesce(sum(adj_close),0)::text from series_selected")
            if list(price_stamp + cursor.fetchone()) != impact['selected_input_stamp']:
                raise RuntimeError('Strategy impact price snapshot changed; rerun the audit')
            if hashlib.sha256((BASE / 'output/baseline_b.json').read_bytes()).hexdigest() != impact['baseline_b_sha256']:
                raise RuntimeError('Reviewed baseline changed')
            if check_prices(cursor, 'BK', 'BNY', '2026-12') != review['proof']:
                raise RuntimeError('Independent canonical proof changed')
            cursor.execute("select month,adj_close::text,raw_close::text,source,basis from series_selected where symbol='BNY' and month<='2026-12' order by month")
            canonical = cursor.fetchall()
            if digest(canonical) != review['canonical_rows_sha256']:
                raise RuntimeError('Exact reviewed canonical input changed')
            cursor.execute("select old_symbol,current_symbol,effective_date::text,evidence_url from v_verified_ticker_renames where old_symbol='BK' and current_symbol='BNY'")
            if [list(r) for r in cursor.fetchall()] != review['online_identity']:
                raise RuntimeError('Online issuer identity changed')
            cursor.execute("select source,basis,frozen from series_pick where symbol='BK'")
            if cursor.fetchone() != ('sina_repaired', 'price_only', False):
                raise RuntimeError('Original source decision changed')
            cursor.execute("select exists(select 1 from locked_symbols where symbol='BK'),exists(select 1 from series_quarantine where symbol='BK')")
            if any(cursor.fetchone()):
                raise RuntimeError('BK is protected or quarantined')
            cursor.execute("select month,adj_close::text,raw_close::text,source,basis from prices_monthly where symbol='BK' order by month,source")
            originals = cursor.fetchall()
            if any(row[3] == 'yahoo_relay' for row in originals):
                raise RuntimeError('Target prices already exist')
            cursor.execute("select id,month,adj_close::text,raw_close::text,source,basis,quality,promoted,reject_reason from ingest_staging where symbol='BK' and source='yahoo_relay' order by month")
            staged = cursor.fetchall()
            if [r[1:6] for r in staged] != canonical or any(r[6:] != ('ok', False, None) for r in staged):
                raise RuntimeError('Staged row identities or exact values changed')
            ids = [r[0] for r in staged]
            cursor.execute((BASE / 'sql/107_bk_verified_alias_window.sql').read_text())
            cursor.execute("select month_to,evidence_url from security_history_window where symbol='BK'")
            if cursor.fetchone() != ('2026-12', review['online_identity'][0][3]):
                raise RuntimeError('Existing history window differs')
            cursor.execute("""insert into prices_monthly(symbol,month,adj_close,raw_close,source,quality,basis)
                select symbol,month,adj_close,raw_close,source,quality,basis
                from ingest_staging where id=any(%s)""", (ids,))
            if cursor.rowcount != len(canonical):
                raise RuntimeError('Incomplete exact promotion')
            cursor.execute("update ingest_staging set promoted=true where id=any(%s) and not promoted and reject_reason is null", (ids,))
            if cursor.rowcount != len(ids):
                raise RuntimeError('Incomplete exact staging acknowledgement')
            cursor.execute("update series_pick set source='yahoo_relay',basis='total_return',chosen_at=now(),reason='Verified BK to BNY same-equity exact canonical copy; bk_exact_repair_review.json' where symbol='BK' and source='sina_repaired' and basis='price_only' and not frozen")
            if cursor.rowcount != 1:
                raise RuntimeError('Source decision precondition failed')
            cursor.execute("select month,adj_close::text,raw_close::text,source,basis from series_selected where symbol='BK' order by month")
            if cursor.fetchall() != canonical:
                raise RuntimeError('Selected BK series differs from canonical')
            cursor.execute("select month,adj_close::text,raw_close::text,source,basis from prices_monthly where symbol='BK' and source<>'yahoo_relay' order by month,source")
            if cursor.fetchall() != originals:
                raise RuntimeError('Original observations changed')
        result = {'write': write, 'rows': len(ids), 'staging_ids': ids,
                  'canonical_rows_sha256': digest(canonical), 'old_rows_preserved': len(originals),
                  'selected_exact_match': True, 'history_window': '2026-12'}
        if write:
            connection.commit()
        else:
            connection.rollback()
        (BASE / 'output' / ('bk_rename_applied.json' if write else 'bk_rename_rollback_probe.json')).write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({k: v for k, v in result.items() if k != 'staging_ids'}, indent=2))
    finally:
        connection.rollback()
        connection.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true')
    run(parser.parse_args().write)
