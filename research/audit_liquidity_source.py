"""Probe daily dollar turnover without publishing signals or modifying the DB."""
import datetime as dt
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path

import requests

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from collect_eastmoney_nominal import URL


def run():
    directory=json.loads((BASE/'output/eastmoney_security_directory.json').read_text())
    results=[]
    cache=BASE/'output/_liquidity_probe';cache.mkdir(exist_ok=True)
    for symbol,year in [('NVDA',2025),('AAPL',2025),('PLTR',2024),('VST',2024)]:
        identities=[r for r in directory['records'] if r['code']==symbol]
        if len(identities)!=1:raise RuntimeError('Ambiguous provider security: '+symbol)
        market=identities[0]['market']
        params={'secid':str(market)+'.'+symbol,'klt':101,'fqt':0,
                'beg':str(year)+'0101','end':str(year)+'1231',
                'fields1':'f1,f2,f3,f4,f5,f6','fields2':'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'}
        response=requests.get(URL,params=params,timeout=20)
        response.raise_for_status()
        if len(response.content)>2*1024**2:raise ValueError('Daily payload exceeds bound')
        payload=response.json();data=payload.get('data') or {}
        if payload.get('rc')!=0 or data.get('code')!=symbol or data.get('market')!=market:
            raise ValueError('Daily security identity mismatch')
        raw=response.content;digest=hashlib.sha256(raw).hexdigest()
        (cache/(symbol+'-'+str(year)+'.json')).write_bytes(raw)
        dates=set();amounts=[];violations=[];closes={}
        for line in data.get('klines') or []:
            fields=line.split(',')
            if len(fields)!=11:raise ValueError('Unexpected daily schema')
            date=dt.date.fromisoformat(fields[0])
            if date.year!=year or date in dates:raise ValueError('Daily period/duplicate mismatch')
            dates.add(date)
            op,close,high,low,volume,amount=map(float,fields[1:7])
            if any(not math.isfinite(v) or v<=0 for v in (op,close,high,low,volume,amount)):
                raise ValueError('Invalid daily value')
            if not low<=min(op,close)<=max(op,close)<=high:raise ValueError('Invalid OHLC range')
            if not low*volume*.99<=amount<=high*volume*1.01:violations.append(str(date))
            amounts.append(amount);closes[str(date)]=close
        if not dates:raise ValueError('No daily observations')
        independent_path=BASE/'output/_nominal_daily'/(symbol+'.json')
        independent=None
        if independent_path.exists():
            independent_rows={r['d']:r for r in json.loads(independent_path.read_text())['rows']
                              if r['d'].startswith(str(year)+'-')}
            provider_dates={str(d) for d in dates}
            differences=[]
            daily={line.split(',')[0]:line.split(',') for line in data['klines']}
            for date in sorted(provider_dates&set(independent_rows)):
                amount=float(independent_rows[date].get('a') or 0)
                if math.isfinite(amount) and amount>0:
                    differences.append(abs(float(daily[date][6])/amount-1))
            independent={'source':'sina_cached_daily',
                         'payload_sha256':hashlib.sha256(independent_path.read_bytes()).hexdigest(),
                         'days':len(independent_rows),'matched_amount_days':len(differences),
                         'missing_dates':sorted(set(independent_rows)-provider_dates),
                         'extra_dates':sorted(provider_dates-set(independent_rows)),
                         'median_amount_relative_deviation':statistics.median(differences) if differences else None,
                         'max_amount_relative_deviation':max(differences) if differences else None}
        complete=bool(independent and independent['days']>=240
                      and not independent['missing_dates'] and not independent['extra_dates']
                      and independent['matched_amount_days']==len(dates))
        results.append({'symbol':symbol,'year':year,'market':market,'days':len(dates),
                        'independent_daily_crosscheck':independent,
                        'calendar_matches_independent_full_year':complete,
                        'status':'calendar_crosschecked' if complete else 'incomplete_or_unproved_calendar',
                        'first_date':str(min(dates)),'last_date':str(max(dates)),
                        'last_close':closes[str(max(dates))],
                        'average_daily_dollar_amount':sum(amounts)/len(amounts),
                        'amount_outside_daily_price_times_shares':violations,
                        'payload_sha256':digest,'source_url':response.url})
    result={'documentation':'https://akshare.akfamily.xyz/data/stock/stock_quote.html',
            'scope':'Source probe only; completeness and independent turnover checks still required',
            'database_and_published_outputs_changed':False,'results':results}
    (BASE/'output/liquidity_source_probe.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':run()
