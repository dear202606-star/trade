"""Audit decision metadata against observed source rows; repair only metadata.

--write never changes prices, selected sources, locks, or published baselines.
Locked/frozen decisions and mixed/unknown source bases are left for review.
"""
import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import db_select


def mismatches(cursor):
    cursor.execute('''select k.symbol,k.source,k.basis,k.frozen,
        exists(select 1 from locked_symbols l where l.symbol=k.symbol),
        exists(select 1 from series_quarantine q where q.symbol=k.symbol),p.bases
        from series_pick k join
          (select symbol,source,array_agg(distinct basis order by basis) as bases
           from prices_monthly where month>='2008-01' group by symbol,source) p
        using(symbol,source)
        where array_length(p.bases,1)<>1 or k.basis is distinct from p.bases[1]
        order by k.symbol''')
    return [dict(zip(('symbol','source','decision_basis','frozen','locked','quarantined','observed_bases'),r))
            for r in cursor.fetchall()]


def selected_fingerprint(cursor):
    cursor.execute('''select count(*),coalesce(sum(adj_close),0)::text,
                      coalesce(sum(raw_close),0)::text from series_selected''')
    return list(cursor.fetchone())


def run(write=False):
    baseline = (BASE/'output/baseline_b.json').read_bytes()
    connection = db_select.db()
    connection.autocommit = False
    try:
        with connection, connection.cursor() as cursor:
            if write:
                # Keep evidence, lock state and source choices unchanged for
                # this short metadata-only repair transaction.
                cursor.execute('lock table series_pick in share row exclusive mode')
                cursor.execute('lock table locked_symbols,prices_monthly in share mode')
            before = mismatches(cursor)
            fingerprint = selected_fingerprint(cursor)
            corrected = []
            if write:
                for row in before:
                    bases = row['observed_bases']
                    if row['frozen'] or row['locked'] or len(bases)!=1 or bases[0] not in ('price_only','total_return'):
                        continue
                    cursor.execute('''update series_pick set basis=%s,
                        reason=coalesce(reason,'')||'; observed source basis metadata corrected'
                        where symbol=%s and source=%s and basis is not distinct from %s
                        and not frozen and not exists
                          (select 1 from locked_symbols l where l.symbol=series_pick.symbol)''',
                        (bases[0],row['symbol'],row['source'],row['decision_basis']))
                    if cursor.rowcount!=1:
                        raise RuntimeError('Decision changed during repair: '+row['symbol'])
                    corrected.append(row['symbol'])
            after = mismatches(cursor)
            if fingerprint != selected_fingerprint(cursor):
                raise RuntimeError('Metadata repair changed selected prices')
            if baseline != (BASE/'output/baseline_b.json').read_bytes():
                raise RuntimeError('Published baseline changed during repair')
    finally:
        connection.close()
    result = {'audited_at':dt.datetime.now(dt.timezone.utc).isoformat(), 'write':write,
              'mismatches_before':before, 'corrected_symbols':corrected, 'remaining':after,
              'selected_price_fingerprint_unchanged':fingerprint,
              'baseline_b_sha256':hashlib.sha256(baseline).hexdigest(),
              'price_rows_sources_locks_and_baselines_changed':False}
    name = 'selection_basis_repair.json' if write else 'selection_basis_audit.json'
    (BASE/'output'/name).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'write':write, 'mismatches_before':len(before), 'corrected':len(corrected),
                      'remaining':len(after), 'selected_prices_unchanged':True}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--write',action='store_true')
    run(parser.parse_args().write)
