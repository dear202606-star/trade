"""Review primary AV replacements on one selected snapshot, before applying picks.

Use the same legacy price predicate in both scenarios to isolate source/return
changes from the separately audited nominal-price rule. Never write picks or
published history. Original provider rows remain preserved under their sources.
"""
import json
import sys
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import pandas as pd
import db_select
import db_prices
import db_ref
import variant_b as strategy

EXPECTED={'HES','ALXN','XLNX'}
EVIDENCE={
    'HES':'https://www.sec.gov/Archives/edgar/data/93410/000095014225001949/eh250652625_8k.htm',
    'ALXN':'https://www.astrazeneca.com/media-centre/press-releases/2021/acquisition-of-alexion-completed.html',
    'XLNX':'https://www.amd.com/en/newsroom/press-releases/2022-2-14-amd-completes-acquisition-of-xilinx.html',
}


def run():
    original=db_prices.load_all()
    stamp=db_prices._db_stamp()
    connection=db_select.db()
    replacements={}
    try:
        with connection.cursor() as cursor:
            proposals=db_select.compute_picks(cursor)
            cursor.execute('select symbol,source from series_pick')
            current=dict(cursor.fetchall())
            changes=[(s,source,basis) for s,source,basis in proposals if current.get(s)!=source]
            if any(s not in EXPECTED or source!='alphavantage' or basis!='total_return'
                   for s,source,basis in changes):
                raise RuntimeError('Unexpected proposed source change; review separately')
            for symbol,source,basis in changes:
                cursor.execute("""select exists(select 1 from locked_symbols where symbol=%s),
                                  exists(select 1 from series_pick where symbol=%s and frozen),
                                  exists(select 1 from series_quarantine where symbol=%s)""",(symbol,symbol,symbol))
                if any(cursor.fetchone()):
                    raise RuntimeError('Locked, frozen, or quarantined source target')
                cursor.execute("""select month,adj_close,raw_close from prices_monthly
                                  where symbol=%s and source='alphavantage' order by month""",(symbol,))
                bars={m:(float(a),float(r)) for m,a,r in cursor.fetchall() if m>='2008-01'}
                if not bars:
                    raise RuntimeError('Primary source has no formal rows')
                replacements[symbol]=bars
    finally:
        connection.close()
    members=db_ref.load_members()
    frame=pd.DataFrame([{'ticker':s,'start_date':a,'end_date':b}
                        for s,periods in members.items() for a,b in periods])
    sectors=db_ref.load_sectors()
    def scenario(prices):
        strategy._ALL_BARS=prices
        years=[]
        for year in range(2010,2027):
            def fixed_floor(symbol,month):
                return original.get(symbol,{}).get(month,(None,None))[1] or 0
            picks,_=strategy.pick_year(frame,sectors,year,price_lookup=fixed_floor)
            result={'year':year,'holdings':[(p['ticker'],p['weight']) for p in picks]}
            if year<=2025:
                # No carry-forward across an acquisition is used as evidence.
                missing=[{'symbol':p['ticker'],'month':'%d-%02d'%(year,m)}
                         for p in picks for m in range(1,13)
                         if '%d-%02d'%(year,m) not in prices[p['ticker']]]
                if missing:
                    result['return_status']='missing_actual_holding_months'
                    result['missing_holding_months']=missing
                else:
                    result['return'],result['drawdown']=strategy.year_perf(picks,year)
            years.append(result)
        return years
    before=scenario(original)
    after=scenario(dict(original,**replacements))
    if tuple(stamp or ())!=tuple(db_prices._db_stamp() or ()):
        raise RuntimeError('Selected input changed during comparison')
    report={'scope':'B source/return changes only; both scenarios use identical legacy floor predicate',
            'snapshot_stamp':stamp,'proposed':changes,'issuer_evidence':EVIDENCE,
            'old_sources_retained':True,'locks_and_published_baseline_changed':False,
            'replacement_ranges':{s:[min(b),max(b),len(b)] for s,b in replacements.items()},
            'before':before,'after':after,
            'holding_changes':[a['year'] for a,b in zip(before,after) if a['holdings']!=b['holdings']],
            'return_changes':[{'year':a['year'],'before':a.get('return'),'after':b.get('return')}
                for a,b in zip(before,after) if a.get('return') is not None and b.get('return') is not None
                and abs(a['return']-b['return'])>.0001]}
    (BASE/'output/av_source_impact.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('before','after')},indent=2))


if __name__=='__main__':
    run()
