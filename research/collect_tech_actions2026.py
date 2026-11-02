"""Audit 20 fixed free event archives against independent nominal prices.

Only missing screening observations may be written. Never changes return
prices, chosen return sources, locked inputs or published strategy outputs.
"""
import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
import sys
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import db_prices
import db_select
import import_kaggle_cc0 as kaggle
from research.collect_yfinance_actions2023 import review

CACHE = BASE/'output/_tech_actions2026'


def adjusted_rows(rows):
    if not rows:
        raise ValueError('empty archive')
    close, adjusted = float(rows[-1]['Close']), float(rows[-1]['Adj Close'])
    if not all(math.isfinite(x) and x > 0 for x in (close, adjusted)) or abs(adjusted/close-1) > 1e-7:
        raise ValueError('terminal adjustment factor is not one')
    return [dict(r, Close=r['Adj Close']) for r in rows]


def download(item):
    CACHE.mkdir(exist_ok=True)
    target = CACHE/(item['symbol']+'-v'+str(item['version'])+'.zip')
    if not target.exists():
        request = urllib.request.Request(
            'https://www.kaggle.com/api/v1/datasets/download/'+item['dataset']+
            '?datasetVersionNumber='+str(item['version']),
            headers={'Authorization': 'Bearer '+kaggle._token()})
        with urllib.request.build_opener(kaggle._SafeRedirect()).open(request, timeout=30) as response:
            content = response.read(5*1024**2+1)
        if len(content) > 5*1024**2:
            raise RuntimeError('archive exceeds download bound')
        temporary = target.with_suffix('.part')
        temporary.write_bytes(content)
        temporary.replace(target)
    content = target.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if item.get('archive_sha256') and digest != item['archive_sha256']:
        raise ValueError('fixed archive fingerprint differs')
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        files = archive.infolist()
        if len(files) != 1 or files[0].filename != item['filename'] or files[0].file_size > 20*1024**2:
            raise RuntimeError('unexpected archive member or size')
        with archive.open(files[0]) as raw:
            rows = list(csv.DictReader(io.TextIOWrapper(raw, encoding='utf-8-sig')))
    # Close in these files is split-adjusted; Adj Close includes cash events.
    # Inversion must use the latter, never apply dividends twice to Close.
    return rows, digest


def run(write=False):
    manifest = json.loads((BASE/'static/tech_actions2026_archives.json').read_text())
    prices = db_prices.load_all()
    stamp = tuple(db_prices._db_stamp() or ())
    if not stamp:
        raise RuntimeError('no live selected snapshot stamp')
    items = [r for r in manifest['archives'] if r['symbol'] in prices]
    connection = db_select.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute('''select symbol,month,close,trade_date,source,evidence_sha256
                              from v_nominal_monthly where source<>'kaggle_tech2026'
                              and symbol=any(%s) order by symbol,month''', ([i['symbol'] for i in items],))
            anchors = defaultdict(dict)
            for symbol, month, close, date, source, proof in cursor.fetchall():
                anchors[symbol][month] = {'close':float(close), 'date':str(date), 'source':source, 'evidence_sha256':proof}
    finally:
        connection.close()
    results, observations = {}, []
    for item in items:
        symbol = item['symbol']
        # Each archive is independent: record network failures without retry.
        # Permission/rate-limit responses stop all further downloads.
        try:
            rows, digest = download(item)
        except OSError as exc:
            results[symbol] = {'status':'transport_failure', 'error_type':type(exc).__name__,
                               'http_status':getattr(exc, 'code', None)}
            limited = getattr(exc, 'code', None) in (403, 429)
            print(symbol, 'transport failure'+('; further downloads stopped' if limited else ''), flush=True)
            if limited:
                break
            continue
        try:
            result = review(adjusted_rows(rows), prices[symbol], anchors[symbol], terminal_date=item['last_quote'],
                            first_month='2008-01', last_month=item['last_quote'][:7])
        except (ValueError, KeyError, OverflowError) as exc:
            result = {'status':'unavailable', 'reason':str(exc), 'months':{}}
        result.update(archive_sha256=digest, dataset=item['dataset'], version=item['version'])
        results[symbol] = result
        if result['status'] == 'crosscheck_pass':
            for month, bar in result['months'].items():
                observations.append((symbol, month, bar['close'], bar['date'], 'kaggle_tech2026',
                    'https://www.kaggle.com/datasets/'+item['dataset']+'/versions/'+str(item['version']),
                    dt.datetime.now(dt.timezone.utc), digest, result['overlap_returns'],
                    result['median_log_deviation'], result['p90_log_deviation'],
                    'Same-vintage Adj Close and dividend/split inversion; terminal '+item['last_quote']+
                    '; independent native overlap '+str(result['native_overlap_months'])+'; archive '+digest))
        print(symbol, result['status'], len(result['months']), flush=True)
    if stamp != tuple(db_prices._db_stamp() or ()):
        raise RuntimeError('selected snapshot changed during audit')
    stored = 0
    if write and observations:
        import psycopg2.extras
        connection = db_select.db()
        connection.autocommit = False
        try:
            with connection, connection.cursor() as cursor:
                psycopg2.extras.execute_values(cursor,
                    '''insert into nominal_monthly(symbol,month,close,trade_date,source,source_url,
                       fetched_at,evidence_sha256,crosscheck_pairs,median_log_deviation,p90_log_deviation,
                       unit_evidence) values %s on conflict(symbol,month,source) do nothing''', observations, page_size=500)
                cursor.execute("select count(*) from nominal_monthly where source='kaggle_tech2026'")
                stored = cursor.fetchone()[0]
        finally:
            connection.close()
    report = {'write':write, 'snapshot_stamp':list(stamp),
        'independent_nominal_snapshot_sha256':hashlib.sha256(json.dumps(anchors, sort_keys=True).encode()).hexdigest(),
        'prepared_observations':len(observations), 'stored_observations':stored,
        'requested_symbols':len(items), 'reviewed_symbols':len(results),
        'collection_incomplete':len(results)!=len(items) or any(r['status']=='transport_failure' for r in results.values()),
        'return_prices_locks_and_baselines_changed':False,
        'results':{s:{k:v for k,v in r.items() if k!='months'} for s,r in results.items()}}
    (BASE/'output/tech_actions2026_review_summary.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='results'}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--write', action='store_true')
    run(parser.parse_args().write)
