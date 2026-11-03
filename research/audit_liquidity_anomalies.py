"""Explain rejected daily turnover using saved source evidence; never repair it."""
import collections
import json
import sys
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from collect_liquidity_windows import daily_records
from liquidity_prices import window_dates


def run():
    receipt=json.loads((BASE/'output/liquidity_ingest_2025-12.json').read_text())
    rows=[]
    for result in receipt['results']:
        if result['status']!='review_required':continue
        symbol=result['symbol'];month=result.get('rank_month','2025-12')
        base=result.get('base_month','%d-%s'%(int(month[:4])-1,month[5:]))
        start,end=window_dates(base,month)
        path=BASE/'output/_liquidity_windows'/(symbol+'-'+start+'-'+end+'.json')
        row=dict(result,amount_conflict_days=[])
        if path.exists():
            payload=json.loads(path.read_text())['payload']
            daily=daily_records((payload.get('data') or {}).get('klines') or [],'eastmoney',start,end)
            independent_path=BASE/'output/_nominal_daily'/(symbol+'.json')
            independent_raw=json.loads(independent_path.read_text())['rows'] if independent_path.exists() else []
            independent_dates={r['d'] for r in independent_raw if start<=r['d']<=end}
            try:independent=daily_records(independent_raw,'sina',start,end)
            except ValueError as exc:
                independent={};row['independent_parse_error']=str(exc)
            calendar_path=path.with_name('SPY-'+start+'-'+end+'.json')
            if calendar_path.exists():
                calendar_payload=json.loads(calendar_path.read_text())['payload']['data']['klines']
                calendar=set(daily_records(calendar_payload,'eastmoney',start,end))
                row['calendar_differences']={
                    'primary_missing':sorted(calendar-set(daily)),
                    'primary_extra':sorted(set(daily)-calendar),
                    'independent_missing':sorted(calendar-independent_dates),
                    'independent_extra':sorted(independent_dates-calendar)}
            for date,quote in sorted(daily.items()):
                lower=quote['low']*quote['volume']*.99
                upper=quote['high']*quote['volume']*1.01
                if not lower<=quote['amount']<=upper:
                    row['amount_conflict_days'].append(dict(date=date,**quote,
                        independent_ohlcv=independent.get(date),
                        implied_average=quote['amount']/quote['volume'] if quote['volume'] else None))
        rows.append(row)
    report={'scope':'2025-12 rejected windows; diagnosis only, no raw data or database changes',
            'reason_counts':dict(collections.Counter(r['reason'] for r in rows)),
            'rejected_windows':len(rows),'rows':rows}
    (BASE/'output/liquidity_anomaly_review.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='rows'},indent=2))


if __name__=='__main__':run()
