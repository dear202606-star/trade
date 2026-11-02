"""Import only exact dated raw closes corroborated by issuer filings.

An issuer's explicit closing-price fact proves that date's share units; it
does not approve other dates or waive the bulk collector's 24 native anchors.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import statistics
from pathlib import Path

import db_prices
import db_select

BASE=Path(__file__).resolve().parent
FACTS=[
    {'symbol':'PLTR','date':'2024-12-31','close':75.63,'cik':'1321655',
     'url':'https://www.sec.gov/Archives/edgar/data/1321655/000132165525000057/pltr-20250424.htm',
     'location':'Outstanding Equity Awards at Fiscal Year-End, footnote 1; Class A closing price'},
    {'symbol':'VST','date':'2024-12-31','close':137.87,'cik':'1692819',
     'url':'https://www.sec.gov/Archives/edgar/data/1692819/000119312525057930/d880503ddef14a.htm',
     'location':'Outstanding equity awards, footnote 1; common stock closing market price'}]


def verify_exact_close(data,fact,market):
    if data['code']!=fact['symbol'] or data['market']!=market:
        raise ValueError('Quote identity mismatch')
    exact=[r.split(',') for r in data['klines'] if r.split(',')[0]==fact['date']]
    if len(exact)!=1 or len(exact[0])!=11:
        raise ValueError('Issuer/native closing price mismatch')
    close=float(exact[0][2])
    if not math.isfinite(close) or close<=0 or abs(close-fact['close'])>1e-9:
        raise ValueError('Issuer/native closing price mismatch')


def run(write=False):
    selected=db_prices.load_all();stamp=tuple(db_prices._db_stamp() or ())
    if not stamp:raise RuntimeError('No selected-price version')
    connection=db_select.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute('select symbol,source,basis from series_pick where symbol=any(%s)',
                           ([r['symbol'] for r in FACTS],))
            picks={s:(source,basis) for s,source,basis in cursor.fetchall()}
    finally:connection.close()
    probe=json.loads((BASE/'output/liquidity_source_probe.json').read_text())
    rows=[];proofs=[]
    for fact in FACTS:
        symbol=fact['symbol'];date=fact['date'];year=int(date[:4]);month=date[:7]
        receipt=next(r for r in probe['results'] if r['symbol']==symbol and r['year']==year)
        if 'fqt=0' not in receipt['source_url']:raise ValueError('Not an explicit raw request')
        raw=(BASE/'output/_liquidity_probe'/(symbol+'-'+str(year)+'.json')).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=receipt['payload_sha256']:raise ValueError('Daily proof changed')
        data=json.loads(raw)['data']
        verify_exact_close(data,fact,receipt['market'])
        independent_path=BASE/'output/_nominal_daily'/(symbol+'.json')
        daily=json.loads(independent_path.read_text())['rows'];monthly={}
        for r in sorted(daily,key=lambda r:r['d']):
            price=float(r['c'])
            if not math.isfinite(price) or price<=0:raise ValueError('Invalid independent daily close')
            monthly[r['d'][:7]]=price
        bars=selected[symbol]
        common=sorted(set(monthly)&set(bars));deviations=[]
        for a,b in zip(common,common[1:]):
            if (int(b[:4])-int(a[:4]))*12+int(b[5:])-int(a[5:])!=1:continue
            deviations.append(abs(math.log((monthly[b]/monthly[a])/(bars[b][0]/bars[a][0]))))
        if len(deviations)<24:raise ValueError('Insufficient independent return overlap')
        median=statistics.median(deviations);p90=sorted(deviations)[int((len(deviations)-1)*.9)]
        if median>.005 or p90>.05:raise ValueError('Selected-security return crosscheck failed')
        if month not in bars:raise ValueError('No selected return month for fact')
        proof={'issuer_fact':fact,'provider_daily_sha256':receipt['payload_sha256'],
               'provider_url':receipt['source_url'],'independent_daily_sha256':hashlib.sha256(independent_path.read_bytes()).hexdigest(),
               'return_pairs':len(deviations),'median_log_deviation':median,'p90_log_deviation':p90,
               'scope':'exact issuer-disclosed date only; other months not approved'}
        digest=hashlib.sha256(json.dumps(proof,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        rows.append((symbol,month,fact['close'],date,'eastmoney_native',receipt['source_url'],
                     dt.datetime.now(dt.timezone.utc),digest,len(deviations),median,p90,
                     'Exact issuer-disclosed closing price/date: '+fact['url']+'; proof '+digest))
        proofs.append(dict(proof,evidence_sha256=digest))
    if stamp!=tuple(db_prices._db_stamp() or ()):raise RuntimeError('Selected snapshot changed')
    inserted=0
    if write:
        connection=db_select.db();connection.autocommit=False
        try:
            with connection,connection.cursor() as cursor:
                for row in rows:
                    cursor.execute('select source,basis from series_pick where symbol=%s for share',(row[0],))
                    if cursor.fetchone()!=picks.get(row[0]):
                        raise RuntimeError('Selected security/source changed during review')
                    cursor.execute('''insert into nominal_monthly
                      (symbol,month,close,trade_date,source,source_url,fetched_at,evidence_sha256,
                       crosscheck_pairs,median_log_deviation,p90_log_deviation,unit_evidence)
                      values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                      on conflict(symbol,month,source) do nothing''',row)
                    inserted+=cursor.rowcount
                    cursor.execute('''select close,trade_date,unit_evidence from nominal_monthly
                                      where symbol=%s and month=%s and source=%s''',(row[0],row[1],row[4]))
                    existing=cursor.fetchone()
                    if (existing is None or float(existing[0])!=row[2] or str(existing[1])!=row[3]
                        or not existing[2]):
                        raise RuntimeError('Stored native observation conflicts with issuer fact')
        finally:connection.close()
    result={'write':write,'prepared':len(rows),'inserted':inserted,'proofs':proofs,
            'return_prices_sources_locks_and_published_outputs_changed':False}
    filename='verified_closing_facts_ingest.json' if write else 'verified_closing_facts_review.json'
    (BASE/'output'/filename).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--write',action='store_true')
    run(parser.parse_args().write)
