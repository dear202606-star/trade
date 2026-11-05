"""One-time reviewed current-year identity boundaries; never changes history picks."""
import datetime as dt
import json
import re
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import requests
from psycopg2.extras import Json
import db_sources
from live_strategy import sha,positive

REVIEWS={
 'BNY':('BK','2026-05-21',[.53,.53],
  ['https://www.sec.gov/Archives/edgar/data/1390777/000119312526281160/d137058d8k.htm',
   'https://www.bny.com/corporate/global/en/investor-relations/dividend-history.html']),
 'MRSH':('MMC','2026-01-14',[],
  ['https://www.marsh.com/en/corp/about/news/marsh-mclennan-to-change-nyse-symbol-to-mrsh.html']),
 'P':('PSTG','2026-04-17',[],
  ['https://www.everpuredata.com/company/newsroom/press-releases/everpure-to-change-ticker-symbol.html',
   'https://www.everpuredata.com/content/dam/pdf/en/legal/2026-form-10-k.pdf']),
 'ECHO':('SATS','2026-06-24',[],
  ['https://ir.echostar.com/news-releases/news-release-details/echostar-changing-stocker-ticker-sats-echo-marking-companys-next']),
 'FITB':('FITB','2026-06-12',[.4],
  ['https://www.sec.gov/Archives/edgar/data/35527/000119312526255417/d157060d8k.htm']),
 'KHC':('KHC','2026-09-14',[.4,.4,.4],
  ['https://www.sec.gov/Archives/edgar/data/1637459/000163745926000057/khc-20260824.htm',
   'https://ir.kraftheinzcompany.com/stock/dividend-history']),
 'SKYD':('PSKY','2026-10-06',[],
  ['https://www.sec.gov/Archives/edgar/data/2041610/000110465926113076/tm2626659d3_8k.htm',
   'https://www.sec.gov/Archives/edgar/data/2041610/000110465926115605/tm2626659d14_fwp.htm']),
 'VMRK':('EQR','2026-08-18',[.7025,.7025],
  ['https://investors.vivmarkresidential.com/sec-filings/all-sec-filings/content/0001140361-26-033377/ef20080318_8k.htm',
   'https://investors.vivmarkresidential.com/sec-filings/all-sec-filings/content/0001193125-26-326170/eqr-20260630.htm'])
}


def collect(item):
    current,(old,effective,dividends,sources)=item
    url='https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/x/US_MinKService.getDailyK?symbol='+old
    r=requests.get(url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://finance.sina.com.cn'},timeout=25)
    r.raise_for_status();match=re.search(r'\(\s*(\[.*\])\s*\)',r.text,re.S)
    if not match:raise ValueError('Missing original-code source')
    rows=json.loads(match.group(1));prices={}
    for row in rows:
        day=dt.date.fromisoformat(row['d']).isoformat()
        if '2025-12-31'<=day<effective:
            if day in prices:raise ValueError('Duplicate original-code daily date')
            prices[day]=positive(float(row['c']))
    if '2025-12-31' not in prices:raise ValueError('Missing original 2025 closing price: '+old)
    return current,{'symbol':current,'old_symbol':old,'effective_date':effective,
        'share_ratio':1,'pre_boundary_cash_distributions':dividends,
        'cash_return_scope':'bounded until source adjusted path is separately verified',
        'sources':sources,'daily_source':url,'response_sha256':sha(rows),'raw':prices,
        'scope':'2026 runtime ranking only; no historical series promotion'}


def run():
    c=db_sources.db()
    try:
        with c.cursor() as x:
            x.execute("select subject from sys_event where source='live_identity_review' and status='pass'")
            done={r[0] for r in x.fetchall()}
        targets=[r for r in REVIEWS.items() if r[0] not in done]
        with ThreadPoolExecutor(8) as pool:
            for symbol,proof in pool.map(collect,targets):
                with c.cursor() as x:
                    x.execute("""insert into sys_event(kind,source,subject,status,metrics,finished_at)
                        values('validation','live_identity_review',%s,'pass',%s,now())""",
                        (symbol,Json({'proof':proof,'evidence_sha256':sha(proof)})))
                c.commit();print('Reviewed identity stored',symbol,flush=True)
        listings={
            'FDXF':('2026-06-01','https://www.sec.gov/Archives/edgar/data/2082247/000162828026053359/fdxf-20260531.htm'),
            'HONA':('2026-06-29','https://www.sec.gov/Archives/edgar/data/2089271/000162828026045874/hona-20260625.htm'),
            'VYLR':('2026-10-01','https://www.sec.gov/Archives/edgar/data/2128626/000119312526414335/d92652d8k.htm')}
        for symbol,(day,url) in listings.items():
            if symbol in done:continue
            proof={'kind':'new_listing','symbol':symbol,'first_regular_way_date':day,'sources':[url],
                   'scope':'2026 runtime ranking; no 2025 year-end holding in this distinct security'}
            with c.cursor() as x:
                x.execute("""insert into sys_event(kind,source,subject,status,metrics,finished_at)
                    values('validation','live_identity_review',%s,'pass',%s,now())""",
                    (symbol,Json({'proof':proof,'evidence_sha256':sha(proof)})))
            c.commit()
        cash_dates=[
            {'symbol':'WDC','date':'2026-12-04','amount':.175,
             'source':'https://www.sec.gov/Archives/edgar/data/106040/000162828026066933/wdc-20261002.htm'},
            {'symbol':'NEM','date':'2026-11-25','amount':.26,
             'source':'https://investors.newmont.com/investors/news-release/news-details/2026/Newmont-Reports-Third-Quarter-2026-Results-with-Record-Third-Quarter-Adjusted-Net-Income-Positioned-to-Achieve-Full-Year-Guidance-Provides-Update-on-Red-Chris-Block-Cave-Project/default.aspx'}]
        for proof in cash_dates:
            subject=proof['symbol']+':'+proof['date']
            proof['kind']='ordinary_cash_distribution'
            proof['date_scope']='issuer record date; source ex-date must agree and adjustment must reconcile to amount'
            with c.cursor() as x:
                x.execute("select 1 from sys_event where source='live_cash_date_review' and subject=%s and status='pass'",(subject,))
                if x.fetchone():continue
                x.execute("""insert into sys_event(kind,source,subject,status,metrics,finished_at)
                    values('validation','live_cash_date_review',%s,'pass',%s,now())""",
                    (subject,Json({'proof':proof,'evidence_sha256':sha(proof)})))
            c.commit()
    finally:c.close()


if __name__=='__main__':run()
