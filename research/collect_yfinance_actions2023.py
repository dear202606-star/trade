"""Audit fixed Yahoo cash/split-event snapshot; optionally add screening prices.

The independent existing nominal-price overlap proves units. Return agreement
alone does not. No return series, source picks, locks or baselines are written.
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
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import db_prices
import db_select
import import_kaggle_cc0 as kaggle
import reconstruct_nominal

ARCHIVE = BASE / 'output/_yfinance_actions2023.zip'
DIGEST = '1b6c78baa277488d659b0e53465a417b18bc95d8668bd2fb15d1d5eb9c77b4b1'
SOURCE_URL = 'https://www.kaggle.com/datasets/iveeaten3223times/massive-yahoo-finance-dataset/versions/2'


def download():
    if ARCHIVE.exists():
        return
    request = urllib.request.Request(
        'https://www.kaggle.com/api/v1/datasets/download/iveeaten3223times/massive-yahoo-finance-dataset?datasetVersionNumber=2',
        headers={'Authorization': 'Bearer '+kaggle._token()})
    temporary = ARCHIVE.with_suffix('.part')
    size = 0
    try:
        with urllib.request.build_opener(kaggle._SafeRedirect()).open(request, timeout=30) as response, temporary.open('wb') as output:
            while True:
                chunk = response.read(1024**2)
                if not chunk:
                    break
                size += len(chunk)
                if size > 128*1024**2:
                    raise RuntimeError('bounded archive download exceeded')
                output.write(chunk)
        temporary.replace(ARCHIVE)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def review(rows, selected, anchors, terminal_date='2023-11-29',
           first_month='2018-12', last_month='2023-11'):
    restored = reconstruct_nominal.restore(rows)
    if restored[-1]['date'] != terminal_date:
        raise ValueError('terminal quote differs from fixed snapshot')
    monthly = {}
    for day in restored:
        month = day['date'][:7]
        if first_month <= month < last_month:
            monthly[month] = day
    common = sorted(set(monthly) & set(selected))
    deviations = []
    for a, b in zip(common, common[1:]):
        if (int(b[:4])-int(a[:4]))*12+int(b[5:])-int(a[5:]) != 1:
            continue
        ratio = (monthly[b]['adjusted']/monthly[a]['adjusted'])/(selected[b][0]/selected[a][0])
        deviations.append(abs(math.log(ratio)))
    native = [abs(monthly[m]['close']/anchor['close']-1)
              for m, anchor in anchors.items() if m in monthly and monthly[m]['date'] == anchor['date']]
    median = statistics.median(deviations) if deviations else None
    p90 = sorted(deviations)[int((len(deviations)-1)*.9)] if deviations else None
    native_median = statistics.median(native) if native else None
    native_max = max(native) if native else None
    accepted = (len(deviations) >= 24 and median <= .005 and p90 <= .02
                and len(native) >= 24 and native_median <= .001 and native_max <= .01)
    # Keep $5 decisions outside both cent precision and the worst observed
    # native-price discrepancy. Never treat rounding uncertainty as a pass.
    threshold_margin = max(.01, 5*native_max/(1-native_max)) if accepted else .01
    ambiguous = [m for m, r in monthly.items() if abs(r['close']-5) < threshold_margin]
    return {'status': 'crosscheck_pass' if accepted else 'review_required',
            'overlap_returns': len(deviations), 'median_log_deviation': median,
            'p90_log_deviation': p90, 'native_overlap_months': len(native),
            'native_median_relative_deviation': native_median, 'native_max_relative_deviation': native_max,
            'threshold_uncertainty_usd': threshold_margin,
            'ambiguous_threshold_months': ambiguous,
            'months': {m: {'date': r['date'], 'close': r['close']} for m, r in monthly.items()
                       if accepted and m not in anchors and m not in ambiguous}}


def run(write=False):
    download()
    if hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() != DIGEST:
        raise RuntimeError('fixed archive fingerprint differs')
    prices = db_prices.load_all()
    stamp = tuple(db_prices._db_stamp() or ())
    if not stamp:
        raise RuntimeError('no live selected snapshot stamp')
    connection = db_select.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""select symbol,month,close,trade_date,source,evidence_sha256 from v_nominal_monthly
                              where source<>'kaggle_yfinance2023' order by symbol,month""")
            anchors = defaultdict(dict)
            for symbol, month, close, date, source, proof in cursor.fetchall():
                anchors[symbol][month] = {'close': float(close), 'date': str(date), 'source': source, 'evidence_sha256': proof}
    finally:
        connection.close()
    grouped = defaultdict(list)
    with zipfile.ZipFile(ARCHIVE) as archive:
        files = archive.infolist()
        if len(files) != 1 or files[0].filename != 'stock_details_5_years.csv' or files[0].file_size > 128*1024**2:
            raise RuntimeError('unexpected archive member or size')
        with archive.open(files[0]) as raw:
            for row in csv.DictReader(io.TextIOWrapper(raw, encoding='utf-8-sig')):
                if row['Company'] in prices:
                    grouped[row['Company']].append(row)
    results = {}
    for symbol, rows in sorted(grouped.items()):
        try:
            results[symbol] = review(rows, prices[symbol], anchors[symbol])
        except (ValueError, KeyError, OverflowError) as exc:
            results[symbol] = {'status': 'unavailable', 'reason': str(exc), 'months': {}}
    if stamp != tuple(db_prices._db_stamp() or ()):
        raise RuntimeError('selected inputs changed during audit')
    report = {'source_url': SOURCE_URL, 'archive_sha256': DIGEST, 'snapshot_stamp': list(stamp),
              'independent_nominal_snapshot_sha256': hashlib.sha256(json.dumps(anchors, sort_keys=True).encode()).hexdigest(),
              'independent_nominal_sources': sorted({r['source'] for months in anchors.values() for r in months.values()}),
              'method': 'same-vintage inverse cash/split events; latest quote D=1; >=24 independent native anchors',
              'statuses': {s: sum(r['status'] == s for r in results.values())
                           for s in sorted({r['status'] for r in results.values()})}, 'results': results}
    (BASE/'output/yfinance_actions2023_review.json').write_text(json.dumps(report, separators=(',', ':'))+'\n')
    observations = [(symbol, m, r['close'], r['date'], 'kaggle_yfinance2023', SOURCE_URL,
                     dt.datetime.now(dt.timezone.utc), DIGEST, result['overlap_returns'],
                     result['median_log_deviation'], result['p90_log_deviation'],
                     'Same-vintage dividend/split inversion; terminal 2023-11-29; independent native overlap '
                     + str(result['native_overlap_months']) + '; archive '+DIGEST)
                    for symbol, result in results.items() if result['status'] == 'crosscheck_pass'
                    for m, r in result['months'].items()]
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
                cursor.execute("select count(*) from nominal_monthly where source='kaggle_yfinance2023'")
                stored = cursor.fetchone()[0]
        finally:
            connection.close()
    summary = {k: v for k, v in report.items() if k != 'results'}
    summary.update(write=write, prepared_observations=len(observations), stored_observations=stored,
                   return_prices_locks_and_baselines_changed=False,
                   results={s: {k: v for k, v in r.items() if k != 'months'} for s, r in results.items()})
    (BASE/'output/yfinance_actions2023_review_summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps({k: v for k, v in summary.items() if k != 'results'}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--write', action='store_true')
    run(parser.parse_args().write)
