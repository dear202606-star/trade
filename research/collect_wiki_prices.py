"""Official Kaggle mirror of Quandl WIKI: retain raw/adjusted month ends.

Local archive only. No database, source decisions, locks, or baseline writes.
"""
import csv
import hashlib
import io
import json
import math
import sys
import urllib.request
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import import_kaggle_cc0 as kaggle

FILE = BASE/'output/_wiki_prices.zip'
MAX_DOWNLOAD = 650 * 1024**2
MAX_UNPACKED = 2 * 1024**3


def run():
    if not FILE.exists():
        request = urllib.request.Request(
            'https://www.kaggle.com/api/v1/datasets.DatasetApiService/DownloadDataset',
            data=json.dumps({'ownerSlug':'marketneutral',
                'datasetSlug':'quandl-wiki-prices-us-equites','fileName':'WIKI_PRICES.csv'}).encode(),
            headers={'Authorization':'Bearer '+kaggle._token(), 'Content-Type':'application/json'})
        temp = FILE.with_suffix('.part')
        count = 0
        try:
            with urllib.request.build_opener(kaggle._SafeRedirect()).open(request,timeout=30) as response, temp.open('wb') as output:
                while True:
                    chunk = response.read(1024**2)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > MAX_DOWNLOAD:
                        raise RuntimeError('Archive exceeds bounded download limit')
                    output.write(chunk)
                    if count % (50*1024**2) == 0:
                        print('downloaded %d MiB' % (count//1024**2), flush=True)
            temp.replace(FILE)
        except Exception:
            temp.unlink(missing_ok=True)
            raise
    digest = hashlib.sha256()
    with FILE.open('rb') as file:
        for chunk in iter(lambda: file.read(1024**2), b''):
            digest.update(chunk)
    monthly, events, previous, split_checks = {}, {}, {}, []
    with zipfile.ZipFile(FILE) as archive:
        files = archive.infolist()
        if len(files) != 1 or Path(files[0].filename).name != 'WIKI_PRICES.csv' or files[0].file_size > MAX_UNPACKED:
            raise RuntimeError('Unexpected archive content')
        with archive.open(files[0]) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw,encoding='utf-8-sig'))
            print('columns',reader.fieldnames,flush=True)
            if not {'ticker','date','close','adj_close'}.issubset(reader.fieldnames or []):
                raise RuntimeError('Unexpected CSV schema')
            count = 0
            for row in reader:
                count += 1
                if count % 5000000 == 0:
                    print('parsed %d rows' % count,flush=True)
                day = row['date'][:10]
                if not '2008-01-01' <= day < '2018-04-01':
                    continue
                close, adj = float(row['close']), float(row['adj_close'])
                if not all(math.isfinite(v) and v > 0 for v in (close,adj)):
                    raise RuntimeError('Nonpositive/nonfinite raw or adjusted price')
                symbol, month = row['ticker'], day[:7]
                split = float(row['split_ratio'])
                dividend = float(row['ex-dividend'])
                if not math.isfinite(split) or split <= 0 or not math.isfinite(dividend):
                    raise RuntimeError('Invalid corporate action')
                if split != 1 or dividend != 0:
                    events.setdefault(symbol,[]).append({'date':day,'split_ratio':split,'dividend':dividend})
                prior = previous.get(symbol)
                if split != 1 and prior and prior['date'] < day:
                    split_checks.append({'symbol':symbol,'date':day,'split_ratio':split,
                        'raw_before':prior['close'],'raw_after':close,
                        'adjusted_ratio':adj/prior['adj_close'],
                        'raw_ratio_times_split':close/prior['close']*split})
                previous[symbol] = {'date':day,'close':close,'adj_close':adj}
                current = monthly.setdefault(symbol,{}).get(month)
                if current is None or day > current['date']:
                    monthly[symbol][month] = {'date':day, 'close':close, 'adj_close':adj}
                elif day == current['date']:
                    raise RuntimeError('Duplicate ticker/date')
    result = {'dataset':'marketneutral/quandl-wiki-prices-us-equites','version':1,
              'source_url':'https://www.kaggle.com/datasets/marketneutral/quandl-wiki-prices-us-equites',
              'archive_sha256':digest.hexdigest(),'license_metadata':'Unknown; dataset card describes public domain origin',
              'symbols':len(monthly),'parsed_daily_rows':count,'monthly':monthly,
              'events':events,'raw_unit_split_checks':split_checks}
    (BASE/'output/wiki_monthly_archive.json').write_text(json.dumps(result,separators=(',',':'))+'\n')
    print('retained %d symbols' % len(monthly),flush=True)


if __name__ == '__main__':
    run()
