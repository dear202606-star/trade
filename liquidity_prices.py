"""Verified daily dollar-turnover windows; no monthly-volume proxies."""
import calendar
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

import db_select

BASE=Path(__file__).resolve().parent
POLICY='daily_dollar_amount_per_actual_trading_day'
_WINDOWS=None


def evidence_sha(evidence):
    return hashlib.sha256(json.dumps(evidence,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def calculator_sha():
    h=hashlib.sha256()
    for name in ('liquidity_prices.py','collect_liquidity_windows.py'):
        h.update(name.encode()+b'\0'+(BASE/name).read_bytes())
    return h.hexdigest()


def window_dates(base,rank):
    start=dt.date.fromisoformat(base+'-01');end=dt.date.fromisoformat(rank+'-01')
    if (end.year-start.year)*12+end.month-start.month!=12:
        raise ValueError('Liquidity requires exactly 12 subsequent calendar months')
    if rank>=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m'):
        raise ValueError('Liquidity month is not complete')
    start=dt.date(start.year+start.month//12,start.month%12+1,1)
    end=dt.date(end.year,end.month,calendar.monthrange(end.year,end.month)[1])
    return str(start),str(end)


def summarize_window(primary,independent,calendar_dates,base,rank):
    """Sum actual daily amounts, independently bound by daily low/high × shares."""
    start,end=window_dates(base,rank)
    expected={d for d in calendar_dates if start<=d<=end}
    if not expected:raise ValueError('No independently crosschecked market calendar')
    first,last=min(expected),max(expected)
    if first[:7]!=start[:7] or last[:7]!=rank:
        raise ValueError('Market calendar does not span the full window')
    months=set()
    date=dt.date.fromisoformat(start)
    for _ in range(12):
        months.add(date.strftime('%Y-%m'));date=dt.date(date.year+date.month//12,date.month%12+1,1)
    if {d[:7] for d in expected}!=months:
        raise ValueError('Market calendar has an absent month')
    p={d:r for d,r in primary.items() if start<=d<=end}
    other={d:r for d,r in independent.items() if start<=d<=end}
    if set(p)!=expected or set(other)!=expected:
        raise ValueError('Daily data does not match the complete market calendar')
    amount=low=high=0.0
    for date in sorted(expected):
        r=p[date];s=other[date]
        values=(r['amount'],r['volume'],r['low'],r['high'],s['volume'],s['low'],s['high'])
        if any(isinstance(v,bool) or not math.isfinite(v) or v<=0 for v in values):
            raise ValueError('Invalid daily turnover input')
        if r['low']>r['high'] or s['low']>s['high']:
            raise ValueError('Invalid daily price range')
        if not r['low']*r['volume']*.99<=r['amount']<=r['high']*r['volume']*1.01:
            raise ValueError('Provider amount/share units conflict with daily prices')
        amount+=r['amount'];low+=s['low']*s['volume'];high+=s['high']*s['volume']
    if not low<=amount<=high:
        raise ValueError('Dollar amount lies outside independent daily OHLCV bounds')
    return {'trading_days':len(expected),'primary_amount':amount,
            'independent_lower':low,'independent_upper':high,
            'first_date':first,'last_date':last,
            'calendar_sha256':evidence_sha(sorted(expected))}


def validate(row):
    evidence=row['evidence'];summary=evidence.get('summary',{})
    if (evidence.get('policy')!=POLICY or evidence.get('symbol')!=row['symbol']
        or evidence.get('base_month')!=row['base_month'] or evidence.get('rank_month')!=row['rank_month']
        or row['evidence_sha256']!=evidence_sha(evidence)
        or row['calculator_sha256']!=calculator_sha()):
        raise RuntimeError('Liquidity identity/evidence/version mismatch')
    window_dates(row['base_month'],row['rank_month'])
    for key in ('trading_days','primary_amount','independent_lower','independent_upper'):
        if row[key]!=summary.get(key):raise RuntimeError('Liquidity totals differ from evidence')
    n=row['trading_days'];amount=row['primary_amount'];low=row['independent_lower'];high=row['independent_upper']
    if (isinstance(n,bool) or not isinstance(n,int) or n<=0 or
        any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0
            for v in (amount,low,high)) or not low<=amount<=high):
        raise RuntimeError('Invalid verified liquidity totals')
    return row


def load_all():
    global _WINDOWS
    if _WINDOWS is not None:return _WINDOWS
    cols='symbol,base_month,rank_month,trading_days,primary_amount,independent_lower,independent_upper,evidence_sha256,calculator_sha256,evidence,verified'
    keys=cols.split(',');connection=None
    try:
        connection=db_select.db()
        connection.autocommit=False
        with connection.cursor() as cursor:
            # The pool defaults to 15-digit float text, which loses exact
            # round-trip values. Scope full precision to this read transaction.
            cursor.execute('set local extra_float_digits=3')
            cursor.execute('select '+cols+' from v_liquidity_windows order by symbol,rank_month')
            rows=[dict(zip(keys,r)) for r in cursor.fetchall()]
    except Exception:
        import db_rest
        rows=db_rest.get('v_liquidity_windows',{'select':cols,'order':'symbol,rank_month'})
    finally:
        if connection is not None:connection.close()
    result={}
    for row in rows:
        for key in ('primary_amount','independent_lower','independent_upper'):row[key]=float(row[key])
        key=(row['symbol'],row['base_month'],row['rank_month'])
        if key in result:raise RuntimeError('Duplicate liquidity window')
        result[key]=validate(row) if row['verified'] else None
    _WINDOWS=result
    return result


def average_daily(symbol,base,rank,threshold):
    row=load_all().get((symbol,base,rank))
    if row is None:raise RuntimeError('%s %s 日均成交额缺少已核验完整日线证据'%(symbol,rank))
    n=row['trading_days'];low=row['independent_lower']/n;high=row['independent_upper']/n
    if low<threshold<=high:
        raise RuntimeError('%s %s 独立成交额界限跨越筛选门槛'%(symbol,rank))
    return row['primary_amount']/n


def input_sha():
    rows=load_all()
    return evidence_sha([{'key':key,'proof':row['evidence_sha256'] if row else None}
                         for key,row in sorted(rows.items())])
