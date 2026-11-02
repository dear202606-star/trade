"""Read-only B comparison: old ticker intervals versus verified equity continuity."""
import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import db_prices
import db_ref
import holding_paths
import security_membership
import variant_b
from research.audit_nominal_impact import nominal_snapshot


def legacy_members(frame,year):
    return set(frame[(frame.start_date<='%d-01-01'%year)&
                     (frame.end_date>='%d-12-31'%year)].ticker.unique())


def run():
    baseline=(BASE/'output/baseline_b.json').read_bytes()
    prices=db_prices.load_all();stamp=tuple(db_prices._db_stamp() or ())
    if not stamp:raise RuntimeError('No selected input version')
    native,native_sha=nominal_snapshot()
    aliases_sha=security_membership.input_sha();event_sha=holding_paths.input_sha()
    members=db_ref.load_members();sectors=db_ref.load_sectors()
    frame=pd.DataFrame([{'ticker':s,'start_date':a,'end_date':b}
                        for s,periods in members.items() for a,b in periods])
    variant_b._ALL_BARS=prices
    years=[]
    for year in range(2010,2027):
        old_pool=legacy_members(frame,year-1)
        new_pool=security_membership.members_full_year(frame,year-1)
        scenarios={}
        for label,selector in [('before',legacy_members),('after',security_membership.members_full_year)]:
            bounds=[]
            for unknown_pass in (False,True):
                def lookup(symbol,month):return native.get((symbol,month),5.0 if unknown_pass else 0.0)
                with patch.object(variant_b,'members_full_year',selector):
                    picks,pool=variant_b.pick_year(frame,sectors,year,price_lookup=lookup)
                result={'holdings':[{'ticker':p['ticker'],'weight':p['weight'],'ret12':p['ret12']} for p in picks],
                        'passing_pool':pool,'unknown_native_predicate':'all_pass' if unknown_pass else 'all_fail'}
                if year<=2025 and picks:
                    try:
                        ret,mdd=variant_b.year_perf(picks,year)
                        result.update(return_value=ret,drawdown=mdd,return_status='complete')
                    except RuntimeError as exc:result.update(return_status='unresolved',reason=str(exc))
                bounds.append(result)
            scenarios[label]={'bounds':bounds,'holdings_invariant_to_unknown_floor':
                              bounds[0]['holdings']==bounds[1]['holdings']}
        before=scenarios['before']['bounds'][0];after=scenarios['after']['bounds'][0]
        years.append(dict(year=year,membership_added=sorted(new_pool-old_pool),membership_removed=sorted(old_pool-new_pool),
                          holdings_changed=before['holdings']!=after['holdings'],
                          return_delta=(after['return_value']-before['return_value'])
                          if 'return_value' in before and 'return_value' in after else None,**scenarios))
    if stamp!=tuple(db_prices._db_stamp() or ()) or native_sha!=nominal_snapshot()[1]:
        raise RuntimeError('Price snapshot changed during comparison')
    security_membership._ALIASES=None;holding_paths._PATHS=None
    if aliases_sha!=security_membership.input_sha() or event_sha!=holding_paths.input_sha():
        raise RuntimeError('Identity/event evidence changed during comparison')
    if baseline!=(BASE/'output/baseline_b.json').read_bytes():raise RuntimeError('Published baseline changed')
    result={'policy':security_membership.POLICY,'comparison_scope':'membership rule only; both use current actual-consideration returns and nominal-floor bounds',
            'snapshot_stamp':list(stamp),'nominal_input_sha':native_sha,'membership_alias_input_sha':aliases_sha,
            'holding_event_input_sha':event_sha,'baseline_b_sha256':hashlib.sha256(baseline).hexdigest(),
            'complete_data_acceptance':False,'published_outputs_changed':False,'years':years}
    (BASE/'output/membership_continuity_impact.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'years':len(years),'membership_changed_years':[r['year'] for r in years if r['membership_added'] or r['membership_removed']],
                      'holdings_changed_years':[r['year'] for r in years if r['holdings_changed']]},indent=2))


if __name__=='__main__':run()
