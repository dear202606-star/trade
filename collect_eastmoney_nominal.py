"""Collect explicit unadjusted monthly closes; never infer total returns.

Default mode plans only. --write permits bounded requests and independently
crosschecked screening observations. No return prices, picks or locks change.
"""
import argparse
import collections
import datetime as dt
import hashlib
import json
import math
import re
import statistics
import time
from pathlib import Path

import requests
import psycopg2.extras
import db_prices
import db_select

BASE=Path(__file__).resolve().parent
CACHE=BASE/'output/_eastmoney_probe/monthly'
URL='https://push2his.eastmoney.com/api/qt/stock/kline/get'


def parse(symbol,market,payload,month_to=None):
    data=payload.get('data') or {}
    if payload.get('rc')!=0 or data.get('code')!=symbol or data.get('market')!=market:
        raise ValueError('provider security identity mismatch or no data')
    today_date=dt.datetime.now(dt.timezone.utc).date()
    today=today_date.strftime('%Y-%m')
    monthly={}
    for line in data.get('klines') or []:
        fields=line.split(',')
        if len(fields)!=11:raise ValueError('unexpected monthly quote schema')
        date=dt.date.fromisoformat(fields[0]);month=fields[0][:7]
        if date>today_date:raise ValueError('future monthly quote')
        if month<'2008-01' or month>=today or (month_to and month>month_to):continue
        if month in monthly:raise ValueError('duplicate monthly quote')
        close,volume=float(fields[2]),float(fields[5])
        if not math.isfinite(close) or close<=0 or not math.isfinite(volume) or volume<0:
            raise ValueError('invalid unadjusted quote')
        # A stopped security's last mid-month trade is not a month-end quote.
        import calendar
        if volume==0 or calendar.monthrange(date.year,date.month)[1]-date.day>7:continue
        monthly[month]={'date':fields[0],'close':close}
    return monthly


def review(monthly,anchors,selected):
    native=[abs(r['close']/anchors[m]['close']-1) for m,r in monthly.items()
            if m in anchors and r['date']==anchors[m]['date']]
    # Conservative first deployment: each security needs 24 independent native
    # anchors. A matching return ratio alone cannot prove absolute share units.
    if len(native)<24 or statistics.median(native)>.001 or max(native)>.01:
        raise ValueError('independent native-price units not proved')
    common=sorted(set(monthly)&set(selected));dev=[]
    for a,b in zip(common,common[1:]):
        if (int(b[:4])-int(a[:4]))*12+int(b[5:])-int(a[5:])!=1:continue
        ratio=(monthly[b]['close']/monthly[a]['close'])/(selected[b][0]/selected[a][0])
        dev.append(abs(math.log(ratio)))
    if len(dev)<24:raise ValueError('insufficient independent return overlap')
    median=statistics.median(dev);p90=sorted(dev)[int((len(dev)-1)*.9)]
    if median>.005 or p90>.05:raise ValueError('security/return identity crosscheck failed')
    margin=max(.01,5*max(native)/(1-max(native)))
    return {'native_pairs':len(native),'native_median_relative_deviation':statistics.median(native),
            'native_max_relative_deviation':max(native),'overlap_returns':len(dev),
            'median_log_deviation':median,'p90_log_deviation':p90,'threshold_uncertainty_usd':margin}


def run(limit=20,write=False):
    if not 0<=limit<=1000:raise ValueError('limit must be 0..1000')
    if limit==0:
        print('Eastmoney collection skipped (limit 0)');return
    directory=json.loads((BASE/'output/eastmoney_security_directory.json').read_text())
    mapping=collections.defaultdict(list)
    for row in directory['records']:mapping[row['code']].append(row)
    impact=json.loads((BASE/'output/nominal_price_impact.json').read_text())
    connection=db_select.db()
    connection.autocommit=False
    try:
        with connection.cursor(name='em_native_anchors') as cursor:
            cursor.itersize=2000
            cursor.execute('''select symbol,month,close,trade_date,source,evidence_sha256
                              from v_nominal_monthly where source<>'eastmoney_native' order by symbol,month''')
            anchors=collections.defaultdict(dict)
            for symbol,month,close,date,source,proof in cursor:
                anchors[symbol][month]={'close':float(close),'date':str(date),'source':source,'evidence_sha256':proof}
        with connection.cursor(name='em_known_native') as cursor:
            cursor.itersize=5000
            cursor.execute('select symbol,month from v_nominal_monthly')
            known=set(cursor)
        with connection.cursor() as cursor:
            cursor.execute('select symbol,month_to from security_history_window')
            windows=dict(cursor.fetchall())
            cursor.execute('select symbol from series_quarantine')
            blocked={r[0] for r in cursor.fetchall()}
            cursor.execute('select symbol,source,basis from series_pick')
            picks={s:(source,basis) for s,source,basis in cursor.fetchall()}
    finally:connection.close()
    scores=collections.Counter(r['symbol'] for r in impact['candidate_cells']
        if (r['symbol'],r['month']) not in known and r['symbol'] not in blocked)
    targets=[s for s,n in scores.most_common() if len(mapping.get(s,[]))==1 and len(anchors[s])>=24
             and re.fullmatch(r'[A-Z][A-Z0-9._-]{0,24}',s)][:limit]
    if not write:
        print(json.dumps({'write':False,'targets':targets,'limit':limit}));return
    CACHE.mkdir(parents=True,exist_ok=True)
    selected=db_prices.load_all();stamp=tuple(db_prices._db_stamp() or ())
    if not stamp:raise RuntimeError('no live selected snapshot')
    results=[]
    with requests.Session() as session:
        for symbol in targets:
            market=mapping[symbol][0]['market'];secid=str(market)+'.'+symbol
            path=CACHE/(secid+'.json')
            if path.exists() and time.time()-path.stat().st_mtime<86400:record=json.loads(path.read_text())
            else:
                try:
                    response=session.get(URL,params={'secid':secid,'klt':103,'fqt':0,'beg':20080101,
                        'end':int((dt.datetime.now(dt.timezone.utc).date()-dt.timedelta(days=1)).strftime('%Y%m%d')),
                        'fields1':'f1,f2,f3,f4,f5,f6','fields2':'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'},timeout=20)
                    response.raise_for_status()
                    if len(response.content)>1024**2:raise ValueError('monthly payload exceeds bound')
                    record={'received_at':dt.datetime.now(dt.timezone.utc).isoformat(),
                            'mode':'fqt=0','secid':secid,'payload':response.json()}
                    path.write_text(json.dumps(record,separators=(',',':')))
                except requests.RequestException as exc:
                    results.append({'symbol':symbol,'status':'network_failure','type':type(exc).__name__});break
            try:
                if record.get('mode')!='fqt=0' or record.get('secid')!=secid:
                    raise ValueError('cached request identity/mode differs')
                monthly=parse(symbol,market,record['payload'],windows.get(symbol))
                proof=review(monthly,anchors[symbol],selected[symbol])
                digest=hashlib.sha256(json.dumps(record['payload'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
                rows=[(symbol,m,r['close'],r['date'],'eastmoney_native',URL+'?secid='+secid+'&klt=103&fqt=0',
                    record['received_at'],digest,proof['overlap_returns'],proof['median_log_deviation'],proof['p90_log_deviation'],
                    'Explicit raw fqt=0; '+str(proof['native_pairs'])+' independent native anchors; '+digest)
                    for m,r in monthly.items() if m in selected[symbol] and (symbol,m) not in known
                    and abs(r['close']-5)>=proof['threshold_uncertainty_usd']]
                connection=db_select.db();connection.autocommit=False
                try:
                    with connection,connection.cursor() as cursor:
                        cursor.execute('select source,basis from series_pick where symbol=%s for share',(symbol,))
                        if cursor.fetchone()!=picks.get(symbol):
                            raise RuntimeError('selected security/source changed during review: '+symbol)
                        if rows:
                            psycopg2.extras.execute_values(cursor,'''insert into nominal_monthly
                              (symbol,month,close,trade_date,source,source_url,fetched_at,evidence_sha256,
                               crosscheck_pairs,median_log_deviation,p90_log_deviation,unit_evidence)
                              values %s on conflict(symbol,month,source) do nothing''',rows,page_size=500)
                        cursor.execute("select count(*) from nominal_monthly where symbol=%s and source='eastmoney_native'",(symbol,))
                        stored=cursor.fetchone()[0]
                finally:connection.close()
                results.append(dict(proof,symbol=symbol,status='stored',prepared=len(rows),stored=stored,payload_sha256=digest))
            except ValueError as exc:
                results.append({'symbol':symbol,'status':'review_required','reason':str(exc)})
            print(symbol,results[-1]['status'],flush=True)
            receipt={'write':True,'requested_limit':limit,'results':results,'return_prices_sources_locks_and_baselines_changed':False}
            (BASE/'output/eastmoney_nominal_ingest.json').write_text(json.dumps(receipt,indent=2)+'\n')
            time.sleep(.15)
    if stamp!=tuple(db_prices._db_stamp() or ()):
        raise RuntimeError('selected inputs changed during collection; review batch evidence before use')
    receipt={'write':True,'requested_limit':limit,'results':results,'snapshot_stamp':list(stamp),
             'independent_native_snapshot_sha256':hashlib.sha256(json.dumps(anchors,sort_keys=True).encode()).hexdigest(),
             'return_prices_sources_locks_and_baselines_changed':False}
    (BASE/'output/eastmoney_nominal_ingest.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({'reviewed':len(results),'prepared':sum(r.get('prepared',0) for r in results)}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--limit',type=int,default=20);parser.add_argument('--write',action='store_true')
    args=parser.parse_args();run(args.limit,args.write)
