"""Review the original TWTR common equity and its 2022 cash holding path."""
import csv
import hashlib
import io
import json
import math
import statistics
import sys
import zipfile
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import fetch_stockanalysis
import reconstruct_nominal
from holding_consideration import distribution_value,POLICY
from research.apply_recent_index_changes import digest

ARCHIVE_SHA='c8a562dfe260afa595c5e9fdb570e79350125204c6bfa3217d7bb9b6280f38d6'
BASE_FACT={'date':'2021-12-31','close':43.22,'cik':'1418091',
    'url':'https://www.sec.gov/Archives/edgar/data/1418091/000114036122014049/ny20001921x3_def14a.htm',
    'location':'Outstanding Equity Awards, footnote 2; closing common-stock price on last trading day'}
RIGHTS_URL='https://www.sec.gov/Archives/edgar/data/1418091/000119312522120461/d310843dex42.htm'
MERGER_URL='https://www.sec.gov/Archives/edgar/data/1418091/000119312522272772/d411753d8k.htm'
DATES=['2021-12-31','2022-01-31','2022-02-28','2022-03-31','2022-04-29','2022-05-31',
       '2022-06-30','2022-07-29','2022-08-31','2022-09-30','2022-10-27']


def retained_values(bars,native_before,cash):
    base=bars['2021-12'][0]
    terminal=distribution_value(bars['2022-10'][0]/base,native_before,cash,[])
    return {'2022-%02d'%m:(bars['2022-%02d'%m][0]/base if m<10 else terminal) for m in range(1,13)}


def review(cursor):
    raw=(BASE/'output/_twtr_review/stockanalysis_response.json').read_bytes()
    payload=json.loads(raw)
    if payload.get('status')!=200:raise ValueError('Unsuccessful monthly response')
    source_rows=payload['data']
    bars=fetch_stockanalysis.parse_rows(source_rows)
    daily_raw=(BASE/'output/_nominal_daily/TWTR.json').read_bytes();daily=json.loads(daily_raw)
    if daily['endpoint']!='https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/x/US_MinKService.getDailyK?symbol=TWTR':
        raise ValueError('Independent equity identity mismatch')
    monthly={};seen=set()
    for row in sorted(daily['rows'],key=lambda r:r['d']):
        date=row['d'];price=float(row['c'])
        if date in seen or not math.isfinite(price) or price<=0:raise ValueError('Invalid daily observation')
        seen.add(date);monthly[date[:7]]=(date,price)
    for date in DATES:
        if monthly[date[:7]][0]!=date or abs(monthly[date[:7]][1]-bars[date[:7]][1])>1e-8:
            raise ValueError('Target month date/native close differs')
        if bars[date[:7]][0]!=bars[date[:7]][1]:raise ValueError('Unexpected 2022 share/dividend adjustment')
    if bars['2021-12']!=(43.22,43.22) or monthly['2022-10']!=('2022-10-27',53.70):
        raise ValueError('Issuer base or final native quote changed')
    archive=BASE/'output/_yfinance_actions2022.zip'
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=ARCHIVE_SHA:raise ValueError('Independent vintage changed')
    with zipfile.ZipFile(archive) as z:
        old_rows=list(csv.DictReader(io.StringIO(z.read('TWTR.csv').decode('utf-8-sig'))))
    reconstructed=reconstruct_nominal.restore(old_rows)
    if reconstructed[-1]['date']!='2022-07-12':raise ValueError('Independent vintage boundary differs')
    overlap={}
    for row in reconstructed:
        if row['date']<'2022-07-01':overlap[row['date'][:7]]=row
    common=sorted(set(overlap)&set(bars));deviations=[]
    for month in common:
        if abs(overlap[month]['close']/bars[month][1]-1)>.00001:raise ValueError('Independent native share units differ')
    for a,b in zip(common,common[1:]):
        if (int(b[:4])-int(a[:4]))*12+int(b[5:])-int(a[5:])!=1:continue
        deviations.append(abs(math.log((bars[b][0]/bars[a][0])/(overlap[b]['adjusted']/overlap[a]['adjusted']))))
    if len(deviations)<24 or statistics.median(deviations)>.005 or sorted(deviations)[int(.9*(len(deviations)-1))]>.05:
        raise ValueError('Independent full-return overlap failed')
    cursor.execute("select month,adj_close,raw_close,source,basis from series_selected where symbol='TWTR' order by month")
    selected=cursor.fetchall()
    if not selected:raise ValueError('Missing selected equity history')
    for month,adj,close,source,basis in selected:
        if month>'2022-09' or source!='stockanalysis' or basis!='total_return' or (float(adj),float(close))!=bars[month]:
            raise ValueError('Selected original security/source differs')
    cursor.execute("select metrics from sys_event where kind='validation' and source='verified_corporate_terms' and status='pass' and subject='TWTR:2022-10-27'")
    terms=cursor.fetchall()
    if len(terms)!=1:raise ValueError('Cash terms version ambiguous')
    metrics=terms[0][0];fact=metrics['proof']['terms']
    if metrics['evidence_sha256']!=digest(metrics['proof']) or fact['cash_usd_per_share']!='54.20' or fact['event_date']!='2022-10-27':
        raise ValueError('Cash terms review changed')
    values=retained_values(bars,53.70,54.20)
    return {'producer':'twtr_cash_merger','symbol':'TWTR','hold_year':2022,'event_date':'2022-10-27',
        'holding_event_policy':POLICY,'issuer_base_fact':BASE_FACT,'issuer_completion_url':MERGER_URL,
        'rights_review':{'url':RIGHTS_URL,'location':'New Section 38, final paragraph',
                        'outcome':'Rights void immediately before effective merger; no separate terminal successor asset'},
        'corporate_terms_sha256':[metrics['evidence_sha256']],
        'source_url':fetch_stockanalysis.API%'twtr','source_response_sha256':hashlib.sha256(raw).hexdigest(),
        'explicit_monthly_source_rows':source_rows,'independent_daily_sha256':hashlib.sha256(daily_raw).hexdigest(),
        'independent_target_month_ends':{d[:7]:list(monthly[d[:7]]) for d in DATES},
        'independent_archive_sha256':ARCHIVE_SHA,'native_overlap_months':len(common),'return_pairs':len(deviations),
        'median_log_deviation':statistics.median(deviations),'p90_log_deviation':sorted(deviations)[int(.9*(len(deviations)-1))],
        'selected_pre_exit_sha256':digest(selected),'native_base':43.22,'native_before':53.70,
        'cash_per_original_share':54.20,'cash_held_without_interest':True,
        'retained_monthly_values':values,'return_2022':values['2022-12']-1,
        'last_quote_carry_counterfactual_return':53.70/43.22-1,
        'scope':'Only TWTR ordinary common equity held from 2021 year-end through 2022 year-end; no price/eligibility substitution'}
