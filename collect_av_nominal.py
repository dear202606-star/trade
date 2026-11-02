"""Free adjusted monthly requests yield native closes and optional missing TR.

All calls reserve the shared rolling 25/day quota first. Defaults to dry-run;
--write permits requests and writes. Existing prices, locks and picks are never
updated directly: needed TR rows go through the existing staging validator.
"""
import argparse
import calendar
import collections
import datetime as dt
import hashlib
import json
import math
import statistics
from pathlib import Path

import requests
import psycopg2.extras
import db_prices
import db_select
import ingest_validate
import relay_source
import staging_write

BASE=Path(__file__).resolve().parent
CACHE=BASE/'output/_av_monthly_native'
SOURCE='alphavantage_native'
BOUNDARIES={'BJS':('2010-04','https://www.sec.gov/Archives/edgar/data/808362/000095012310039884/h72191exv99w1.htm'),
            'AYE':('2011-02','https://www.sec.gov/Archives/edgar/data/3673/000119312511047579/d8k.htm'),
            'LB':('2021-08','https://www.sec.gov/Archives/edgar/data/701985/000114036121026658/nt10026698x7_8k.htm'),
            'HES':('2025-07','https://www.sec.gov/Archives/edgar/data/93410/000095014225001949/eh250652625_8k.htm'),
            'ALXN':('2021-07','https://www.astrazeneca.com/media-centre/press-releases/2021/acquisition-of-alexion-completed.html'),
            'XLNX':('2022-02','https://www.amd.com/en/newsroom/press-releases/2022-2-14-amd-completes-acquisition-of-xilinx.html')}


def parse(symbol,payload):
    meta=payload.get('Meta Data') or {}
    if meta.get('2. Symbol')!=symbol:
        raise ValueError('response symbol mismatch')
    series=payload.get('Monthly Adjusted Time Series') or payload.get('Monthly Time Series')
    if not series:
        raise ValueError('no monthly price series')
    adjusted='Monthly Adjusted Time Series' in payload
    today=dt.datetime.now(dt.timezone.utc).date()
    observations,returns={},{}
    for day,row in series.items():
        date=dt.date.fromisoformat(day)
        if date>today:
            raise ValueError('future monthly quote')
        month=day[:7]
        if month>=today.strftime('%Y-%m'):
            continue
        if symbol in BOUNDARIES and month>=BOUNDARIES[symbol][0]:
            continue
        if calendar.monthrange(date.year,date.month)[1]-date.day>7:
            continue
        close=float(row['4. close'])
        adj=float(row['5. adjusted close']) if adjusted else close
        if not all(math.isfinite(x) and x>0 for x in (close,adj)):
            raise ValueError('invalid native or adjusted close')
        if month in observations:
            raise ValueError('duplicate monthly quote')
        observations[month]={'date':day,'close':close}
        returns[month]=(adj,close)
    if len(observations)<24:
        raise ValueError('less than 24 completed monthly observations')
    return observations,returns,adjusted


def targets():
    report=json.loads((BASE/'output/nominal_price_impact.json').read_text())
    coverage=json.loads((BASE/'output/coverage_audit.json').read_text())
    connection=db_select.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute("select symbol,month from v_nominal_monthly where month like '%-12'")
            known=set(cursor.fetchall())
            cursor.execute('select symbol from series_quarantine')
            blocked={r[0] for r in cursor.fetchall()}
            cursor.execute("select distinct symbol from prices_monthly where source='alphavantage' and basis='total_return' union select distinct symbol from ingest_staging where source='alphavantage' and reject_reason is null")
            existing_tr={r[0] for r in cursor.fetchall()}
    finally:
        connection.close()
    scores=collections.Counter()
    required=collections.defaultdict(set)
    # Known current-provider reuses do not serve the historical index issuer.
    # See ARCHIVE_IDENTITY_REVIEW and the LB SEC evidence in BOUNDARIES.
    blocked.update({'LB','HCP','PX','S','CHK','Q'})
    for row in report['years']:
        for field in ('unknown_all_pass_holdings','unknown_all_fail_holdings'):
            for holding in (row.get(field) or {}).get('holdings',[]):
                symbol=holding['ticker'];month='%d-12'%(row['year']-1)
                if (symbol,month) not in known:
                    scores[symbol]+=100+20*(row['year']-2010)
    for cell in report['candidate_cells']:
        if (cell['symbol'],cell['month']) not in known:
            scores[cell['symbol']]+=1
            required[cell['symbol']].add(cell['month'])
    for cell in coverage['gaps']:
        if not cell['quarantined'] and not cell['locked'] and cell['symbol'] not in existing_tr:
            scores[cell['symbol']]+=2
            required[cell['symbol']].add('%d-12'%cell['year'])
    for symbol,months in required.items():
        path=CACHE/(symbol+'.json')
        if path.exists():
            payload=json.loads(path.read_text())['payload']
            series=payload.get('Monthly Adjusted Time Series') or payload.get('Monthly Time Series') or {}
            if series and max(months)<min(series)[:7]:
                # The provider only knows a later issuer/period. Re-downloading
                # it tomorrow cannot fill this old historical window.
                blocked.add(symbol)
    return [s for s,_ in scores.most_common() if s not in blocked]


def run(limit=8,write=False,symbols=None):
    if not 0<=limit<=25:
        raise ValueError('limit must be 0..25')
    if limit==0:
        print('AV native collection skipped (limit 0)')
        return
    selected=(symbols or targets())[:limit]
    if not write:
        print(json.dumps({'write':False,'targets':selected,'limit':limit}));return
    key=relay_source.keys().get('alphavantage_key')
    if not key:
        raise RuntimeError('Missing configured Alpha Vantage key')
    CACHE.mkdir(exist_ok=True)
    prices=db_prices.load_all()
    connection=db_select.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute('select symbol,basis from series_pick')
            basis=dict(cursor.fetchall())
    finally:
        connection.close()
    receipts=[]
    for symbol in selected:
        path=CACHE/(symbol+'.json')
        record=json.loads(path.read_text()) if path.exists() else None
        if record and symbol not in BOUNDARIES:
            age=dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(record['received_at'])
            if age>dt.timedelta(days=1):
                record=None
        if record is None:
            if not relay_source.reserve_av_call(symbol):
                break
            try:
                response=requests.get('https://www.alphavantage.co/query',params={
                    'function':'TIME_SERIES_MONTHLY_ADJUSTED','symbol':symbol,'apikey':key},timeout=30)
            except requests.RequestException as exc:
                receipts.append({'symbol':symbol,'status':'network_failure','type':type(exc).__name__})
                break
            if response.status_code in (403,429):
                receipts.append({'symbol':symbol,'status':'upstream_blocked','http':response.status_code});break
            response.raise_for_status()
            payload=response.json()
            if 'Monthly Adjusted Time Series' not in payload:
                receipts.append({'symbol':symbol,'status':'no_series','fields':sorted(payload)})
                if payload.get('Note') or payload.get('Information'):
                    break
                continue
            record={'received_at':dt.datetime.now(dt.timezone.utc).isoformat(),'payload':payload}
            path.write_text(json.dumps(record,separators=(',',':')))
        try:
            observations,bars,adjusted=parse(symbol,record['payload'])
            report=json.loads((BASE/'output/nominal_price_impact.json').read_text())
            needed={r['month'] for r in report['candidate_cells'] if r['symbol']==symbol}
            if needed and not needed.intersection(observations):
                raise ValueError('response does not cover any requested historical month; issuer/window review required')
            common=sorted(set(bars)&set(prices.get(symbol,{})))
            deviations=[]
            for before,after in zip(common,common[1:]):
                if (int(after[:4])-int(before[:4]))*12+int(after[5:])-int(before[5:])!=1:
                    continue
                deviations.append(abs(math.log((bars[after][0]/bars[before][0])/
                    (prices[symbol][after][0]/prices[symbol][before][0]))))
            median=statistics.median(deviations) if deviations else None
            p90=sorted(deviations)[int((len(deviations)-1)*.9)] if deviations else None
            if len(deviations)<24 or median>.005 or p90>.05:
                raise ValueError('selected-series identity/return crosscheck unresolved')
            digest=hashlib.sha256(json.dumps(record['payload'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
            rows=[(symbol,m,r['close'],r['date'],SOURCE,'https://www.alphavantage.co/documentation/',
                   record['received_at'],digest,len(deviations),median,p90,
                   'Primary monthly raw/as-traded Close; response symbol matched; '+digest+
                   ('; security ends before '+BOUNDARIES[symbol][0]+' '+BOUNDARIES[symbol][1] if symbol in BOUNDARIES else ''))
                  for m,r in observations.items()]
            connection=db_select.db();connection.autocommit=False
            try:
                with connection,connection.cursor() as cursor:
                    psycopg2.extras.execute_values(cursor,
                        '''insert into nominal_monthly(symbol,month,close,trade_date,source,source_url,
                           fetched_at,evidence_sha256,crosscheck_pairs,median_log_deviation,p90_log_deviation,
                           unit_evidence) values %s on conflict(symbol,month,source) do nothing''',rows,page_size=500)
                    cursor.execute('select count(*) from nominal_monthly where symbol=%s and source=%s',(symbol,SOURCE))
                    stored=cursor.fetchone()[0]
            finally:
                connection.close()
            staged=0
            if adjusted and basis.get(symbol)!='total_return':
                ok,reason,cleaned=ingest_validate.check_payload(symbol,bars,'total_return',allow_stale=True)
                if not ok:
                    raise ValueError('TR staging validation: '+reason)
                staged=staging_write.push([(symbol,m,a,r,'alphavantage','ok','total_return')
                                           for m,(a,r) in cleaned.items()])
                if staged!=len(cleaned):
                    raise RuntimeError('Partial TR staging write')
            receipts.append({'symbol':symbol,'status':'stored','prepared_native_observations':len(rows),
                             'stored_native_observations':stored,
                             'staged_tr':staged,'payload_sha256':digest,
                             'median_log_deviation':median,'p90_log_deviation':p90})
        except ValueError as exc:
            receipts.append({'symbol':symbol,'status':'review_required','reason':str(exc)})
        print(symbol,receipts[-1]['status'],flush=True)
        (BASE/'output/av_nominal_ingest.json').write_text(json.dumps(
            {'write':True,'requested_limit':limit,'results':receipts},indent=2)+'\n')
    used=relay_source.av_used_last_24h()
    result={'write':True,'requested_limit':limit,'results':receipts,
            'remaining_daily_calls':max(0,25-used) if used is not None else None}
    (BASE/'output/av_nominal_ingest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--limit',type=int,default=8)
    parser.add_argument('--symbols')
    parser.add_argument('--write',action='store_true')
    args=parser.parse_args()
    if not 0<=args.limit<=25:
        parser.error('--limit must be 0..25')
    run(args.limit,args.write,args.symbols.split(',') if args.symbols else None)
