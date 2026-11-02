"""Collect fixed Kaggle v89 and audit its event-based nominal reconstruction.

No database or strategy writes. Uses one bounded official archive download.
"""
import csv
import hashlib
import io
import json
import math
import statistics
import sys
import urllib.request
import zipfile
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import import_kaggle_cc0 as kaggle
import reconstruct_nominal
import db_prices

ARCHIVE=BASE/'output/_yfinance_actions2022.zip'
SOURCE_URL='https://www.kaggle.com/datasets/rprkh15/sp500-stock-prices/versions/89'


def download():
    if ARCHIVE.exists():
        return
    request=urllib.request.Request(
        'https://www.kaggle.com/api/v1/datasets/download/rprkh15/sp500-stock-prices?datasetVersionNumber=89',
        headers={'Authorization':'Bearer '+kaggle._token()})
    temporary=ARCHIVE.with_suffix('.part')
    size=0
    try:
        with urllib.request.build_opener(kaggle._SafeRedirect()).open(request,timeout=30) as response,temporary.open('wb') as output:
            while True:
                chunk=response.read(1024**2)
                if not chunk:
                    break
                size+=len(chunk)
                if size>256*1024**2:
                    raise RuntimeError('bounded archive download exceeded')
                output.write(chunk)
        temporary.replace(ARCHIVE)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def run():
    download()
    digest=hashlib.sha256()
    with ARCHIVE.open('rb') as file:
        for chunk in iter(lambda:file.read(1024**2),b''):
            digest.update(chunk)
    prices=db_prices.load_all()
    stamp=db_prices._db_stamp()
    wiki=json.loads((BASE/'output/wiki_monthly_archive.json').read_text())['monthly']
    results={}
    with zipfile.ZipFile(ARCHIVE) as archive:
        files=archive.infolist()
        if len(files)>1000 or sum(f.file_size for f in files)>512*1024**2:
            raise RuntimeError('unexpected archive size')
        for index,entry in enumerate(files):
            if not entry.filename.endswith('.csv'):
                continue
            symbol=Path(entry.filename).stem
            if symbol not in prices:
                continue
            try:
                with archive.open(entry) as raw:
                    rows=list(csv.DictReader(io.TextIOWrapper(raw,encoding='utf-8-sig')))
                restored=reconstruct_nominal.restore(rows)
                if restored[-1]['date']!='2022-07-12':
                    raise ValueError('terminal quote differs from fixed snapshot date')
                monthly={}
                for day in restored:
                    month=day['date'][:7]
                    if '2008-01'<=month<'2022-07':
                        monthly[month]=day
                common=sorted(set(monthly)&set(prices[symbol]))
                devs=[]
                for before,after in zip(common,common[1:]):
                    if (int(after[:4])-int(before[:4]))*12+int(after[5:])-int(before[5:])!=1:
                        continue
                    ratio=(monthly[after]['adjusted']/monthly[before]['adjusted'])/(prices[symbol][after][0]/prices[symbol][before][0])
                    devs.append(abs(math.log(ratio)))
                median=statistics.median(devs) if devs else None
                p90=sorted(devs)[int((len(devs)-1)*.9)] if devs else None
                native_devs=[abs(monthly[m]['close']/w['close']-1)
                             for m,w in wiki.get(symbol,{}).items()
                             if m<'2018-03' and m in monthly and monthly[m]['date']==w['date']]
                native_median=statistics.median(native_devs) if native_devs else None
                native_max=max(native_devs) if native_devs else None
                accepted=(len(devs)>=24 and median<=.005 and p90<=.02
                          and (not native_devs or (native_median<=.001 and native_max<=.02)))
                # Do not decide a $5 crossing from floating-point inversion.
                ambiguous=[m for m,r in monthly.items() if abs(r['close']-5)<.01]
                results[symbol]={'status':'crosscheck_pass' if accepted else 'review_required',
                    'overlap_returns':len(devs),'median_log_deviation':median,'p90_log_deviation':p90,
                    'native_overlap_months':len(native_devs),'native_median_relative_deviation':native_median,
                    'native_max_relative_deviation':native_max,'ambiguous_threshold_months':ambiguous,
                    'action_only_rows':sum(r['Close']=='' for r in rows),
                    'snapshot_last_date':restored[-1]['date'],
                    'file':entry.filename,
                    'months':{m:{'date':r['date'],'close':r['close']} for m,r in monthly.items()
                              if accepted and m not in ambiguous}}
            except (ValueError,KeyError,OverflowError) as exc:
                results[symbol]={'status':'unavailable','reason':str(exc)}
            if index%50==0:
                print('reviewed',index+1,'archive entries',flush=True)
    if tuple(stamp or ())!=tuple(db_prices._db_stamp() or ()):
        raise RuntimeError('selected input changed during audit')
    result={'dataset':'rprkh15/sp500-stock-prices','version':89,'source_url':SOURCE_URL,
            'archive_sha256':digest.hexdigest(),'snapshot_stamp':stamp,
            'method':'same-vintage inverse dividend factors and subsequent split ratios; terminal D=1',
            'statuses':{s:sum(r['status']==s for r in results.values()) for s in sorted({r['status'] for r in results.values()})},
            'results':results}
    (BASE/'output/yfinance_actions2022_review.json').write_text(json.dumps(result,separators=(',',':'))+'\n')
    print(json.dumps(result['statuses']),flush=True)


if __name__=='__main__':
    run()
