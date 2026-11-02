"""Read-only NVDA example of nominal versus future-split-adjusted $5 screening.

Only NVDA is changed in memory. Other stocks are not corrected, so the scenario
is not a proposed or fully corrected performance series. No DB/baseline writes.
"""
import copy
import json
import sys
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import pandas as pd
import db_prices
import db_ref
import variant_b as strategy

EVENTS=[
    {'month':'2021-07','ratio':4,'evidence':'https://investor.nvidia.com/files/doc_downloads/doc_faq/06/21/NVIDIA-2021-Stock-Split-FAQ.pdf'},
    {'month':'2024-06','ratio':10,'evidence':'https://www.sec.gov/Archives/edgar/data/1045810/000104581024000144/nvda-20240607.htm'},
]


def run():
    original=json.loads((BASE/'output/_prices_cache.json').read_text())
    stamp=original.pop('__stamp__')
    built=original.pop('__built_at__')
    if tuple(stamp)!=tuple(db_prices._db_stamp() or ()):
        raise RuntimeError('Full snapshot fingerprint changed; refresh before this audit')
    proposed=copy.deepcopy(original)
    for month,values in proposed['NVDA'].items():
        factor=1
        for event in EVENTS:
            if month<event['month']:
                factor*=event['ratio']
        values[1]*=factor
    members=db_ref.load_members()
    frame=pd.DataFrame([{'ticker':symbol,'start_date':a,'end_date':b}
                        for symbol,periods in members.items() for a,b in periods])
    sectors=db_ref.load_sectors()

    def scenario(bars):
        strategy._ALL_BARS=bars
        years=[]
        for year in range(2010,2026):
            picks,pool=strategy.pick_year(frame,sectors,year,
                price_lookup=lambda symbol, month: bars[symbol][month][1])
            ret,drawdown=strategy.year_perf(picks,year)
            years.append({'year':year,'holdings':[(p['ticker'],p['weight']) for p in picks],
                          'return':ret,'drawdown':drawdown,'pool':pool})
        return years

    before,after=scenario(original),scenario(proposed)
    published=json.loads((BASE/'docs/data/b/strategy_history.json').read_text())
    published={r['year']:r for r in published['years']}
    baseline_drift=[r['year'] for r in before if r['year'] in published and (
        [p[0] for p in r['holdings']]!=[p['ticker'] for p in published[r['year']]['holdings']]
        or abs(r['return']-published[r['year']]['ret'])>.005)]
    result={'scope':'NVDA only; other stocks uncorrected; not a new official backtest',
            'snapshot_stamp':stamp,'snapshot_built_at':built,'split_evidence':EVENTS,
            'current_B_vs_published_drift_years':baseline_drift,
            'examples':[{'month':month,'current_filter_price':original['NVDA'][month][1],
                         'nominal_example_price':proposed['NVDA'][month][1]}
                        for month in ['2015-12','2016-12','2020-12']],
            'changes':[{'year':a['year'],'before':a,'nvda_only_scenario':b}
                       for a,b in zip(before,after) if a['holdings']!=b['holdings']
                       or abs(a['return']-b['return'])>.0001]}
    (BASE/'output/price_filter_example.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    run()
