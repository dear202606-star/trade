"""Collect exact daily-dollar windows with independent OHLCV/calendar proof."""
import argparse
import datetime as dt
import json
import math
import re
from pathlib import Path

import psycopg2.extras
import requests

import db_prices
import db_select
import liquidity_prices as liquidity

BASE=Path(__file__).resolve().parent
CACHE=BASE/'output/_liquidity_windows'
EM='https://push2his.eastmoney.com/api/qt/stock/kline/get'
SINA='https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/x/US_MinKService.getDailyK?symbol='
PAT=re.compile(r'\(\s*(\[.*\])\s*\)',re.S)


def daily_records(rows,provider,start,end):
    result={}
    for raw in rows:
        if provider=='eastmoney':
            f=raw.split(',')
            if len(f)!=11:raise ValueError('Unexpected daily quote schema')
            date=f[0];values=dict(zip(('open','close','high','low','volume','amount'),map(float,f[1:7])))
        else:
            date=raw['d']
            if not start<=date<=end:continue
            values={k:float(raw[key]) for k,key in [('open','o'),('close','c'),('high','h'),('low','l'),('volume','v')]}
        dt.date.fromisoformat(date)
        if not start<=date<=end:continue
        if date in result:raise ValueError('Duplicate daily date')
        if any(not math.isfinite(v) or v<0 for v in values.values()):raise ValueError('Invalid daily number')
        if not 0<values['low']<=min(values['open'],values['close'])<=max(values['open'],values['close'])<=values['high']:
            raise ValueError('Daily OHLC range invalid')
        result[date]=values
    return result


def fetch_daily(symbol,identity,start,end):
    secid=str(identity['market'])+'.'+symbol
    path=CACHE/(symbol+'-'+start+'-'+end+'.json')
    if path.exists():record=json.loads(path.read_text())
    else:
        response=requests.get(EM,params={'secid':secid,'klt':101,'fqt':0,
            'beg':start.replace('-',''),'end':end.replace('-',''),
            'fields1':'f1,f2,f3,f4,f5,f6','fields2':'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'},timeout=20)
        response.raise_for_status()
        if len(response.content)>4*1024**2:raise ValueError('Daily payload exceeds bound')
        record={'secid':secid,'mode':'fqt=0','source_url':response.url,'payload':response.json()}
        path.write_text(json.dumps(record,separators=(',',':')))
    data=record['payload'].get('data') or {}
    if (record.get('secid')!=secid or record.get('mode')!='fqt=0' or record['payload'].get('rc')!=0
        or data.get('code')!=symbol or data.get('market')!=identity['market']):
        raise ValueError('Provider security/request identity mismatch')
    primary=daily_records(data.get('klines') or [],'eastmoney',start,end)
    sina_path=BASE/'output/_nominal_daily'/(symbol+'.json')
    independent=json.loads(sina_path.read_text()) if sina_path.exists() else None
    if independent is None or max((r['d'] for r in independent.get('rows',[])),default='')<max(primary,default=start):
        response=requests.get(SINA+symbol,headers={'Referer':'https://finance.sina.com.cn'},timeout=20)
        response.raise_for_status()
        if len(response.content)>4*1024**2:raise ValueError('Independent daily payload exceeds bound')
        match=PAT.search(response.text)
        if not match:raise ValueError('Independent daily schema missing')
        independent={'endpoint':SINA+symbol,'rows':json.loads(match.group(1)),
                     'fetched_at':dt.datetime.now(dt.timezone.utc).isoformat()}
        sina_path.parent.mkdir(exist_ok=True);sina_path.write_text(json.dumps(independent,separators=(',',':')))
    if independent.get('endpoint')!=SINA+symbol:raise ValueError('Independent request identity mismatch')
    secondary=daily_records(independent['rows'],'sina',start,end)
    return primary,secondary,{'primary_url':record['source_url'],
        'primary_payload_sha256':liquidity.evidence_sha(record['payload']),
        'independent_url':independent['endpoint'],
        'independent_payload_sha256':liquidity.evidence_sha(independent['rows'])}


def run(symbols,months,write=False):
    if not symbols or len(symbols)>1000 or len(set(symbols))!=len(symbols):raise ValueError('Invalid symbol batch')
    if any(not re.fullmatch(r'[A-Z][A-Z0-9._-]{0,24}',s) for s in symbols):raise ValueError('Invalid symbol')
    windows=[('%d-%s'%(int(m[:4])-1,m[5:]),m) for m in sorted(set(months))]
    dates=[liquidity.window_dates(a,b) for a,b in windows]
    if not dates:raise ValueError('No windows requested')
    start=min(a for a,b in dates);end=max(b for a,b in dates)
    if not write:
        print(json.dumps({'symbols':symbols,'windows':windows,'write':False,'provider_requests':0}));return
    directory=json.loads((BASE/'output/eastmoney_security_directory.json').read_text())['records']
    def identity(symbol):
        matches=[r for r in directory if r['code']==symbol]
        if len(matches)!=1:raise ValueError('Security directory identity missing/ambiguous: '+symbol)
        return matches[0]
    CACHE.mkdir(exist_ok=True)
    prices=db_prices.load_all();stamp=tuple(db_prices._db_stamp() or ())
    if not stamp:raise RuntimeError('No selected source version')
    connection=db_select.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute('select symbol,source,basis from series_pick where symbol=any(%s)',(symbols,))
            picks={s:(source,basis) for s,source,basis in cursor.fetchall()}
    finally:connection.close()
    benchmark,independent_benchmark,calendar_proof=fetch_daily('SPY',identity('SPY'),start,end)
    if set(benchmark)!=set(independent_benchmark):raise ValueError('Independent SPY market calendars disagree')
    results=[]
    for symbol in symbols:
        try:
            primary,secondary,proof=fetch_daily(symbol,identity(symbol),start,end)
            for base,rank in windows:
                if rank not in prices.get(symbol,{}):
                    results.append({'symbol':symbol,'rank_month':rank,'status':'no_selected_month'});continue
                try:summary=liquidity.summarize_window(primary,secondary,set(benchmark),base,rank)
                except ValueError as exc:
                    results.append({'symbol':symbol,'base_month':base,'rank_month':rank,
                                    'status':'review_required','reason':str(exc)})
                    continue
                evidence={'policy':liquidity.POLICY,'symbol':symbol,'base_month':base,'rank_month':rank,
                          'summary':summary,'sources':proof,'market_calendar':calendar_proof,
                          'independent_amount_definition':'daily low/high times shares; not Sina a as actual turnover'}
                digest=liquidity.evidence_sha(evidence);calculator=liquidity.calculator_sha()
                connection=db_select.db();connection.autocommit=False
                try:
                    with connection,connection.cursor() as cursor:
                        cursor.execute('select source,basis from series_pick where symbol=%s for share',(symbol,))
                        if cursor.fetchone()!=picks.get(symbol):raise RuntimeError('Selected security/source changed')
                        cursor.execute('''insert into liquidity_windows
                         (symbol,base_month,rank_month,trading_days,primary_amount,independent_lower,independent_upper,
                          evidence_sha256,calculator_sha256,evidence,verified)
                         values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true) on conflict do nothing''',
                         (symbol,base,rank,summary['trading_days'],summary['primary_amount'],summary['independent_lower'],
                          summary['independent_upper'],digest,calculator,psycopg2.extras.Json(evidence)))
                        inserted=cursor.rowcount
                finally:connection.close()
                results.append({'symbol':symbol,'rank_month':rank,'status':'stored','inserted':inserted,
                                'evidence_sha256':digest,'summary':summary})
        except ValueError as exc:results.append({'symbol':symbol,'status':'review_required','reason':str(exc)})
        except requests.RequestException as exc:
            results.append({'symbol':symbol,'status':'network_failure','type':type(exc).__name__});break
        (BASE/'output/liquidity_window_ingest.json').write_text(json.dumps({'write':True,'results':results},indent=2)+'\n')
        print(symbol,results[-1]['status'],flush=True)
    if stamp!=tuple(db_prices._db_stamp() or ()):raise RuntimeError('Selected inputs changed during collection')
    result={'write':True,'snapshot_stamp':list(stamp),'results':results,
            'prices_sources_locks_and_published_outputs_changed':False}
    (BASE/'output/liquidity_window_ingest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'windows_stored':sum(r['status']=='stored' for r in results),'results':len(results)}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--symbols',required=True)
    parser.add_argument('--months',required=True);parser.add_argument('--write',action='store_true')
    args=parser.parse_args();run(args.symbols.split(','),args.months.split(','),args.write)
