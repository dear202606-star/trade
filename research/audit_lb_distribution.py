"""LB/BBWI native-price proof and actual-share distribution evidence.

--write stores only proved pre-rename screening observations. The separately
reviewed event path never replaces quoted prices or published baselines here.
"""
import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
import statistics
import sys
import zipfile
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import reconstruct_nominal
from holding_consideration import distribution_value, POLICY
import db_select
import psycopg2.extras

TERMS='https://www.sec.gov/Archives/edgar/data/701985/000114036121026658/nt10026698x7_8k.htm'
OPENS='https://bbwinc.gcs-web.com/static-files/b771f363-3863-4b55-b2ef-2d1e6dee794e'
DIVIDENDS='https://www.sec.gov/Archives/edgar/data/1856437/000185643726000004/vsco-20260131.htm'
CHILD_MONTHLY='https://www.digrin.com/stocks/detail/VSCO/price'


def child_monthly_evidence(text):
    class TableRows(HTMLParser):
        def __init__(self):
            super().__init__();self.rows=[];self.row=None;self.cell=None
        def handle_starttag(self,tag,attrs):
            if tag=='tr':self.row=[]
            if tag in ('td','th') and self.row is not None:self.cell=[]
        def handle_data(self,data):
            if self.cell is not None:self.cell.append(data)
        def handle_endtag(self,tag):
            if tag in ('td','th') and self.cell is not None:
                self.row.append(''.join(self.cell).strip());self.cell=None
            if tag=='tr' and self.row is not None:
                self.rows.append(self.row);self.row=None
    parser=TableRows();parser.feed(text)
    if ['Date','Adjusted price','Real price'] not in parser.rows:
        raise ValueError('independent monthly table schema differs')
    result={}
    for row in parser.rows:
        if len(row)!=3:continue
        try:month=dt.datetime.strptime(row[0],'%B %Y').strftime('%Y-%m')
        except ValueError:continue
        if not '2021-08'<=month<='2021-12':continue
        adjusted,real=[float(v.replace('$','').replace(',','')) for v in row[1:]]
        if month in result or not math.isfinite(real) or real<=0 or adjusted!=real:
            raise ValueError('unresolved child monthly units/dividends')
        result[month]=real
    if set(result)!={'2021-%02d'%m for m in range(8,13)}:
        raise ValueError('independent child holding months are incomplete')
    return result


def run(write=False):
    archive=BASE/'output/_yfinance_actions2022.zip'
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    receipt=json.loads((BASE/'output/yfinance_actions2022_review.json').read_text())
    if digest!=receipt['archive_sha256']:
        raise RuntimeError('Archive fingerprint differs')
    with zipfile.ZipFile(archive) as source,source.open('BBWI.csv') as file:
        rows=list(csv.DictReader(io.TextIOWrapper(file,encoding='utf-8-sig')))
    restored=reconstruct_nominal.restore(rows)
    if restored[-1]['date']!='2022-07-12':
        raise RuntimeError('Unexpected source vintage')
    daily={r['date']:r for r in restored}
    monthly={}
    for row in restored:
        month=row['date'][:7]
        if '2008-01'<=month<='2021-07':
            monthly[month]=row
    wiki=json.loads((BASE/'output/wiki_monthly_archive.json').read_text())['monthly']['LB']
    common=sorted(m for m in monthly if m<'2018-03' and m in wiki
                  and monthly[m]['date']==wiki[m]['date'])
    deviations=[abs(monthly[m]['close']/wiki[m]['close']-1) for m in common]
    returns=[abs(math.log((monthly[b]['adjusted']/monthly[a]['adjusted'])/
              (wiki[b]['adj_close']/wiki[a]['adj_close']))) for a,b in zip(common,common[1:])]
    if len(common)<24 or statistics.median(deviations)>.001 or max(deviations)>.01:
        raise RuntimeError('Old LB native-price overlap failed')
    median=statistics.median(returns)
    p90=sorted(returns)[int((len(returns)-1)*.9)]
    if median>.005 or p90>.05:
        raise RuntimeError('Old LB independent return overlap failed')
    original={r['Date']:r for r in rows}
    child_path=BASE/'output/_nominal_daily/VSCO.json'
    if not child_path.exists():
        from research import audit_nominal_prices as collector
        collector.CACHE.mkdir(exist_ok=True)
        collector.review('VSCO',{})  # Raw evidence only, not an approved screening series.
    child=json.loads(child_path.read_text())
    child_daily={r['d']:r for r in child['rows']}
    independent_path=BASE/'output/_holding_evidence/VSCO_digrin_price.html'
    if not independent_path.exists():
        independent_path.parent.mkdir(exist_ok=True)
        with urllib.request.urlopen(CHILD_MONTHLY,timeout=20) as response:
            html=response.read(1024**2+1)
        if len(html)>1024**2:raise RuntimeError('independent price page exceeds bound')
        independent_path.write_bytes(html)
    independent=child_monthly_evidence(independent_path.read_text())
    child_months={}
    for day,row in sorted(child_daily.items()):
        if '2021-08'<=day[:7]<='2021-12':child_months[day[:7]]=row
    if set(child_months)!=set(independent) or any(abs(float(child_months[m]['c'])-p)>.005 for m,p in independent.items()):
        raise RuntimeError('independent child month-end prices disagree')
    start,before,event,end='2020-12-31','2021-08-02','2021-08-03','2021-12-31'
    parent_open=float(original[event]['Open'])/float(original[event]['Close'])*daily[event]['close']
    child_open=float(child_daily[event]['o'])
    if abs(parent_open-64.5)>.001 or child_open!=48:
        raise RuntimeError('Issuer Form 8937 native opening-price anchors failed')
    retained_value=distribution_value(daily[before]['adjusted']/daily[start]['adjusted'],daily[before]['close'],0,[
        {'shares':1,'native_entry':64.5,'growth':daily[end]['adjusted']/float(original[event]['Open'])},
        {'shares':1/3,'native_entry':48,'growth':float(child_daily[end]['c'])/child_open}])
    valuation_months={m:max((r for r in restored if r['date'][:7]==m),key=lambda r:r['date'])
                      for m in ['2021-%02d'%i for i in range(1,13)]}
    holding_values={}
    for month,row in valuation_months.items():
        if month<'2021-08':value=row['adjusted']/daily[start]['adjusted']
        else:
            if child_months[month]['d']!=row['date']:
                raise RuntimeError('parent/child valuation dates disagree')
            value=distribution_value(daily[before]['adjusted']/daily[start]['adjusted'],daily[before]['close'],0,[
                {'shares':1,'native_entry':64.5,'growth':row['adjusted']/float(original[event]['Open'])},
                {'shares':1/3,'native_entry':48,'growth':float(child_months[month]['c'])/child_open}])
        holding_values[month]=value
    result={'scope':'old LB native-price proof plus sensitivity; no return-price or baseline writes',
        'producer':'lb_distribution','symbol':'LB','hold_year':2021,'event_date':'2021-08-03',
        'same_common_equity_tickers':['LB','BBWI'],
        'consideration':{'cash_per_old_share':0,'successor_shares':{'BBWI':1,'VSCO':1/3}},
        'issuer_terms':TERMS,'native_open_anchors_source':OPENS,'child_no_cash_dividends_source':DIVIDENDS,
        'archive_sha256':digest,'child_daily_sha256':hashlib.sha256(json.dumps(child['rows'],separators=(',',':')).encode()).hexdigest(),
        'child_independent_source':CHILD_MONTHLY,
        'child_independent_html_sha256':hashlib.sha256(independent_path.read_bytes()).hexdigest(),
        'child_independently_matched_holding_months':independent,
        'native_overlap_months':len(common),'native_median_relative_deviation':statistics.median(deviations),
        'native_max_relative_deviation':max(deviations),'native_2020_12':monthly['2020-12']['close'],
        'parent_provider_adjusted_2021_return':daily[end]['adjusted']/daily[start]['adjusted']-1,
        'holding_event_policy':POLICY,
        'retained_monthly_values':holding_values,
        'retain_both_components_2021_illustration':retained_value-1,
        'illustration_limits':['continuous fractional holdings, zero fees/tax as in existing theoretical weights',
            'ordinary dividends follow component provider-adjusted-return convention',
            'child proof restricted to 2021-08 through 2021-12; pre-IPO and later renamed-ticker quotes excluded',
            'event value is a holding value, not a tradable original-ticker quote'],
        'native_write':write,'native_stored_rows':0}
    if write:
        observations=[('LB',m,r['close'],r['date'],'kaggle_yfinance2022',
            receipt['source_url']+'?select=BBWI.csv',dt.datetime.now(dt.timezone.utc),digest,len(returns),median,p90,
            'Old LB/L Brands, not LandBridge; BBWI same common stock '+TERMS+'; WIKI LB native overlap '+str(len(common)))
            for m,r in monthly.items() if abs(r['close']-5)>=.01]
        connection=db_select.db();connection.autocommit=False
        try:
            with connection,connection.cursor() as cursor:
                psycopg2.extras.execute_values(cursor,
                    '''insert into nominal_monthly(symbol,month,close,trade_date,source,source_url,
                       fetched_at,evidence_sha256,crosscheck_pairs,median_log_deviation,p90_log_deviation,
                       unit_evidence) values %s on conflict(symbol,month,source) do nothing''',observations,page_size=500)
                cursor.execute("select count(*) from nominal_monthly where symbol='LB' and source='kaggle_yfinance2022'")
                result['native_stored_rows']=cursor.fetchone()[0]
        finally:
            connection.close()
    (BASE/'output/lb_distribution_review.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--write',action='store_true')
    run(parser.parse_args().write)
