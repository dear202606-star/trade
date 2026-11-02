"""Complete A/B screening-impact inventory; unknown inputs remain unknown.

Compare both rules on one unchanged selected return-price snapshot. Never
publish candidate returns or treat incomplete years as corrected performance.
"""
import hashlib
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import pandas as pd
import datafeed
import db_prices
import db_ref
import db_select
import variant_b as variant_b
import strategy_history as variant_a
import holding_paths
from holding_consideration import POLICY


def nominal_snapshot():
    connection=db_select.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""select symbol,month,close,source,evidence_sha256
                              from v_nominal_monthly where month like '%-12'
                              and month between '2009-12' and '2025-12'
                              order by symbol,month""")
            rows=[(s,m,float(p),source,digest) for s,m,p,source,digest in cursor.fetchall()]
    finally:
        connection.close()
    digest=hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest()
    return {(s,m):p for s,m,p,source,proof in rows},digest


def run():
    prices = db_prices.load_all()
    stamp = tuple(db_prices._db_stamp() or ())
    if not stamp:
        raise RuntimeError('No selected input version')
    observations,nominal_sha=nominal_snapshot()
    event_paths=holding_paths.load_all()
    event_sha=holding_paths.input_sha()
    published_b=json.loads((BASE/'output/baseline_b.json').read_text()).get('years',{})
    members = db_ref.load_members()
    frame = pd.DataFrame([{'ticker': symbol, 'start_date': a, 'end_date': b}
                         for symbol, periods in members.items() for a, b in periods])
    sectors = db_ref.load_sectors()
    variant_b._ALL_BARS = prices
    datafeed._ALL_BARS = prices
    years, cells = [], []
    for year in range(2010, 2027):
        month = '%d-12' % (year-1)
        for variant in ('A','B'):
            pool = (variant_b.members_full_year(frame, year-1) if variant == 'B' else
                    set(frame[(frame.start_date <= '%d-01-01' % year) &
                              (frame.end_date >= '%d-01-01' % year)].ticker))
            candidates = []
            for symbol in sorted(pool):
                bars = prices.get(symbol, {})
                if month not in bars:
                    continue
                momentum=holding_paths.momentum_bars(symbol,bars,'%d-12'%(year-2),month)
                if variant == 'B':
                    ret = variant_b.ret12(momentum, month, '%d-12' % (year-2))
                    eligible = (ret is not None and abs(ret) <= variant_b.MAX_ABS_RET12
                                and variant_b._series_ok(momentum, '%d-12' % (year-2), month))
                else:
                    ret, eligible = variant_a.ret_12m(momentum, month)
                if not eligible:
                    continue
                nominal = observations.get((symbol, month))
                cell = {'variant': variant, 'year': year, 'symbol': symbol, 'month': month,
                        'return12': ret, 'legacy_price': bars[month][1],
                        'nominal_price': nominal,
                        'nominal_status': 'verified_observation' if nominal is not None else 'unknown',
                        'legacy_pass': bool(bars[month][1] and bars[month][1] >= 5),
                        'nominal_pass': nominal >= 5 if nominal is not None else None}
                cells.append(cell)
                candidates.append(cell)
            # The preflight above inventories every missing candidate, not just
            # the first exception encountered by the production selector.
            unknown = [c['symbol'] for c in candidates if c['nominal_price'] is None]
            def select(lookup):
                if variant == 'B':
                    return variant_b.pick_year(frame, sectors, year, price_lookup=lookup)[0]
                return variant_a.pick_for_year(year, [c['symbol'] for c in candidates],
                                               sectors, price_lookup=lookup)
            old = select(lambda s,m: prices[s][m][1])
            new = None if unknown else select(lambda s,m: observations[(s,m)])
            lower = upper = None
            if unknown:
                # Predicate bounds only, not invented price observations:
                # unknown candidates all fail versus all pass the $5 screen.
                lower = select(lambda s,m: observations.get((s,m), 0.0))
                upper = select(lambda s,m: observations.get((s,m), 5.0))
            def summarize(picks):
                if picks is None:
                    return None
                result = {'holdings': [{'ticker': p['ticker'], 'weight': p['weight']} for p in picks]}
                if year <= 2025 and picks:
                    missing=[{'symbol':p['ticker'],'month':'%d-%02d'%(year,m)}
                             for p in picks for m in range(1,13)
                             if event_paths.get((p['ticker'],year)) is None and
                                '%d-%02d'%(year,m) not in prices[p['ticker']]]
                    if missing:
                        result['return_status']='missing_actual_holding_months'
                        result['missing_holding_months']=missing
                    else:
                        ret, drawdown = variant_b.year_perf(picks, year)
                        result.update(return_value=ret, drawdown=drawdown,return_status='complete')
                return result
            before_summary,after_summary=summarize(old),summarize(new)
            complete=(not unknown and after_summary is not None and
                      (year==2026 or after_summary.get('return_status')=='complete'))
            years.append({'variant': variant, 'year': year, 'complete': complete,
                          'published_b_return':published_b.get(str(year)) if variant=='B' else None,
                          'price_screen_complete':not unknown,
                          'missing_nominal_symbols': unknown,
                          'before': before_summary, 'after': after_summary,
                          'unknown_all_fail_holdings': summarize(lower),
                          'unknown_all_pass_holdings': summarize(upper),
                          'predicate_bounds_agree': lower is not None and
                              [(p['ticker'],p['weight']) for p in lower] ==
                              [(p['ticker'],p['weight']) for p in upper]})
    if stamp != tuple(db_prices._db_stamp() or ()):
        raise RuntimeError('Snapshot changed during analysis')
    if nominal_sha!=nominal_snapshot()[1]:
        raise RuntimeError('Nominal observations changed during analysis')
    holding_paths._PATHS=None
    if event_sha!=holding_paths.input_sha():
        raise RuntimeError('Holding-event evidence changed during analysis')
    result = {'policy': '5 USD screen uses observed as-traded close; ranking and holdings share actual cash/successor paths',
              'snapshot_stamp': list(stamp), 'published_baselines_changed': False,
              'nominal_snapshot_sha256':nominal_sha,
              'holding_event_policy':POLICY,'holding_event_input_sha':event_sha,
              'comparison_scope':'legacy versus nominal floor on current sources and approved holding-event policy; before is not the published old baseline',
              'nominal_year_end_observations':len(observations),
              'baseline_b_sha256': hashlib.sha256((BASE/'output/baseline_b.json').read_bytes()).hexdigest(),
              'fully_verified': all(y['complete'] for y in years),
              'complete_variant_years': sum(y['complete'] for y in years),
              'variant_years': len(years), 'years': years, 'candidate_cells': cells}
    baseline = BASE/'output/baseline_b.json'
    preserved = BASE/'output/baseline_b_legacy_price_screen.json'
    if preserved.exists() and preserved.read_bytes()!=baseline.read_bytes():
        raise RuntimeError('Legacy baseline backup differs; do not overwrite it')
    if not preserved.exists():
        preserved.write_bytes(baseline.read_bytes())
    (BASE/'output/nominal_price_impact.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('years','candidate_cells')},indent=2))


if __name__ == '__main__':
    run()
