"""Verify archived raw closes; retain conflicts instead of choosing silently."""
import json
import math
import statistics
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import db_prices

RENAMES = {'BKNG': {'archive_symbol':'PCLN', 'through':'2018-02',
    'evidence':'https://www.sec.gov/Archives/edgar/data/1075531/000107553118000008/ex991namechangepressrelease.htm'}}


def run():
    archive = json.loads((BASE/'output/wiki_monthly_archive.json').read_text())
    sina = json.loads((BASE/'output/nominal_price_review.json').read_text())
    prices = db_prices.load_all()
    if tuple(sina['snapshot_stamp']) != tuple(db_prices._db_stamp() or ()):
        raise RuntimeError('Review snapshot changed')
    results, conflicts = {}, []
    for symbol, bars in prices.items():
        name = RENAMES.get(symbol,{}).get('archive_symbol', symbol)
        months = {m:r for m,r in archive['monthly'].get(name,{}).items()
                  if m < '2018-03' and m in bars}
        if symbol in RENAMES:
            months = {m:r for m,r in months.items() if m <= RENAMES[symbol]['through']}
        common = sorted(months)
        devs = []
        for before, after in zip(common,common[1:]):
            if (int(after[:4])-int(before[:4]))*12+int(after[5:])-int(before[5:]) != 1:
                continue
            ratio = (months[after]['adj_close']/months[before]['adj_close']) / (bars[after][0]/bars[before][0])
            devs.append(abs(math.log(ratio)))
        median = statistics.median(devs) if devs else None
        p90 = sorted(devs)[int((len(devs)-1)*.9)] if devs else None
        passed = len(devs)>=24 and median<=.005 and p90<=.02
        nominal = {m:{'date':r['date'],'close':r['close']} for m,r in months.items()} if passed else {}
        previous = sina['results'].get(symbol,{})
        if previous.get('status')=='crosscheck_pass':
            for month, row in nominal.items():
                old = previous['months'].get(month)
                if old and abs(row['close']/old['close']-1) > .005:
                    conflicts.append({'symbol':symbol,'month':month,'sina_close':old['close'],
                                      'wiki_close':row['close']})
        results[symbol] = {'status':'crosscheck_pass' if passed else 'review_required',
                           'archive_symbol':name,'overlap_returns':len(devs),
                           'median_log_deviation':median,'p90_log_deviation':p90,
                           'months':nominal}
    result = {'snapshot_stamp':sina['snapshot_stamp'],'source_url':archive['source_url'],
              'archive_sha256':archive['archive_sha256'],'verified_renames':RENAMES,
              'statuses':{s:sum(r['status']==s for r in results.values())
                          for s in ('crosscheck_pass','review_required')},
              'conflicts':conflicts,'results':results}
    (BASE/'output/wiki_nominal_review.json').write_text(json.dumps(result,separators=(',',':'))+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('results','conflicts')},indent=2))
    print('conflicting observation cells',len(conflicts))


if __name__=='__main__':
    run()
