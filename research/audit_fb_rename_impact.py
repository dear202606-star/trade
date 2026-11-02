"""Isolate the reviewed FB→META source repair; never apply source choices."""
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import datafeed
import db_prices
import db_ref
import db_sources
import holding_paths
import security_membership
import strategy_history as a
import variant_b as b
from import_verified_renames import check_prices
from research.audit_nominal_impact import nominal_snapshot


def run():
    original=db_prices.load_all();stamp=tuple(db_prices._db_stamp() or ())
    if not stamp:raise RuntimeError('No selected source version')
    native,native_sha=nominal_snapshot()
    event_sha=holding_paths.input_sha();aliases_sha=security_membership.input_sha()
    baseline=(BASE/'output/baseline_b.json').read_bytes()
    connection=db_sources.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute("select source,basis,frozen from series_pick where symbol='FB'")
            if cursor.fetchone()!=('sina_repaired','price_only',False):
                raise RuntimeError('FB source state changed; preserve the original preapplication report')
            proof=check_prices(cursor,'FB','META','2022-12')
            cursor.execute("select month,adj_close,raw_close from series_selected where symbol='META' and month<='2022-12' order by month")
            replacement={m:(float(adj),float(raw)) for m,adj,raw in cursor.fetchall()}
            cursor.execute("select exists(select 1 from locked_symbols where symbol='FB'),exists(select 1 from series_pick where symbol='FB' and frozen),exists(select 1 from series_quarantine where symbol='FB')")
            if any(cursor.fetchone()):raise RuntimeError('FB is protected or quarantined')
    finally:connection.close()
    members=db_ref.load_members();sectors=db_ref.load_sectors()
    frame=pd.DataFrame([{'ticker':s,'start_date':start,'end_date':end}
                        for s,periods in members.items() for start,end in periods])
    references={'members':members,'sectors':sectors}
    ref_sha=holding_paths.evidence_sha(references)
    before_after={}
    for label,prices in [('before',original),('after',dict(original,FB=replacement))]:
        b._ALL_BARS=prices;datafeed._ALL_BARS=prices
        results=[]
        for variant in ('A','B'):
            for year in range(2010,2027):
                bounds=[]
                a_pool=set(frame[(frame.start_date<='%d-01-01'%year)&
                                 (frame.end_date>='%d-01-01'%year)].ticker)
                a_pool={s for s in a_pool if '%d-12'%(year-1) in prices.get(s,{})}
                for passed in (False,True):
                    def lookup(symbol,month):return native.get((symbol,month),5.0 if passed else 0.0)
                    picks=(b.pick_year(frame,sectors,year,price_lookup=lookup)[0] if variant=='B'
                           else a.pick_for_year(year,a_pool,sectors,price_lookup=lookup))
                    if len(picks)!=3:raise RuntimeError('Incomplete scenario holdings: %s %d'%(variant,year))
                    row={'unknown_floor':'all_pass' if passed else 'all_fail',
                         'holdings':[(p['ticker'],p['weight']) for p in picks]}
                    if year<=2025 and picks:
                        try:
                            ret,mdd=b.year_perf(picks,year)
                            row.update(return_value=ret,drawdown=mdd,return_status='complete')
                        except RuntimeError as exc:row.update(return_status='unresolved',reason=str(exc))
                    bounds.append(row)
                results.append({'variant':variant,'year':year,'bounds':bounds})
        before_after[label]=results
    if stamp!=tuple(db_prices._db_stamp() or ()):raise RuntimeError('Selected prices changed')
    if ref_sha!=holding_paths.evidence_sha({'members':db_ref.load_members(),'sectors':db_ref.load_sectors()}):
        raise RuntimeError('Reference snapshot changed')
    holding_paths._PATHS=None;security_membership._ALIASES=None
    if event_sha!=holding_paths.input_sha() or aliases_sha!=security_membership.input_sha():
        raise RuntimeError('Event/identity evidence changed')
    if baseline!=(BASE/'output/baseline_b.json').read_bytes():raise RuntimeError('Baseline changed')
    changes=[{'variant':x['variant'],'year':x['year'],'before':x['bounds'],'after':y['bounds']}
             for x,y in zip(before_after['before'],before_after['after']) if x!=y]
    result={'reviewed_replacement':proof,'issuer_evidence':'https://www.sec.gov/Archives/edgar/data/1326801/000132680122000070/fb-20220531.htm',
            'comparison_scope':'FB source change only; same actual-consideration and verified-continuity policies; fixed nominal-floor snapshot with both missing-price predicate bounds',
            'selected_input_stamp':list(stamp),'nominal_input_sha':native_sha,'reference_input_sha':ref_sha,
            'event_input_sha':event_sha,'alias_input_sha':aliases_sha,
            'baseline_b_sha256':hashlib.sha256(baseline).hexdigest(),'changes':changes,
            'source_choices_prices_locks_and_published_outputs_changed':False,**before_after}
    (BASE/'output/fb_rename_impact.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'compared_variant_years':34,'predicate_scenarios_per_year':2,'changed_variant_years':len(changes)}))


if __name__=='__main__':run()
