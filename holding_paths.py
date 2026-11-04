"""One return path for annual holdings; event evidence comes only from the DB."""
import hashlib
import datetime as dt
import json
import math
from pathlib import Path

import db_select
from holding_consideration import POLICY

BASE=Path(__file__).resolve().parent
PRODUCERS={'lb_distribution':('research/audit_lb_distribution.py','reconstruct_nominal.py'),
           'cvc_cash_merger':('research/audit_cvc_cash_merger.py',),
           'wdc_distribution':('research/audit_wdc_distribution.py','collect_eastmoney_nominal.py'),
           'twtr_cash_merger':('research/audit_twtr_cash_merger.py','research/import_twtr_cash_merger.py',
                               'fetch_stockanalysis.py','reconstruct_nominal.py'),
           'atvi_cash_merger':('research/audit_atvi_cash_merger.py','research/import_atvi_cash_merger.py',
                               'fetch_stockanalysis.py','reconstruct_nominal.py')}
_PATHS=None
_EVIDENCE_SHA=None
_EVENT_MONTHS={}


def evidence_sha(evidence):
    return hashlib.sha256(json.dumps(evidence,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def calculator_sha(producer):
    if producer not in PRODUCERS:
        raise RuntimeError('Unknown holding-event evidence producer')
    digest=hashlib.sha256()
    for name in ('holding_consideration.py','import_holding_event_paths.py')+PRODUCERS[producer]:
        digest.update(name.encode()+b'\0'+(BASE/name).read_bytes())
    return digest.hexdigest()


def validate(row):
    evidence=row['evidence']
    if (row['policy']!=POLICY or evidence.get('holding_event_policy')!=POLICY
        or row['evidence_sha256']!=evidence_sha(evidence)
        or row['calculator_sha256']!=calculator_sha(evidence.get('producer'))):
        raise RuntimeError('Holding-event evidence/version mismatch')
    if evidence.get('symbol')!=row['symbol'] or evidence.get('hold_year')!=row['hold_year']:
        raise RuntimeError('Holding-event security/year mismatch')
    if evidence.get('original_symbol',row['symbol'])!=row['symbol'] and row['symbol'] not in evidence.get('same_common_equity_tickers',[]):
        raise RuntimeError('Unverified holding-event ticker alias')
    if evidence.get('event_date')!=str(row['event_date']):
        raise RuntimeError('Holding-event effective date mismatch')
    values=row['monthly_values']
    months=['%d-%02d'%(row['hold_year'],m) for m in range(1,13)]
    if (not isinstance(values,list) or len(values)!=12
        or set(evidence.get('retained_monthly_values',{}))!=set(months)
        or values!=[evidence['retained_monthly_values'][m] for m in months]
        or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 for v in values)):
        raise RuntimeError('Incomplete or invalid holding-event monthly values')
    return values


def load_all():
    global _PATHS,_EVIDENCE_SHA,_EVENT_MONTHS
    if _PATHS is not None:return _PATHS
    connection=None
    try:
        connection=db_select.db()
        connection.autocommit=False
        connection.set_session(isolation_level='REPEATABLE READ',readonly=True)
        with connection.cursor() as cursor:
            cursor.execute('''select symbol,hold_year,event_date,policy,monthly_values,evidence,evidence_sha256,calculator_sha256,verified
                              from v_holding_event_paths order by symbol,hold_year''')
            keys=('symbol','hold_year','event_date','policy','monthly_values','evidence','evidence_sha256','calculator_sha256','verified')
            rows=[dict(zip(keys,r)) for r in cursor.fetchall()]
            cursor.execute("select subject,metrics from sys_event where kind='validation' and source='verified_corporate_terms' and status='pass' order by subject,id")
            terms=cursor.fetchall()
    finally:
        if connection is not None:connection.close()
    result={}
    for row in rows:
        key=(row['symbol'],row['hold_year'])
        if key in result:raise RuntimeError('Duplicate holding-event evidence')
        result[key]=validate(row) if row['verified'] else None
    event_months={(r['symbol'],r['hold_year']):str(r['event_date'])[:7] for r in rows}
    term_hashes=[]
    for subject,metrics in terms:
        proof=metrics.get('proof',{})
        if metrics.get('evidence_sha256')!=evidence_sha(proof):
            raise RuntimeError('Corporate terms evidence hash mismatch')
        term=proof.get('terms',{})
        date=dt.date.fromisoformat(term['event_date'])
        symbol=term['symbol']
        if subject!=symbol+':'+date.isoformat() or proof.get('holding_valuation_accepted') is not False:
            raise RuntimeError('Corporate terms identity/scope mismatch')
        sha=metrics['evidence_sha256'];term_hashes.append(sha)
        key=(symbol,date.year)
        accepted=next((r for r in rows if (r['symbol'],r['hold_year'])==key and r['verified']),None)
        # A path must explicitly cover this exact terms version. Existing paths
        # cannot accidentally authorize a later event in the same holding year.
        if accepted is None or sha not in accepted['evidence'].get('corporate_terms_sha256',[]):
            result[key]=None
        event_months[key]=min(event_months.get(key,date.isoformat()[:7]),date.isoformat()[:7])
    _PATHS=result
    _EVENT_MONTHS=event_months
    _EVIDENCE_SHA=evidence_sha({'paths':[{'symbol':r['symbol'],'year':r['hold_year'],
                               'evidence':r['evidence_sha256'],'calculator':r['calculator_sha256'],
                               'verified':r['verified']} for r in rows], 'corporate_terms':term_hashes})
    return result


def input_sha():
    load_all()
    if _EVIDENCE_SHA is None:raise RuntimeError('Holding-event snapshot was not loaded')
    return _EVIDENCE_SHA


def require_price_only_window(symbol,base_month,end_month):
    """A quote/split-only calculation cannot value a distribution window."""
    paths=load_all()
    for key in paths:
        if key[0]!=symbol:continue
        month=_EVENT_MONTHS.get(key)
        if month is None:raise RuntimeError('Holding-event dates were not loaded')
        if base_month<month<=end_month:
            raise RuntimeError('%s %s→%s 滚动企业行动收益尚未核验'%(symbol,base_month,end_month))


def momentum_bars(symbol,bars,base_month,rank_month):
    """Return a wealth index for ranking, never a fabricated tradable quote.

    Calendar-year rankings share the approved holding path. Non-calendar
    windows crossing an event require their own evidence; no adj fallback.
    """
    paths=load_all()
    year=int(rank_month[:4])
    if base_month=='%d-12'%(year-1) and rank_month=='%d-12'%year and (symbol,year) in paths:
        values_=paths[(symbol,year)]
        if values_ is None:raise RuntimeError('%s %d 动量企业行动证据尚未核验'%(symbol,year))
        result={base_month:(1.0,None)}
        result.update({'%d-%02d'%(year,m):(value,None) for m,value in enumerate(values_,1)})
        return result
    require_price_only_window(symbol,base_month,rank_month)
    return bars or {}


def values(symbol,year,load_bars):
    paths=load_all()
    if (symbol,year) in paths:
        event=paths[(symbol,year)]
        if event is None:raise RuntimeError('%s %d 企业行动证据待核验或已撤回'%(symbol,year))
        return list(event)
    bars=load_bars(symbol)
    base='%d-12'%(year-1)
    if not bars or base not in bars or not bars[base][0]:
        raise RuntimeError('%s 缺 %s 价格'%(symbol,base))
    result=[]
    for m in range(1,13):
        month='%d-%02d'%(year,m)
        if month not in bars:
            raise RuntimeError('%s 缺 %s 价格'%(symbol,month))
        value=bars[month][0]/bars[base][0]
        if not math.isfinite(value) or value<=0:
            raise RuntimeError('%s %s 收益价格非法'%(symbol,month))
        result.append(value)
    return result
