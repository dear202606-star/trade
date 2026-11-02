"""Read-only B sensitivity for documented same-equity ticker renames.

Uses the stamped selected-view snapshot only if its selected-view fingerprint
still matches live DB; canonical prices are also read live and compared exactly.
Does not copy prices, modify picks, freeze anything, or publish strategy outputs.
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import pandas as pd
import db_prices
import db_sources
import db_ref
import variant_b as strategy


def run():
    aliases = json.loads((BASE / 'static/verified_ticker_renames.json').read_text())['aliases']
    snapshot = json.loads((BASE / 'output/_prices_cache.json').read_text())
    stamp = snapshot.pop('__stamp__')
    built = snapshot.pop('__built_at__')
    live_stamp = db_prices._db_stamp()
    if not live_stamp or tuple(stamp[-3:]) != tuple(live_stamp[-3:]):
        raise RuntimeError('Selected-view snapshot changed; refresh it before this audit')
    replacements = {}
    with db_sources.db() as connection, connection.cursor() as cursor:
        for entry in aliases:
            old, current = entry['old'], entry['current']
            cursor.execute('select month,adj_close,raw_close,basis,source from series_selected where symbol=%s', (current,))
            rows = cursor.fetchall()
            if not rows or any(basis != 'total_return' or close is None for month, adj, close, basis, source in rows):
                raise RuntimeError('%s has no complete canonical total-return input' % current)
            bars = {month: [float(adj), float(close)] for month, adj, close, basis, source in rows}
            if bars != snapshot[current]:
                raise RuntimeError('Canonical input changed: %s' % current)
            cursor.execute('select exists(select 1 from locked_symbols where symbol=%s),exists(select 1 from series_quarantine where symbol=%s)', (old, old))
            if any(cursor.fetchone()):
                raise RuntimeError('Target is locked or quarantined: %s' % old)
            # Continue the same equity through the rename year's holding end;
            # do not populate later old-code years that could be reused.
            through = entry['effective_date'][:4] + '-12'
            replacements[old] = {month: values for month, values in bars.items() if month <= through}
    members = db_ref.load_members()
    frame = pd.DataFrame([{'ticker': symbol, 'start_date': start, 'end_date': end}
                          for symbol, periods in members.items() for start, end in periods])
    sectors = db_ref.load_sectors()

    def scenario(bars):
        strategy._ALL_BARS = bars
        years = []
        for year in range(2010, 2026):
            picks, pool = strategy.pick_year(frame, sectors, year,
                price_lookup=lambda symbol, month: bars[symbol][month][1])
            ret, drawdown = strategy.year_perf(picks, year)
            years.append({'year': year, 'holdings': [(p['ticker'],p['weight']) for p in picks],
                          'return': ret, 'drawdown': drawdown, 'pool': pool})
        picks, pool = strategy.pick_year(frame, sectors, 2026,
            price_lookup=lambda symbol, month: bars[symbol][month][1])
        return years, [(p['ticker'],p['weight']) for p in picks]

    before, official_before = scenario(snapshot)
    after, official_after = scenario(dict(snapshot, **replacements))
    result = {'screening_policy': 'legacy adjusted close, before nominal-price correction',
              'snapshot_stamp': stamp, 'snapshot_built_at': built, 'aliases': aliases,
              'canonical_input_exact_match': True, 'before': before, 'after': after,
              'changed_holdings_or_returns': [a['year'] for a,b in zip(before,after)
                    if a['holdings']!=b['holdings'] or abs(a['return']-b['return'])>.0001],
              'official_2026_before': official_before, 'official_2026_after': official_after,
              'official_2026_changed': official_before != official_after}
    (BASE / 'output/rename_impact.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('before','after','aliases')},indent=2))
    return result


if __name__ == '__main__':
    run()
