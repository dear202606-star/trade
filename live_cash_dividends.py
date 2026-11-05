"""Validate new ordinary cash adjustments against independent distribution records."""
import datetime as dt
import io
import hashlib

import openpyxl
import requests

from live_strategy import sha,positive

SSGA='https://www.ssga.com/library-content/products/fund-data/etfs/us/spdr-etf-historical-distributions.xlsx'


def spy_distributions(content,wanted=None):
    workbook=openpyxl.load_workbook(io.BytesIO(content),read_only=True,data_only=True)
    result={}
    try:
        rows=iter(workbook['dividend'].values);header=next(rows)
        expected=('FUND NAME','TICKER','CUSIP','EX-DATE','RECORD DATE','PAYABLE DATE','DIVIDEND ($)',
                  'SHORT TERM CAPITAL GAIN ($)','LONG TERM CAPITAL GAIN ($)','FREQUENCY')
        if tuple(header)!=expected:raise ValueError('SSGA distribution schema changed')
        for row in rows:
            if row[1]!='SPY':continue
            if row[2]!='78462F103':raise ValueError('SSGA benchmark identity mismatch')
            day=dt.datetime.strptime(row[3],'%m/%d/%Y').date().isoformat()
            if wanted is not None and day not in wanted:continue
            if day in result:raise ValueError('Duplicate benchmark distribution')
            # Capital distributions have a distinct policy and cannot enter silently.
            if any(float(str(v).strip())!=0 for v in row[7:9] if str(v or '').strip() not in ('','-')):
                raise ValueError('Benchmark capital distribution needs separate review')
            result[day]={'amount':positive(float(row[6])),'source':SSGA,
                'workbook_sha256':hashlib.sha256(content).hexdigest(),'source_row':[str(v or '') for v in row]}
    finally:workbook.close()
    return result


def load_proofs(connection):
    result={}
    with connection.cursor() as cursor:
        cursor.execute("select metrics from sys_event where source='live_cash_date_review' and status='pass' order by id")
        for (metrics,) in cursor.fetchall():
            proof=metrics['proof']
            if sha(proof)!=metrics['evidence_sha256']:raise ValueError('Cash distribution proof hash mismatch')
            result.setdefault(proof['symbol'],{})[proof['date']]=proof
    return result


def check_adjustment(symbol,day,previous_raw,current_raw,factor,proofs):
    proof=proofs.get(symbol,{}).get(day)
    if proof is None:raise ValueError('Unreviewed distribution/split: '+symbol+' '+day)
    amount=positive(proof['amount'])
    if amount>=previous_raw:raise ValueError('Invalid ordinary cash distribution')
    expected=(previous_raw/(previous_raw-amount),1+amount/current_raw)
    tolerance=.01/min(previous_raw,current_raw)+1e-5
    if factor<=1 or min(abs(factor-f) for f in expected)>tolerance:
        raise ValueError('Source adjustment disagrees with declared cash: '+symbol)
