"""Read-only sensitivity run using a stamped live-DB snapshot and production B rules.

Never calls variant_b.main, writes a baseline, changes a pick, or publishes data.
The exclusion scenario is a review proposal, not a finding that every jump is bad.
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import pandas as pd
import db_ref
import variant_b as strategy


def run():
    snapshot = json.loads((BASE / 'output/_quarantine_review_prices.json').read_text())
    stamp = snapshot.pop('__stamp__', None)
    built = snapshot.pop('__built_at__', None)
    if not stamp or not built:
        raise RuntimeError('A stamped live-DB snapshot is required')
    jumps = json.loads((BASE / 'output/selected_jump_audit.json').read_text())
    suspects = sorted({r['symbol'] for r in jumps
                       if r['within_membership_month'] and (
                           (r['source'] == 'sina_repaired' and (r['ratio'] > 10 or r['ratio'] < .01))
                           or r['source'] == 'hf_ayushkhaire')})
    # EP is El Paso in the member universe, while the current ticker belongs
    # to Empire Petroleum. See the SEC evidence in ARCHIVE_IDENTITY_REVIEW.md.
    suspects = sorted(set(suspects) | {'EP'})
    members = db_ref.load_members()
    frame = pd.DataFrame([{'ticker': symbol, 'start_date': start, 'end_date': end}
                          for symbol, periods in members.items() for start, end in periods])
    sectors = db_ref.load_sectors()

    def scenario(exclude):
        strategy._ALL_BARS = {s: bars for s, bars in snapshot.items() if s not in exclude}
        years = []
        for year in range(2010, 2026):
            picks, pool = strategy.pick_year(frame, sectors, year,
                price_lookup=lambda symbol, month: snapshot[symbol][month][1])
            ret, drawdown = strategy.year_perf(picks, year)
            years.append({'year': year, 'tickers': [p['ticker'] for p in picks],
                          'return': ret, 'drawdown': drawdown, 'pool': pool})
        return years

    current = scenario(set())
    excluded = scenario(set(suspects))
    published = json.loads((BASE / 'docs/data/b/strategy_history.json').read_text())
    published_years = {r['year']: r for r in published['years']}
    result = {
        'snapshot_stamp': stamp, 'snapshot_built_at': built,
        'suspects_for_review': suspects,
        'definition': 'B pick_year/year_perf, 2010-2025; legacy adjusted-close screen before nominal correction; no publication',
        'current_snapshot': current, 'exclusion_scenario': excluded,
        'exclusion_changes': [a['year'] for a, b in zip(current, excluded)
                              if a['tickers'] != b['tickers'] or abs(a['return']-b['return']) > .0001],
        'current_vs_published_changes': [a['year'] for a in current if a['year'] in published_years
            and (a['tickers'] != [p['ticker'] for p in published_years[a['year']]['holdings']]
                 or abs(a['return']-published_years[a['year']]['ret']) > .005)],
    }
    path = BASE / 'output/quarantine_impact.json'
    path.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items()
                      if k not in ('current_snapshot', 'exclusion_scenario')}, indent=2))


if __name__ == '__main__':
    run()
