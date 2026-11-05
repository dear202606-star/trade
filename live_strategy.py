"""Independent online current-account calculator; never rerun historical selection."""
import argparse
import copy
import datetime as dt
import hashlib
import json
import math
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path

import requests
from psycopg2.extras import Json

import db_sources
import live_quotes
import market_calendar

SITE='https://dear202606-star.github.io/trade/data/'
API='https://push2his.eastmoney.com/api/qt/stock/kline/get'


def sha(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def equity_code_sha():
    base=Path(__file__).resolve().parent
    return sha({name:hashlib.sha256((base/name).read_bytes()).hexdigest()
                for name in ('live_strategy.py','live_cash_dividends.py','market_calendar.py')})


def positive(value):
    if type(value) not in (float,int) or not math.isfinite(value) or value<=0:
        raise ValueError('Invalid positive financial value')
    return value


def parse_daily(payload,symbol,market):
    data=payload.get('data') or {}
    if payload.get('rc')!=0 or data.get('code')!=symbol or data.get('market')!=market:
        raise ValueError('Daily source identity mismatch: '+symbol)
    result={}
    for line in data.get('klines') or []:
        fields=line.split(',')
        if len(fields)!=11:raise ValueError('Unexpected daily source schema')
        day=dt.date.fromisoformat(fields[0]).isoformat()
        if day in result:raise ValueError('Duplicate daily source date')
        result[day]=positive(float(fields[2]))
    if not result:raise ValueError('Empty daily source: '+symbol)
    return result


def fetch_daily(symbol,market,start,end):
    evidence={};series={}
    for adjustment in (0,1):
        params={'secid':str(market)+'.'+symbol,'klt':101,'fqt':adjustment,
                'beg':start.replace('-',''),'end':end.replace('-',''),
                'fields1':'f1,f2,f3,f4,f5,f6',
                'fields2':'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'}
        response=requests.get(API,params=params,timeout=25)
        response.raise_for_status();body=response.json()
        series[adjustment]=parse_daily(body,symbol,market)
        evidence[str(adjustment)]={'url':response.url,'payload':body}
    if set(series[0])!=set(series[1]):raise ValueError('Adjusted/raw source dates differ')
    return {'raw':series[0],'adjusted':series[1],'evidence':evidence}


def provider_markets(symbols):
    if not symbols:return {}
    connection=db_sources.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""select record->>'code',(record->>'market')::integer
                from sys_event e cross join lateral jsonb_array_elements(e.metrics->'proof'->'records') record
                where e.id=(select max(id) from sys_event where source='live_security_directory' and status='pass')
                  and record->>'code'=any(%s)""",([s.replace('.','_') for s in symbols],))
            rows=cursor.fetchall()
        result={}
        for symbol in symbols:
            matches=[market for code,market in rows if code==symbol.replace('.','_')]
            if len(matches)!=1:raise ValueError('No unique online provider identity: '+symbol)
            result[symbol]=matches[0]
        return result
    finally:connection.close()


def iso(day):
    value=str(day).replace('-','')
    return dt.datetime.strptime(value,'%Y%m%d').date().isoformat()


def close_enough(a,b):
    return abs(positive(a)-positive(b))<=max(.0011,abs(b)*1e-8)


def validate_anchor(body):
    equity,signal=body['equity'],body['signal']
    positions=equity['position'];official=signal['official']
    weights={p['ticker']:positive(p['weight']) for p in positions}
    if (not weights or len(weights)!=len(positions) or len({p['ticker'] for p in official})!=len(official)
            or abs(sum(weights.values())-1)>1e-6
            or weights!={p['ticker']:p['weight'] for p in official}):
        raise ValueError('Official holdings and account anchor differ')
    day=iso(equity['last_session'])
    for p in positions:
        if iso(p['last_date'])!=day:raise ValueError('Mixed anchor sessions')
        for key in ('entry','entry_raw','last','last_raw'):positive(p[key])
    wealth=sum(p['weight']*p['last']/p['entry'] for p in positions)
    positive(1+equity['stats']['strategy']);positive(1+equity['stats']['benchmark'])
    if abs(wealth-(1+equity['stats']['strategy']))>1e-5:
        raise ValueError('Anchor holdings do not reconcile to net value')
    points=equity['points']
    dates=[iso(point['d']) for point in points]
    if dates!=sorted(set(dates)):raise ValueError('Duplicate or unordered account curve')
    for point in points:positive(point['s']);positive(point['b'])
    if not points or iso(points[-1]['d'])!=day or abs(points[-1]['s']-wealth)>1e-5:
        raise ValueError('Anchor curve does not reconcile')
    return day


def calculate_equity(anchor,quotes,prices,calendar,cash_proofs=None):
    from live_cash_dividends import check_adjustment
    start=validate_anchor(anchor);end=quotes['session']
    if end<start:raise ValueError('Quote precedes account anchor')
    previous=quotes['previous_session'];equity=copy.deepcopy(anchor['equity'])
    original_positions=copy.deepcopy(equity['position'])
    required={start,previous,end}
    # The source must supply every intervening exchange close; never forward fill.
    day=dt.date.fromisoformat(start)
    sessions=[]
    while day<=dt.date.fromisoformat(end):
        if calendar.bounds(day):sessions.append(day.isoformat())
        day+=dt.timedelta(days=1)
    if not quotes.get('intraday'):required.update(sessions)
    symbols=[p['ticker'] for p in equity['position']]+['SPY']
    for symbol in symbols:
        source=prices[symbol];raw,adj=source['raw'],source['adjusted'];q=quotes['quotes'][symbol]
        if not required<=set(raw):raise ValueError('Missing required daily prices: '+symbol)
        if not close_enough(raw[previous],q['pc']):raise ValueError('Previous close disagrees: '+symbol)
        if not quotes.get('intraday') and not close_enough(raw[end],q['p']):
            raise ValueError('Closing quote disagrees: '+symbol)
        # An unreviewed unit change must not be hidden in a source adjustment.
        days=sorted(required)
        for before,after in zip(days,days[1:]):
            factor=(adj[after]/raw[after])/(adj[before]/raw[before])
            if abs(factor-1)>.0001:
                check_adjustment(symbol,after,raw[before],raw[after],factor,cash_proofs or {})
    for p in equity['position']:
        if not close_enough(prices[p['ticker']]['raw'][start],p['last_raw']):
            raise ValueError('Source cannot bridge stored anchor: '+p['ticker'])
    def value(symbol,day):
        source=prices[symbol]
        ratio=source['adjusted'][day]/source['adjusted'][start]
        return ratio*quotes['quotes'][symbol]['p']/source['raw'][day] if day==end and quotes.get('intraday') else ratio
    def wealth(day):
        return sum(p['weight']*p['last']/p['entry']*value(p['ticker'],day) for p in original_positions)
    prior=wealth(previous);current=wealth(end)
    for day in sessions:
        if day<=start or (day==end and quotes.get('intraday')):continue
        equity['points'].append({'d':day.replace('-',''),'s':wealth(day),
            'b':(1+equity['stats']['benchmark'])*value('SPY',day)})
    settled=previous if quotes.get('intraday') else end
    equity['live_position']=copy.deepcopy(original_positions)
    for p in equity['live_position']:
        p['last']*=value(p['ticker'],end)
        p['last_raw']=quotes['quotes'][p['ticker']]['p'];p['last_date']=end.replace('-','')
    for p in equity['position']:
        p['last']*=value(p['ticker'],settled)
        p['last_raw']=prices[p['ticker']]['raw'][settled]
        p['last_date']=settled.replace('-','')
    settled_value=wealth(settled)
    equity['last_session']=settled.replace('-','')
    equity['stats']['strategy']=settled_value-1
    equity['stats']['benchmark']=(1+anchor['equity']['stats']['benchmark'])*value('SPY',settled)-1
    equity['stats']['excess']=equity['stats']['strategy']-equity['stats']['benchmark']
    peak=0;drawdown=0
    for point in equity['points']:
        peak=max(peak,point['s']);drawdown=min(drawdown,point['s']/peak-1)
    equity['stats'].update(mdd=drawdown,days=len(equity['points']))
    equity.update(updated=quotes['ts_local'],quote_version=quotes['version'],
                  source_as_of=quotes['ts'],runtime_verified=True,
                  today={'pct':current/prior-1,'per_dollar':current-prior,
                         'benchmark_factor':value('SPY',end)/value('SPY',previous),
                         'session':end,'quote_version':quotes['version']})
    return equity


def install_anchors():
    """One-time migration of the published account, not historical acceptance."""
    connection=db_sources.db()
    try:
        with connection.cursor() as cursor:
            for variant,prefix in (('A',''),('B','b/')):
                cursor.execute('select payload from live_strategy_anchor where variant=%s and hold_year=2026',(variant,))
                if cursor.fetchone():continue
                body={}
                for name in ('equity','signal'):
                    r=requests.get(SITE+prefix+name+'.json',timeout=25);r.raise_for_status();body[name]=r.json()
                validate_anchor(body)
                body['migration']={'source':SITE+prefix,'historical_strategy_acceptance':False}
                cursor.execute('insert into live_strategy_anchor(variant,hold_year,payload,evidence_sha256) values(%s,%s,%s,%s)',
                    (variant,2026,Json(body),sha(body)))
        connection.commit()
    finally:connection.close()


def store(variant,kind,quotes,payload,evidence):
    digest=sha(evidence);connection=db_sources.db()
    connection.autocommit=False
    try:
        with connection.cursor() as cursor:
            cursor.execute('select evidence_sha256 from live_quote_snapshot where singleton for share')
            if cursor.fetchone()[0]!=quotes['version']:raise RuntimeError('Quote advanced during calculation; retry next run')
            cursor.execute('''insert into live_strategy_current(variant,kind,source_as_of,quote_version,input_sha256,payload,evidence)
                values(%s,%s,%s,%s,%s,%s,%s) on conflict(variant,kind) do update set
                source_as_of=excluded.source_as_of,quote_version=excluded.quote_version,
                input_sha256=excluded.input_sha256,payload=excluded.payload,evidence=excluded.evidence,created_at=now()''',
                (variant,kind,quotes['ts'],quotes['version'],digest,Json(payload),Json(evidence)))
            if not quotes['intraday']:
                financial=copy.deepcopy(payload);financial.pop('quote_version',None)
                if 'today' in financial:financial['today'].pop('quote_version',None)
                financial_sha=sha({'financial':financial,'calculator':evidence['calculator_sha256'],
                                   'anchor':evidence.get('root_anchor_sha256')})
                cursor.execute('''insert into live_strategy_results(variant,kind,source_as_of,quote_version,
                    input_sha256,payload,evidence,financial_sha256) values(%s,%s,%s,%s,%s,%s,%s,%s)
                    on conflict do nothing''',
                    (variant,kind,quotes['ts'],quotes['version'],digest,Json(payload),Json(evidence),financial_sha))
        connection.commit()
    finally:connection.close()


def run_equity():
    from live_cash_dividends import load_proofs,spy_distributions,SSGA
    quotes=live_quotes.load_snapshot();calendar=market_calendar.load()
    if calendar.sha!=quotes['calendar_sha256']:raise RuntimeError('Exchange calendar advanced; retry current quote')
    connection=db_sources.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute('select distinct on(variant) variant,payload,evidence_sha256 from live_strategy_anchor where hold_year=%s order by variant,revision desc',(int(quotes['session'][:4]),))
            anchors=cursor.fetchall()
            cursor.execute("select variant,payload,quote_version,evidence from live_strategy_current where kind='equity'")
            previous_results={v:(p,q,e) for v,p,q,e in cursor.fetchall()}
        cash_proofs=load_proofs(connection)
    finally:connection.close()
    if {r[0] for r in anchors}!={'A','B'}:raise RuntimeError('Missing online annual account anchors')
    code_version=equity_code_sha()
    roots={v:digest for v,_,digest in anchors}
    def current(prior,root):
        return (prior and prior[1]==quotes['version'] and prior[2].get('calculator_sha256')==code_version
                and prior[2].get('root_anchor_sha256')==root)
    if all(current(previous_results.get(v),digest) for v,_,digest in anchors):
        print('Both account results already match this online quote; no collection');return
    active=[]
    for variant,body,digest in anchors:
        if sha(body)!=digest:raise RuntimeError('Online account anchor hash mismatch')
        prior=previous_results.get(variant)
        if current(prior,digest):continue
        if prior and prior[2].get('root_anchor_sha256')==digest:
            body={**body,'equity':prior[0]};digest=sha(body)
        active.append((variant,body,digest))
    anchors=active
    start=min([quotes['previous_session']]+[validate_anchor(body) for _,body,_ in anchors])
    symbols=sorted({'SPY'}|{p['ticker'] for _,b,_ in anchors for p in b['equity']['position']})
    prices={};failures=[]
    for variant,_,_ in anchors:
        prior=previous_results.get(variant)
        if prior and prior[1]==quotes['version']:
            prices.update(prior[2].get('prices',{}))
    markets=provider_markets([s for s in symbols if s not in prices])
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures={pool.submit(fetch_daily,s.replace('.','_'),markets[s],start,quotes['session']):s for s in symbols if s not in prices}
        for future in as_completed(futures):
            symbol=futures[future]
            try:prices[symbol]=future.result()
            except Exception as exc:failures.append(symbol+': '+str(exc))
    # The benchmark publishes exact ordinary distribution amounts in its free workbook.
    spy=prices.get('SPY')
    if spy:
        dates=sorted(spy['raw'])
        if any(abs((spy['adjusted'][b]/spy['raw'][b])/(spy['adjusted'][a]/spy['raw'][a])-1)>.0001
               for a,b in zip(dates,dates[1:])):
            response=requests.get(SSGA,timeout=25);response.raise_for_status()
            cash_proofs['SPY']=spy_distributions(response.content,set(dates))
    for variant,body,digest in anchors:
        try:
            if sha(body)!=digest:raise RuntimeError('Online account anchor hash mismatch')
            result=calculate_equity(body,quotes,prices,calendar,cash_proofs)
            used={s:prices[s] for s in {p['ticker'] for p in body['equity']['position']}|{'SPY'}}
            store(variant,'equity',quotes,result,{'anchor_sha256':digest,'root_anchor_sha256':roots[variant],
                'prices':used,'cash_distribution_proofs':cash_proofs,'calculator_sha256':code_version})
            print(variant,'equity',result['last_session'],round(result['stats']['strategy']*100,4))
        except Exception as exc:failures.append(variant+': '+str(exc))
    if failures:raise RuntimeError('Independent account failures: '+'; '.join(failures))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-anchors',action='store_true')
    args=parser.parse_args()
    if args.install_anchors:install_anchors()
    else:run_equity()
