"""Independent online current-account calculator; never rerun historical selection."""
import argparse
import copy
import datetime as dt
import hashlib
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path

import requests
from psycopg2.extras import Json

import db_sources
import live_quotes
import market_calendar
import run_guard

SITE='https://dear202606-star.github.io/trade/data/'
API='https://push2his.eastmoney.com/api/qt/stock/kline/get'
YAHOO_TPL='https://query1.finance.yahoo.com/v8/finance/chart/%s'
# 东方财富对 CI 出口 IP 间歇断连时的备用通道（与 yahoo_fetch 同策略，日线粒度）
YAHOO_CHANNELS=(
    ('direct',YAHOO_TPL),
    ('translate','https://query1-finance-yahoo-com.translate.goog/v8/finance/chart/%s'),
    ('codetabs','https://api.codetabs.com/v1/proxy?quest=https://query1.finance.yahoo.com/v8/finance/chart/%s'),
    ('allorigins','https://api.allorigins.win/raw?url=https://query1.finance.yahoo.com/v8/finance/chart/%s'),
)


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
        for attempt in range(3):
            try:
                response=requests.get(API,params=params,headers={'User-Agent':'Mozilla/5.0',
                    'Referer':'https://quote.eastmoney.com/'},timeout=25)
                response.raise_for_status();body=response.json();break
            except requests.RequestException:
                if attempt==2:raise
                time.sleep(attempt+1)
        series[adjustment]=parse_daily(body,symbol,market)
        if any(not start<=day<=end for day in series[adjustment]):raise ValueError('Source dates outside requested window')
        evidence[str(adjustment)]={'url':response.url,'payload':body}
    if set(series[0])!=set(series[1]):raise ValueError('Adjusted/raw source dates differ')
    return {'raw':series[0],'adjusted':series[1],'evidence':evidence}


def parse_daily_yahoo(payload,symbol,start,end):
    """Yahoo chart 日线解析：身份回显校验 + raw/adjclose 同构。"""
    result=(payload.get('chart') or {}).get('result') or []
    if len(result)!=1:raise ValueError('Yahoo source identity mismatch: '+symbol)
    data=result[0]
    if (data.get('meta') or {}).get('symbol','').upper()!=symbol.upper():
        raise ValueError('Yahoo source identity mismatch: '+symbol)
    ts=data.get('timestamp') or []
    quote=((data.get('indicators') or {}).get('quote') or [{}])[0]
    adj=(((data.get('indicators') or {}).get('adjclose') or [{}])[0].get('adjclose')) or []
    close=quote.get('close') or []
    if not ts or not close or len(ts)!=len(close) or (adj and len(adj)!=len(ts)):
        raise ValueError('Unexpected Yahoo daily schema: '+symbol)
    raw={};adjusted={}
    for i,stamp in enumerate(ts):
        day=dt.datetime.fromtimestamp(stamp,dt.timezone.utc).date().isoformat()
        if not start<=day<=end:raise ValueError('Yahoo source dates outside requested window: '+symbol)
        c=close[i];a=adj[i] if i<len(adj) else None
        if c is None or a is None:continue        # 停牌日两端都必须缺，最后统一校验
        raw[day]=positive(float(c));adjusted[day]=positive(float(a))
    if not raw or set(raw)!=set(adjusted):raise ValueError('Empty or divergent Yahoo daily source: '+symbol)
    return raw,adjusted


def fetch_daily_yahoo(symbol,start,end):
    """东方财富不可达时的备用日线通路（raw+adjusted，含证据）。"""
    yahoo_symbol=symbol.replace('.','-')       # BRK.B → BRK-B
    # Yahoo period1/period2 均为含边界的时间戳；美股日线 bar 的时间戳在当日
    # 13:30/14:30 UTC，因此 period1 取 start 当日零点即可精确排除窗口外 bar，
    # period2 取 end 次日零点即可包含 end 当日 bar（已实测否则引入窗口外日期）。
    period1=int(dt.datetime.fromisoformat(start+'T00:00:00+00:00').timestamp())
    period2=int(dt.datetime.fromisoformat(end+'T00:00:00+00:00').timestamp())+86400
    tail='?period1=%d&period2=%d&interval=1d&events=div%%2Csplits&includeAdjustedClose=true'%(period1,period2)
    last_error=None
    for attempt in range(4):
        name,tpl=YAHOO_CHANNELS[attempt%len(YAHOO_CHANNELS)]
        if name=='translate':url=(tpl%yahoo_symbol)+tail+'&_x_tr_sl=en&_x_tr_tl=zh-CN&_x_tr_hl=en'
        elif name in ('codetabs','allorigins'):
            import urllib.parse
            url=(tpl%yahoo_symbol)+urllib.parse.quote(tail,safe='')
        else:url=(tpl%yahoo_symbol)+tail
        try:
            response=requests.get(url,headers={'User-Agent':'Mozilla/5.0'},timeout=35)
            response.raise_for_status()
            raw,adjusted=parse_daily_yahoo(response.json(),symbol,start,end)
            return {'raw':raw,'adjusted':adjusted,'provider':'yahoo',
                    'evidence':{'yahoo':{'url':response.url,'payload':response.json()}}}
        except requests.RequestException as exc:
            last_error=exc;time.sleep(attempt+1)
    raise RuntimeError('Yahoo daily source unreachable after channel rotation: %s (%s)'%(symbol,last_error))


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


def online_prices(symbols,start,quotes):
    """Read sealed paired facts before making any external request."""
    if not symbols:return {}
    from prepare_live_ranking import normalized
    phase='intraday' if quotes['intraday'] else 'close';connection=db_sources.db();result={}
    try:
        with connection.cursor() as cursor:
            cursor.execute('''select symbol,payload,evidence_sha256 from live_ranking_context
                where symbol=any(%s) and base_year=%s and session between %s and %s
                  and (phase='close' or (session=%s and phase=%s))
                order by session,created_at''',
                (symbols,int(quotes['session'][:4]),start,quotes['session'],quotes['session'],phase))
            rows=cursor.fetchall()
    finally:connection.close()
    for symbol,body,digest in rows:
        if sha(body)!=digest:raise ValueError('Online paired-price evidence mismatch')
        item=result.setdefault(symbol,{'raw':{},'adjusted':{},'evidence':{'online_context_sha256':[]}})
        values=normalized(body)
        for day,price in body['raw'].items():
            if start<=day<=quotes['session']:
                if day in item['raw'] and not close_enough(item['raw'][day],price):
                    raise ValueError('Previously sealed native price changed: '+symbol+' '+day)
                item['raw'][day]=price;item['adjusted'][day]=values[day]
        item['evidence']['online_context_sha256'].append(digest)
    required={start,quotes['previous_session'],quotes['session']}
    return {s:p for s,p in result.items() if required<=set(p['raw'])}


def iso(day):
    value=str(day).replace('-','')
    return dt.datetime.strptime(value,'%Y%m%d').date().isoformat()


def close_enough(a,b):
    return abs(positive(a)-positive(b))<=max(.0011,abs(b)*1e-8)


def reconcile_closing_prices(prices,quotes,symbols):
    """收盘后逐标的用独立源复核主源当日收盘价，修掉单源数据污染导致的整口径停更。

    2026-11-07：B 口径因 NEM 的「Closing quote disagrees」长期停在上一交易日，
    而行情快照与 Yahoo 独立源的当日收盘价完全一致，说明是主源（东财日线 /
    封存 context）当日记录滞后或污染。过去这种情况会让整个 variant 计算失败，
    净值、次年预览、排名一起停更，且没有任何自愈路径。

    安全边界（不放宽 fail-closed）：
      · 只在收盘后（intraday=False）执行，盘中不做任何替换；
      · 仅当「独立源当日收盘价与已封存行情快照一致」时才替换主源当日值；
      · 独立源不可达 / 双源都不吻合 → 保持原值，由 calculate_equity 原有对账拒绝；
      · 只改当日 raw/adjusted，历史封存证据不动；替换动作写入 evidence 留痕。
    """
    if quotes.get('intraday'):return prices,[]
    end=quotes['session'];snapshot=quotes.get('quotes') or {};repairs=[]
    for symbol in symbols:
        expected=(snapshot.get(symbol) or {}).get('p')
        source=prices.get(symbol) or {};raw=source.get('raw') or {}
        if expected is None or end not in raw or close_enough(raw[end],expected):continue
        try:
            alt=fetch_daily_yahoo(symbol,min(raw),end)
        except Exception as exc:
            repairs.append(symbol+': 独立源不可达 '+type(exc).__name__);continue
        if end not in alt['raw'] or not close_enough(alt['raw'][end],expected):
            repairs.append(symbol+': 双源均不吻合，交由原对账拒绝');continue
        adjusted=source.setdefault('adjusted',{})
        ratio=(adjusted.get(end) or raw[end])/raw[end]   # 保留主源复权比例，避免破坏连续性
        prior=raw[end]
        raw[end]=alt['raw'][end]
        adjusted[end]=alt['raw'][end]*ratio
        source.setdefault('evidence',{})['closing_cross_check']={
            'source':'yahoo','session':end,'expected':expected,
            'primary':prior,'verified':alt['raw'][end],
            'reason':'primary daily close disagrees with sealed quote snapshot'}
        repairs.append('%s: 主源 %.4f → 独立源 %.4f（已核验）'%
                       (symbol,prior,alt['raw'][end]))
    return prices,repairs


def market_advice(quotes,calendar):
    """Existing rule: nine prior month ends plus the current month's latest SPY."""
    month=dt.date.fromisoformat(quotes['session']).replace(day=1);dates=[]
    for _ in range(9):
        close=calendar.previous(month);dates.append(close.isoformat());month=close.replace(day=1)
    connection=db_sources.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute('''select distinct on(point.key) point.key,point.value,evidence_sha256
                from live_ranking_context c cross join lateral jsonb_each_text(c.payload->'raw') point
                where symbol='SPY' and point.key=any(%s)
                order by point.key,session desc,created_at desc''',(dates,))
            rows=cursor.fetchall()
    finally:connection.close()
    values={d:positive(float(v)) for d,v,_ in rows}
    if set(values)!=set(dates):raise ValueError('Missing verified SPY month ends')
    return {'month':quotes['session'][:7],'calendar_sha256':calendar.sha,
            'calculator_sha256':equity_code_sha(),
            'closes':[{'date':d,'price':values[d]} for d in sorted(dates)],
            'source_evidence_sha256':{d:h for d,_,h in rows},'window':10}


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
                  source_as_of=quotes['ts'],fresh_until=quotes['fresh_until'],quote_intraday=quotes['intraday'],runtime_verified=True,
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
    # 先「不校验时效」读一次，让 run-guard 有机会在盘中 / 已算过场次秒退。
    # 盘中报价在 30 分钟采集节奏下必然超过 15 分钟心跳阈值，若先跑严格
    # 校验就会抛错，把本应秒退的作业变成红色失败（2026-11-10 实测）。
    quotes=live_quotes.load_snapshot(strict=False)
    if run_guard.should_skip('equity',quotes):return
    # 真要重算（收盘场次）时重新严格复核报价时效，写库前的门禁不变。
    quotes=live_quotes.load_snapshot()
    calendar=market_calendar.load()
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
    prices.update(online_prices([s for s in symbols if s not in prices],start,quotes))
    markets=provider_markets([s for s in symbols if s not in prices])
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures={pool.submit(fetch_daily,s.replace('.','_'),markets[s],start,quotes['session']):s for s in symbols if s not in prices}
        for future in as_completed(futures):
            symbol=futures[future]
            try:prices[symbol]=future.result()
            except Exception as exc:failures.append(symbol+': '+str(exc))
    # 单源当日收盘价污染会拖垮整个口径的净值/预览推进；先做独立源交叉复核。
    prices,cross_checks=reconcile_closing_prices(prices,quotes,symbols)
    for note in cross_checks:print('[closing-cross-check]',note)
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
            result.pop('market_advice_error',None)
            basis=body['equity'].get('market_advice',{})
            try:
                if (basis.get('month')!=quotes['session'][:7] or basis.get('calendar_sha256')!=calendar.sha
                        or basis.get('calculator_sha256')!=code_version):basis=market_advice(quotes,calendar)
                result['market_advice']=basis
            except Exception as exc:
                result.pop('market_advice',None)
                result['market_advice_error']=type(exc).__name__+': '+str(exc)[:120]
            used={s:prices[s] for s in {p['ticker'] for p in body['equity']['position']}|{'SPY'}}
            store(variant,'equity',quotes,result,{'anchor_sha256':digest,'root_anchor_sha256':roots[variant],
                'prices':used,'cash_distribution_proofs':cash_proofs,'calculator_sha256':code_version})
            print(variant,'equity',result['last_session'],round(result['stats']['strategy']*100,4))
        except Exception as exc:failures.append(variant+': '+str(exc))
    if failures:raise RuntimeError('Independent account failures: '+'; '.join(failures))
    run_guard.mark_done('equity',quotes)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-anchors',action='store_true')
    args=parser.parse_args()
    if args.install_anchors:install_anchors()
    else:run_equity()
