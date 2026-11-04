"""Publish current quotes to the online singleton; never process historical prices."""
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


def read_snapshot(cursor):
    cursor.execute('select source_as_of,collected_at,evidence_sha256,payload from live_quote_snapshot where singleton')
    row=cursor.fetchone()
    if row is None:raise RuntimeError('在线报价快照不存在')
    result=dict(zip(('source_as_of','collected_at','evidence_sha256','payload'),row))
    cursor.execute("""select finished_at,metrics from sys_event where kind='fetch'
        and source='live_quotes' and status='ok' order by finished_at desc,id desc limit 1""")
    event=cursor.fetchone()
    return result,dict(zip(('finished_at','metrics'),event)) if event else None


def load_snapshot(now=None):
    """Read one consistent online quote/calendar/worker version; no file fallback."""
    from watchdog_checks import quote_problems
    now=now or dt.datetime.now(UTC)
    connection=db_sources.db()
    try:
        connection.set_session(isolation_level='REPEATABLE READ',readonly=True)
        with connection.cursor() as cursor:
            cursor.execute("set local statement_timeout='20s'")
            cursor.execute('select calendar,evidence_sha256 from v_market_calendar')
            rows=cursor.fetchall()
            if len(rows)!=1:raise RuntimeError('No unique reviewed online exchange calendar')
            calendar=market_calendar.Calendar(*rows[0])
            row,heartbeat=read_snapshot(cursor)
            problems=quote_problems(row,heartbeat,calendar,now)
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


def build_snapshot(tickers, raw, fx, now, calendar=None, reference=None):
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
        if intraday and (now-stamp > dt.timedelta(minutes=20)):
            continue
        values[symbol] = dict(quote,as_of=stamp.isoformat())
    if len(values)<.98*len(tickers):
        raise ValueError('Incomplete same-session quote coverage: %d/%d'%(len(values),len(tickers)))
    payload = {'ts':as_of.strftime('%Y-%m-%dT%H:%M:%SZ'),
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


def universe(now):
    date = now.astimezone(ET).date().isoformat()
    active,_,version = db_ref.load_market_reference(date)
    if len(active)<450:
        raise RuntimeError('Online current membership is incomplete')
    return sorted(active | set(quotes.EXTRA)),{'as_of':date,'version':version}


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
    return build_snapshot(tickers,raw,fx,dt.datetime.now(UTC),calendar,reference)


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


def run(write=False):
    started = dt.datetime.now(UTC)
    payload,sha = collect(started)
    changed = publish(payload,sha,dt.datetime.now(UTC)) if write else False
    print(json.dumps({'write':write,'changed':changed,'count':payload['count'],
        'session':payload['session'],'source_as_of':payload['ts'],'evidence_sha256':sha,
        'duration_seconds':round((dt.datetime.now(UTC)-started).total_seconds(),2)}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write',action='store_true')
    run(parser.parse_args().write)
