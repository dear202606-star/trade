"""Publish current quotes to the online singleton; never process historical prices.

--as-of 只用于「GitHub 把收盘任务延迟了数小时」的补跑：它把**逻辑时间**
固定到目标交易日收盘后 17 分钟，于是日历校验、成分股参考日、报价时间戳
判定全部按该场次执行；数据源仍是在线挂牌的当前报价（盘前仍返回上一场次
收盘价，不读取任何历史行情）。collected_at 始终用真实时间留审计证据。
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import re
import time
from zoneinfo import ZoneInfo

import db_ref
import db_sources
import quotes
import market_calendar

UTC = dt.timezone.utc
ET = ZoneInfo('America/New_York')
SOURCE_TIME = re.compile(r'([A-Z][a-z]{2}) (\d{1,2}) (\d{2}:\d{2}[AP]M) (EST|EDT)')
MONTHS = {name:i for i,name in enumerate(
    ('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'),1)}

# 在线挂牌接口只保留「最近一个场次」的报价；超过约 20 小时后其字段
# 已被下一场次改写（实测盘前 SPY 前收价被写成现价），继续补采只会反复
# 失败并触发无效自愈。收紧到 20 小时 = 当晚延迟 + 次日盘前的兜底窗口。
MAX_BACKFILL_HOURS = 20
MAX_CLOSING_AGE_MIN = 12 * 60  # 与 trading_window 的延迟容忍上限一致


def logical_time(spec=None, now=None):
    """解析 --as-of 为逻辑时间；任何可疑输入一律 fail-closed 抛错。

    默认（spec 为空）返回真实时间，正常路径行为完全不变。
    """
    real = now or dt.datetime.now(UTC)
    if not spec:
        return real
    as_of = dt.datetime.fromisoformat(str(spec).replace('Z', '+00:00'))
    if as_of.tzinfo is None:
        raise ValueError('as-of 缺少时区')
    as_of = as_of.astimezone(UTC)
    if as_of > real + dt.timedelta(minutes=5):
        raise ValueError('as-of 不能是未来时刻')
    if real - as_of > dt.timedelta(hours=MAX_BACKFILL_HOURS):
        raise ValueError('as-of 超出 %d 小时补采上限' % MAX_BACKFILL_HOURS)
    import trading_window
    cal = trading_window.load_calendar()
    info = trading_window.session_info(as_of, cal)
    if not info['trading_day']:
        raise ValueError('as-of 不是已复核交易日')
    age = (as_of - info['close']).total_seconds() / 60
    if age < 0 or age > MAX_CLOSING_AGE_MIN:
        raise ValueError('as-of 必须落在收盘后 0~%d 分钟窗口' % MAX_CLOSING_AGE_MIN)
    return as_of


def read_snapshot(cursor):
    cursor.execute('select source_as_of,collected_at,evidence_sha256,payload from live_quote_snapshot where singleton')
    row=cursor.fetchone()
    if row is None:raise RuntimeError('在线报价快照不存在')
    result=dict(zip(('source_as_of','collected_at','evidence_sha256','payload'),row))
    cursor.execute("""select finished_at,metrics from sys_event where kind='fetch'
        and source='live_quotes' and status='ok' order by finished_at desc,id desc limit 1""")
    event=cursor.fetchone()
    return result,dict(zip(('finished_at','metrics'),event)) if event else None


def load_snapshot(now=None, strict=True, *, closing_only=False):
    """Read one consistent online quote/calendar/worker version; no file fallback.

    strict=False 只读当前 payload，供计算作业先检查同版本是否已成功。
    新版本始终用 strict=True 复核报价、交易所场次和采集回执后再计算。
    """
    from watchdog_checks import quote_problems
    now=now or dt.datetime.now(UTC)
    connection=db_sources.db()
    try:
        connection.set_session(isolation_level='REPEATABLE READ',readonly=True)
        with connection.cursor() as cursor:
            cursor.execute("set local statement_timeout='20s'")
            if not strict:
                row,_=read_snapshot(cursor)
                return row['payload']
            cursor.execute('select calendar,evidence_sha256 from v_market_calendar')
            rows=cursor.fetchall()
            if len(rows)!=1:raise RuntimeError('No unique reviewed online exchange calendar')
            calendar=market_calendar.Calendar(*rows[0])
            row,heartbeat=read_snapshot(cursor)
            problems=quote_problems(row,heartbeat,calendar,now,closing_only=closing_only)
            if problems:raise RuntimeError('在线报价未通过核验：'+'；'.join(problems))
            return row['payload']
    finally:
        connection.close()


def source_time(fields, now):
    """Use the explicitly zoned regular-session quote time, not collection time."""
    match = SOURCE_TIME.fullmatch(fields[25].strip())
    if not match:
        raise ValueError('Missing regular-session source timestamp')
    month,day,clock,zone = match.groups()
    year = int(fields[29])
    if year not in (now.year-1,now.year):
        raise ValueError('Unreviewed quote year')
    local = dt.datetime.strptime('%d-%02d-%02d %s'%(year,MONTHS[month],int(day),clock),
                                 '%Y-%m-%d %I:%M%p')
    offset = -5 if zone=='EST' else -4
    aware = local.replace(tzinfo=dt.timezone(dt.timedelta(hours=offset)))
    if aware.astimezone(ET).utcoffset() != aware.utcoffset():
        raise ValueError('Source timezone disagrees with exchange daylight saving time')
    stamp = aware.astimezone(UTC)
    if stamp > now+dt.timedelta(minutes=5):
        raise ValueError('Future source quote')
    return stamp


def build_snapshot(tickers, raw, fx, now, calendar=None, reference=None, supplements=None):
    if now.tzinfo is None or not tickers or len(set(tickers)) != len(tickers):
        raise ValueError('Invalid snapshot request')
    if not isinstance(fx,(int,float)) or not math.isfinite(fx) or not 5<fx<10:
        raise ValueError('Missing or invalid source exchange rate')
    parsed = {}
    for symbol in tickers:
        fields = raw.get('gb_'+symbol.replace('.','$').lower())
        if fields is None:
            continue
        quote = quotes.parse_us(fields)
        if quote is None:
            continue
        try:
            stamp = source_time(fields,now)
        except (ValueError,KeyError,IndexError,TypeError):
            continue
        parsed[symbol] = (quote,stamp)
    for symbol,value in (supplements or {}).items():
        if symbol not in tickers or symbol=='SPY':raise ValueError('Invalid supplemental quote identity')
        parsed[symbol]=value
    if 'SPY' not in parsed:
        raise ValueError('No verified SPY source timestamp')
    as_of = parsed['SPY'][1]
    session = as_of.astimezone(ET).date()
    calendar = calendar or market_calendar.load()
    state = calendar.state(now)
    if as_of<state['minimum_source_time']:
        raise ValueError('Benchmark source is older than the reviewed exchange session permits')
    intraday = state['intraday'] and session==state['session']
    values = {}
    for symbol,(quote,stamp) in parsed.items():
        if stamp.astimezone(ET).date() != session:
            continue
        if stamp<state['minimum_source_time']:
            continue
        values[symbol] = dict(quote,as_of=stamp.isoformat())
    if len(values)<.98*len(tickers):
        raise ValueError('Incomplete same-session quote coverage: %d/%d'%(len(values),len(tickers)))
    payload = {'ts':as_of.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'fresh_until':min(calendar.source_deadline(parsed[s][1]) for s in values).isoformat(),
        'ts_local':as_of.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M'),
        'session':session.isoformat(),'intraday':intraday,'fx':fx,
        'previous_session':calendar.previous(session).isoformat(),'calendar_sha256':calendar.sha,
        'count':len(values),'universe':len(tickers),'coverage':len(values)/len(tickers),
        'ok':True,'stale':[s for s in tickers if s not in values], 'quotes':values}
    if reference is not None:payload['reference']=reference
    sha = hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),
                                    allow_nan=False).encode()).hexdigest()
    payload['version'] = sha
    return payload,sha


def held_symbols(year):
    connection=db_sources.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute('''select payload from (
                select distinct on(variant) variant,payload from live_strategy_anchor
                where hold_year<=%s order by variant,hold_year desc,revision desc
                ) anchors''',(year,))
            return {p['ticker'] for (body,) in cursor.fetchall() for p in body['equity']['position']}
    finally:connection.close()


def universe(now):
    date = now.astimezone(ET).date().isoformat()
    active,_,version = db_ref.load_market_reference(date)
    if len(active)<450:
        raise RuntimeError('Online current membership is incomplete')
    return sorted(active | set(quotes.EXTRA) | held_symbols(int(date[:4]))),{'as_of':date,'version':version}


def collect(now):
    calendar = market_calendar.load()
    calendar.state(now)
    tickers,reference = universe(now)
    raw = {}
    for start in range(0,len(tickers),quotes.BATCH):
        batch = tickers[start:start+quotes.BATCH]
        # Any transport failure aborts the publication; no previous file read.
        raw.update(quotes.sina_batch(['gb_'+s.replace('.','$').lower() for s in batch]))
        time.sleep(.2)
    fx = quotes.fetch_fx()
    # 逻辑时间贯穿日历 / 成分股 / 报价时间戳校验；补跑时由 --as-of 固定到
    # 目标场次，正常运行时 now 就是真实时间。collected_at 另用真实时间。
    collected=now
    supplements=repair_quotes(tickers,raw,collected,calendar)
    return build_snapshot(tickers,raw,fx,collected,calendar,reference,supplements)


def repair_quotes(tickers, raw, now, calendar):
    """Repair rejected components with explicit, independently anchored facts."""
    from live_strategy import provider_markets, sha
    state=calendar.state(now);session=state['session'].isoformat()
    previous=state['previous_session'].isoformat();missing=[]
    for symbol in tickers:
        if symbol=='SPY':continue
        fields=raw.get('gb_'+symbol.replace('.','$').lower())
        try:
            stamp=source_time(fields,now)
            valid=quotes.parse_us(fields) is not None and stamp>=state['minimum_source_time'] and stamp.astimezone(ET).date().isoformat()==session
        except (ValueError,TypeError,IndexError,KeyError):valid=False
        if not valid:missing.append(symbol)
    if not missing:return {}
    markets=provider_markets(missing);connection=db_sources.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute('''select distinct on(symbol) symbol,payload,evidence_sha256
                from live_ranking_context where symbol=any(%s) and phase='close' and session<=%s
                order by symbol,session desc,created_at desc''',(missing,session))
            facts={}
            for symbol,body,digest in cursor.fetchall():
                if sha(body)!=digest or body.get('symbol')!=symbol:
                    raise ValueError('Supplemental quote evidence hash/identity mismatch')
                facts[symbol]=(body,digest)
    finally:connection.close()
    result={}
    for symbol in missing:
        body,digest=facts.get(symbol,({},None));prices=body.get('raw',{})
        pc=prices.get(previous)
        if type(pc) not in (int,float) or not math.isfinite(pc) or pc<=0:continue
        try:
            response=quotes.requests.get('https://push2.eastmoney.com/api/qt/stock/get',
                params={'secid':str(markets[symbol])+'.'+symbol.replace('.','_'),
                        'fields':'f43,f57,f58,f59,f60,f86'},
                headers={'User-Agent':quotes.UA,'Referer':'https://quote.eastmoney.com/'},timeout=15)
            response.raise_for_status();envelope=response.json()
            quote,stamp=parse_secondary(symbol,envelope,pc,now,calendar)
            if not state['intraday'] and session in prices and not math.isclose(quote['p'],prices[session],rel_tol=0,abs_tol=.00011):
                raise ValueError('Secondary close disagrees with sealed daily fact')
            result[symbol]=(dict(quote,context_sha256=digest,source_sha256=sha(envelope)),stamp)
            continue
        except (quotes.requests.RequestException,ValueError,TypeError,KeyError,OverflowError):
            pass
        # A closed daily bar is usable only for that exact closed session,
        # and both native prices must agree with the other provider's fields.
        fields=raw.get('gb_'+symbol.replace('.','$').lower())
        if state['intraday'] or now<state['close'] or body.get('source_end')!=session or session not in prices or not fields:continue
        try:
            p=prices[session]
            if type(p) not in (int,float) or not math.isfinite(p) or p<=0:continue
            if (not math.isclose(float(fields[1]),p,rel_tol=0,abs_tol=.00011)
                    or not math.isclose(float(fields[26]),pc,rel_tol=0,abs_tol=.00011)):continue
        except (ValueError,TypeError,IndexError):continue
        result[symbol]=({'p':p,'pc':pc,'chg':round(p/pc-1,6),'source':'corroborated_daily_close',
                         'context_sha256':digest,'corroboration_sha256':sha(fields)},state['close'])
    return result


def parse_secondary(symbol, envelope, previous_close, now, calendar):
    if not isinstance(envelope,dict) or envelope.get('rc')!=0:raise ValueError('Secondary quote failed')
    data=envelope.get('data')
    if not isinstance(data,dict) or str(data.get('f57','')).replace('.','_')!=symbol.replace('.','_'):
        raise ValueError('Secondary quote identity mismatch')
    precision=data.get('f59')
    if type(precision) is not int or not 0<=precision<=6:raise ValueError('Secondary price unit unknown')
    for key in ('f43','f60','f86'):
        if type(data.get(key)) not in (int,float) or not math.isfinite(data[key]) or data[key]<=0:
            raise ValueError('Invalid secondary price/time')
    p=data['f43']/10**precision;pc=data['f60']/10**precision
    if not math.isclose(pc,previous_close,rel_tol=0,abs_tol=.00011):raise ValueError('Secondary previous close disagrees with verified fact')
    stamp=dt.datetime.fromtimestamp(data['f86'],UTC);state=calendar.state(now)
    bounds=calendar.bounds(state['session'])
    if (not bounds[0]<=stamp<=bounds[1] or stamp<state['minimum_source_time']
            or stamp>now+dt.timedelta(minutes=5)):
        raise ValueError('Secondary quote is not a fresh regular-session observation')
    return {'p':p,'pc':pc,'chg':round(p/pc-1,6),'source':'eastmoney_quote'},stamp


def publish(payload, sha, collected_at):
    connection = db_sources.db()
    try:
        with connection,connection.cursor() as cursor:
            cursor.execute("set local statement_timeout='20s'")
            cursor.execute("set local lock_timeout='5s'")
            cursor.execute('select evidence_sha256 from v_market_calendar')
            if cursor.fetchone()!=(payload.get('calendar_sha256'),):
                raise RuntimeError('Reviewed exchange calendar changed before publication')
            cursor.execute('''insert into live_quote_snapshot
                (singleton,source_as_of,collected_at,evidence_sha256,payload)
                values (true,%s,%s,%s,%s)
                on conflict(singleton) do update set
                  source_as_of=excluded.source_as_of,collected_at=excluded.collected_at,
                  evidence_sha256=excluded.evidence_sha256,payload=excluded.payload
                where excluded.source_as_of>=live_quote_snapshot.source_as_of
                  and excluded.collected_at>live_quote_snapshot.collected_at
                  and excluded.evidence_sha256<>live_quote_snapshot.evidence_sha256''',
                (payload['ts'],collected_at,sha,json.dumps(payload,allow_nan=False)))
            changed = cursor.rowcount
            cursor.execute('select source_as_of,evidence_sha256 from live_quote_snapshot where singleton')
            stored_date,stored_sha = cursor.fetchone()
            if stored_date < dt.datetime.fromisoformat(payload['ts'].replace('Z','+00:00')):
                raise RuntimeError('Stored quote snapshot regressed')
            cursor.execute("""insert into sys_event(kind,source,subject,status,metrics,finished_at)
                values ('fetch','live_quotes','current','ok',%s,now())""",
                (json.dumps({'changed':bool(changed),'count':payload['count'],
                  'source_as_of':payload['ts'],'stored_evidence_sha256':stored_sha}),))
        return bool(changed)
    finally:
        connection.close()


def close_already_calculated(session):
    from contextlib import closing
    with closing(db_sources.db()) as conn:
        with conn.cursor() as cursor:
            cursor.execute("""select count(*) from live_quote_snapshot q
                join live_strategy_current r on r.quote_version=q.evidence_sha256
                where q.singleton and q.payload->>'session'=%s
                  and q.payload->>'intraday'='false'""", (session,))
            return cursor.fetchone()[0] == 4


def run(write=False, as_of=None, *, closing_only=False):
    started = dt.datetime.now(UTC)
    logical = logical_time(as_of, started)
    if closing_only:
        import trading_window
        decision = trading_window.closing_collection_gate(started, trading_window.load_calendar(), as_of)
        if not decision['allow']:
            raise RuntimeError('Outside the verified closing collection window')
        if write and close_already_calculated(decision['session']):
            print(json.dumps({'session': decision['session'], 'status': 'close_already_calculated'}))
            return
        logical = logical_time(decision['as_of'], started)
    payload,sha = collect(logical)
    if closing_only and (payload['intraday'] or payload['session'] != decision['session']):
        raise RuntimeError('Quote does not belong to the target closing session')
    changed = publish(payload,sha,dt.datetime.now(UTC)) if write else False
    print(json.dumps({'write':write,'changed':changed,'count':payload['count'],
        'session':payload['session'],'source_as_of':payload['ts'],'evidence_sha256':sha,
        'logical_as_of':logical.isoformat(),'backfill':bool(as_of),
        'duration_seconds':round((dt.datetime.now(UTC)-started).total_seconds(),2)}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write',action='store_true')
    parser.add_argument('--as-of',dest='as_of',default=None,
        help='延迟补跑的目标场次逻辑时间（ISO 8601）')
    parser.add_argument('--closing-only',action='store_true')
    args=parser.parse_args()
    run(args.write, args.as_of, closing_only=args.closing_only)
