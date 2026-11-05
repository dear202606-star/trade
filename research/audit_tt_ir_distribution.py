"""Original Ingersoll-Rand ordinary equity retained as TT plus distributed IR."""
import csv
import hashlib
import io
import json
import math
import sys
import zipfile
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from holding_consideration import distribution_value,POLICY

ARCHIVE_SHA='c8a562dfe260afa595c5e9fdb570e79350125204c6bfa3217d7bb9b6280f38d6'
PDFS={
 'parent_proxy_2020.pdf':('124ffde2d6c00026c8e0cd571ed8f5414048b5e33492334db40307f75b19d05b',59,
   'https://s2.q4cdn.com/950394465/files/doc_financial/annual/2019/372176%281%29_16_Trane-Technologies_Combo_WR_R1.pdf'),
 'parent_annual_2020.pdf':('8617fda23d285d23c687b5edcc7ad33e049c591c1a7d01a469a8e29c8a152c28',119,
   'https://s2.q4cdn.com/950394465/files/doc_financials/2020/ar/Trane-Technologies-2020-Annual-Report-Web-Ready-4.16.2021v2.pdf'),
 'child_annual_2020.pdf':('cc92ea284e52b981b6d25bca54d0e39d6c98b4b7abaf69e11768ffc4a29b4606',37,
   'https://s23.q4cdn.com/965037804/files/doc_financials/2020/ar/Ingersoll-Rand-%2720-Annual-Report-and-%2720-10-K-Final-4-28-21.pdf'),
 'distribution_tax_2020.pdf':('fbedcd404af5b07e73f86965a50c3e0613578471bf98a2c06cc8da4280bbcb8f',1,
   'https://s2.q4cdn.com/950394465/files/doc_downloads/industrial_filings/Form_8937_Attachment_Trane_Technologies_plc_2_March_2020_updated_as_of_March_2nd.pdf')}
DIVIDENDS={d:.53 for d in ('2020-03-12','2020-06-04','2020-09-03','2020-12-03')}


def file_sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(1024**2),b''):digest.update(chunk)
    return digest.hexdigest()


def positive(value):
    if isinstance(value,bool) or not math.isfinite(value) or value<=0:raise ValueError('Invalid original-equity price')
    return value


def retained_values(inputs):
    base=positive(inputs['original_native']['2019-12-31'])
    if base!=132.92:raise ValueError('Original issuer year-end cost differs')
    native_before=positive(inputs['original_native']['2020-02-28'])
    dates=inputs['month_ends'];parent=inputs['parent_native'];child=inputs['child_native']
    adjusted=inputs['parent_adjusted'];anchor='2020-03-02'
    result={}
    for month in range(1,13):
        key='2020-%02d'%month;day=dates[key]
        if month<3:result[key]=positive(inputs['original_native'][day])/base
        else:result[key]=distribution_value(native_before/base,native_before,0,[
            {'shares':1,'native_entry':positive(parent[anchor]),
             'growth':positive(adjusted[day])/positive(adjusted[anchor])},
            {'shares':.8824,'native_entry':positive(child[anchor]),
             'growth':positive(child[day])/positive(child[anchor])}])
    return result


def factor_errors(adjusted,native,cash):
    if set(adjusted)!=set(native):raise ValueError('Independent daily coverage differs')
    dates=sorted(native);errors=[]
    for a,b in zip(dates,dates[1:]):
        actual=(positive(adjusted[b])/positive(native[b]))/(positive(adjusted[a])/positive(native[a]))
        dividend=cash.get(b,0)
        if not math.isfinite(dividend) or not 0<=dividend<native[a]:raise ValueError('Invalid cash dividend')
        expected=1/(1-dividend/native[a])
        errors.append(abs(math.log(actual/expected)))
    if not errors or max(errors)>1e-6:
        raise ValueError('Adjustment contains an unreviewed cash or share event')
    return {'daily_transitions':len(errors),'max_log_deviation':max(errors),'tolerance':1e-6}


def review():
    import fitz
    root=BASE/'output/_tt_ir_2020';primary=[];texts={}
    for name,(sha,page,url) in PDFS.items():
        if file_sha(root/name)!=sha:raise ValueError('Issuer document changed: '+name)
        with fitz.open(root/name) as document:texts[name]=' '.join(document[page-1].get_text().split())
        primary.append({'url':url,'sha256':sha,'pdf_page':page})
    if ('132.92' not in texts['parent_proxy_2020.pdf']
        or 'closing market price' not in texts['parent_proxy_2020.pdf']
        or 'December 31, 2019' not in texts['parent_proxy_2020.pdf']):raise ValueError('Original-share cost proof missing')
    if 'Dividends declared per ordinary share $ 2.12' not in texts['parent_annual_2020.pdf']:
        raise ValueError('Parent ordinary dividend total missing')
    child_text=texts['child_annual_2020.pdf']
    if ('We did not declare or pay dividends' not in child_text
        or 'December 31, 2020 and 2019' not in child_text):raise ValueError('Child no-dividend proof missing')
    terms=texts['distribution_tax_2020.pdf']
    if ('one (1) share of IR US HoldCo common stock for every (1) share' not in terms
        or '0.8824:1 New IR to IR US HoldCo' not in terms
        or 'February 29, 2020' not in terms):raise ValueError('Actual distribution units missing')
    archive=BASE/'output/_yfinance_actions2022.zip'
    if file_sha(archive)!=ARCHIVE_SHA:raise ValueError('Independent source vintage changed')
    vintage={}
    with zipfile.ZipFile(archive) as z:
        for symbol in ('TT','IR'):
            with z.open(symbol+'.csv') as file:
                rows=list(csv.DictReader(io.TextIOWrapper(file,encoding='utf-8-sig')))
            chosen={r['Date'][:10]:r for r in rows if '2019-12-31'<=r['Date'][:10]<='2020-12-31'}
            if len(chosen)!=sum('2019-12-31'<=r['Date'][:10]<='2020-12-31' for r in rows):
                raise ValueError('Duplicate source observation')
            vintage[symbol]=chosen
    caches={};native={}
    for symbol in ('IR','TT','SPY'):
        path=BASE/'output/_nominal_daily'/(symbol+'.json');caches[symbol]=file_sha(path)
        body=json.loads(path.read_text())
        if body['endpoint']!='https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/x/US_MinKService.getDailyK?symbol='+symbol:
            raise ValueError('Independent source security route differs')
        rows=[r for r in body['rows'] if '2019-12-31'<=r['d']<='2020-12-31']
        native[symbol]={r['d']:positive(float(r['c'])) for r in rows}
        if len(native[symbol])!=len(rows):raise ValueError('Duplicate native daily observation')
    days=set(native['SPY']);before={d for d in days if d<'2020-03-02'};after=days-before
    original={d:p for d,p in native['IR'].items() if d<'2020-03-02'}
    parent={d:p for d,p in native['TT'].items() if d>='2020-03-02'}
    child={d:p for d,p in native['IR'].items() if d>='2020-03-02'}
    if set(original)!=before or set(parent)!=after or set(child)!=after:
        raise ValueError('Incomplete same-session original/parent/child daily observations')
    original_adjusted={d:float(vintage['TT'][d]['Close']) for d in before}
    parent_adjusted={d:float(vintage['TT'][d]['Close']) for d in after}
    child_adjusted={d:float(vintage['IR'][d]['Close']) for d in after}
    parent_cash={d:float(r['Dividends']) for d,r in vintage['TT'].items() if float(r['Dividends'])}
    split_events={d:float(r['Stock Splits']) for d,r in vintage['TT'].items() if float(r['Stock Splits'])}
    if parent_cash!=DIVIDENDS or split_events!={'2020-03-02':1.289}:
        raise ValueError('Unreviewed parent event schedule')
    if any(float(vintage['IR'][d]['Dividends']) or float(vintage['IR'][d]['Stock Splits']) for d in after):
        raise ValueError('Child cash/share actions contradict issuer evidence')
    errors={'original_pre_event':factor_errors(original_adjusted,original,{}),
            'retained_parent':factor_errors(parent_adjusted,parent,DIVIDENDS),
            'distributed_child':factor_errors(child_adjusted,child,{})}
    if parent['2020-03-02']!=100.55 or child['2020-03-02']!=32.80:
        raise ValueError('First post-event native close differs')
    # Independent issuer opening-price anchors identify the two share units.
    for symbol,price in [('TT',100.60),('IR',33.06)]:
        body=json.loads((BASE/'output/_nominal_daily'/(symbol+'.json')).read_text())
        row=next(r for r in body['rows'] if r['d']=='2020-03-02')
        if float(row['o'])!=price:raise ValueError('Issuer Form 8937 opening-price anchor differs')
    month_ends={}
    for day in sorted(days):
        if day.startswith('2020'):month_ends[day[:7]]=day
    if len(month_ends)!=12:raise ValueError('Incomplete calendar-year month ends')
    inputs={'original_native':original,'original_adjusted':original_adjusted,
            'parent_native':parent,'parent_adjusted':parent_adjusted,'parent_cash':DIVIDENDS,
            'child_native':child,'child_adjusted':child_adjusted,'month_ends':month_ends}
    values=retained_values(inputs)
    return {'producer':'tt_ir_distribution','symbol':'TT','hold_year':2020,'event_date':'2020-02-29',
        'first_regular_way_date':'2020-03-02','holding_event_policy':POLICY,
        'original_issuer_cik':'1466258','distributed_issuer_cik':'1699150',
        'original_quote_symbol_before_event':'IR','consideration':{'cash_per_old_share':0,
            'successor_shares':{'TT':1,'IR':.8824}},'issuer_sources':primary,
        'source_archive_sha256':ARCHIVE_SHA,'native_cache_sha256':caches,
        'daily_factor_checks':errors,'daily_inputs':inputs,'retained_monthly_values':values,
        'retained_2020_return':values['2020-12']-1,
        'parent_provider_adjusted_return':parent_adjusted['2020-12-31']/original_adjusted['2019-12-31']-1,
        'vendor_synthetic_split_not_applied_to_actual_shares':1.289,
        'ordinary_cash_policy':'Component provider-adjusted returns; no second addition of parent dividends',
        'fractional_holding_convention':'Continuous fractional ownership, zero fees and taxes as in existing accepted paths',
        'scope':'Only original CIK1466258 equity held over calendar 2020, represented by retained TT; not a path for new IR, not index-membership acceptance'}


if __name__=='__main__':
    proof=review()
    print(json.dumps({k:proof[k] for k in ('symbol','hold_year','retained_2020_return','parent_provider_adjusted_return','daily_factor_checks')}))
