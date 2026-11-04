"""Review ATVI's dividend-adjusted 2023 growth and actual cash exit."""
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
BASE_URL='https://www.sec.gov/Archives/edgar/data/718877/000130817923000834/latvi012_def14a.htm'
DIVIDEND_URL='https://www.sec.gov/Archives/edgar/data/718877/000162828023025102/atvi-20230719.htm'
MERGER_URL='https://www.sec.gov/Archives/edgar/data/718877/000110465923108985/tm2328253d1_8k.htm'
DATES=['2022-12-30','2023-01-31','2023-02-28','2023-03-31','2023-04-28','2023-05-31',
       '2023-06-30','2023-07-31','2023-08-31','2023-09-29','2023-10-12']


def retained_values(bars):
    base=bars['2022-12'][0]
    terminal=distribution_value(bars['2023-10'][0]/base,bars['2023-10'][1],95.,[])
    return {'2023-%02d'%m:(bars['2023-%02d'%m][0]/base if m<10 else terminal) for m in range(1,13)}


def review(cursor):
    raw=(BASE/'output/_atvi_review/stockanalysis_response.json').read_bytes();payload=json.loads(raw)
    if payload.get('status')!=200:raise ValueError('Monthly source failed')
    bars=fetch_stockanalysis.parse_rows(payload['data'])
    daily_raw=(BASE/'output/_nominal_daily/ATVI.json').read_bytes();daily=json.loads(daily_raw)
    if daily['endpoint']!='https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/x/US_MinKService.getDailyK?symbol=ATVI':
        raise ValueError('Independent security identity differs')
    monthly={};dates={}
    for row in sorted(daily['rows'],key=lambda r:r['d']):
        date=row['d'];price=float(row['c'])
        if date in dates or not math.isfinite(price) or price<=0:raise ValueError('Invalid independent observation')
        dates[date]=price;monthly[date[:7]]=(date,price)
    for date in DATES:
        if monthly[date[:7]][0]!=date or abs(monthly[date[:7]][1]-bars[date[:7]][1])>1e-8:
            raise ValueError('Native date/close mismatch')
    if bars['2022-12']!=(75.734,76.55) or bars['2023-10']!=(94.42,94.42):
        raise ValueError('Reviewed base or final quote changed')
    # Independently check the ordinary-dividend factor, allowing only the
    # provider's observed three-decimal rounding, without changing its rule.
    factor=1/(1+.99/dates['2023-08-01'])
    for month in ['2022-12']+['2023-%02d'%m for m in range(1,10)]:
        expected=bars[month][1]*(factor if month<'2023-08' else 1)
        if abs(bars[month][0]-expected)>.00050001:raise ValueError('Dividend adjustment not corroborated')
    archive=BASE/'output/_yfinance_actions2022.zip'
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=ARCHIVE_SHA:raise ValueError('Independent archive changed')
    with zipfile.ZipFile(archive) as z:
        old_rows=list(csv.DictReader(io.StringIO(z.read('ATVI.csv').decode('utf-8-sig'))))
    restored=reconstruct_nominal.restore(old_rows)
    if restored[-1]['date']!='2022-07-12':raise ValueError('Independent vintage boundary differs')
    old={}
    for row in restored:
        if row['date']<'2022-07-01':old[row['date'][:7]]=row
    common=sorted(set(old)&set(bars));deviations=[]
    native_deviations=[abs(old[m]['close']/bars[m][1]-1) for m in common]
    if not native_deviations or max(native_deviations)>.001:raise ValueError('Independent native units disagree')
    for a,b in zip(common,common[1:]):
        if (int(b[:4])-int(a[:4]))*12+int(b[5:])-int(a[5:])!=1:continue
        deviations.append(abs(math.log((bars[b][0]/bars[a][0])/(old[b]['adjusted']/old[a]['adjusted']))))
    if len(deviations)<24 or statistics.median(deviations)>.005 or sorted(deviations)[int(.9*(len(deviations)-1))]>.05:
        raise ValueError('Independent adjusted returns disagree')
    cursor.execute("select month,adj_close,raw_close,source,basis from series_selected where symbol='ATVI' order by month")
    selected=cursor.fetchall()
    if not selected:raise ValueError('Missing original security selection')
    for month,adj,close,source,basis in selected:
        if month>'2023-09' or source!='stockanalysis' or basis!='total_return' or (float(adj),float(close))!=bars[month]:
            raise ValueError('Selected source/history differs')
    cursor.execute("select metrics from sys_event where kind='validation' and source='verified_corporate_terms' and status='pass' and subject='ATVI:2023-10-13'")
    terms=cursor.fetchall()
    if len(terms)!=1:raise ValueError('Cash terms version ambiguous')
    metric=terms[0][0];fact=metric['proof']['terms']
    if metric['evidence_sha256']!=digest(metric['proof']) or fact['cash_usd_per_share']!='95.00' or fact['event_date']!='2023-10-13':
        raise ValueError('Cash terms evidence changed')
    values=retained_values(bars)
    return {'producer':'atvi_cash_merger','symbol':'ATVI','hold_year':2023,'event_date':'2023-10-13',
        'holding_event_policy':POLICY,'corporate_terms_sha256':[metric['evidence_sha256']],
        'issuer_base_fact':{'date':'2022-12-30','close':76.55,'url':BASE_URL,'location':'Outstanding Equity Awards, footnote 3'},
        'ordinary_dividend':{'cash_usd_per_share':.99,'payable_on':'2023-08-17','record_date':'2023-08-02',
                            'issuer_url':DIVIDEND_URL,'included_in_provider_growth':True,'additional_cash_added':0},
        'dividend_factor_crosscheck':{'ex_date':'2023-08-01','independent_ex_close':dates['2023-08-01'],
            'implied_factor':factor,'provider_adjusted_price_rounding_bound':.0005,
            'rule':'Existing component provider-adjusted-return convention retained'},
        'issuer_completion_url':MERGER_URL,'source_url':fetch_stockanalysis.API%'atvi',
        'source_response_sha256':hashlib.sha256(raw).hexdigest(),'explicit_monthly_source_rows':payload['data'],
        'independent_daily_sha256':hashlib.sha256(daily_raw).hexdigest(),
        'independent_target_month_ends':{d[:7]:list(monthly[d[:7]]) for d in DATES},
        'independent_archive_sha256':ARCHIVE_SHA,'native_overlap_months':len(common),'return_pairs':len(deviations),
        'native_max_relative_deviation':max(native_deviations),'native_relative_tolerance':.001,
        'median_log_deviation':statistics.median(deviations),'p90_log_deviation':sorted(deviations)[int(.9*(len(deviations)-1))],
        'selected_pre_exit_sha256':digest(selected),'native_base':76.55,'native_before':94.42,
        'cash_per_event_share':95.,'cash_held_without_interest':True,'retained_monthly_values':values,
        'return_2023':values['2023-12']-1,'return_precision_interval_from_base_rounding':[95/75.7345-1,95/75.7335-1],
        'scope':'ATVI 2023 only, at reported provider precision; no eligibility, raw-price or baseline substitution'}
