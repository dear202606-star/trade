"""Approve only KLAC's missing 2025-12 close using existing cached evidence."""
import argparse
import datetime as dt
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import db_sources
from import_verified_closing_facts import verify_exact_close

FACT = {'symbol': 'KLAC', 'date': '2025-12-31', 'close': 1215.08,
        'url': 'https://www.sec.gov/Archives/edgar/data/312070/000191870426000169/form424b2.htm',
        'location': 'Initial Valuation Date, KLAC Initial Value, and definitions of Initial Value and Closing Value'}


def review(cursor):
    path = BASE / 'output/_liquidity_windows/KLAC-2025-01-01-2025-12-31.json'
    content = path.read_bytes(); record = json.loads(content)
    url = urlparse(record['source_url']); params = parse_qs(url.query)
    if (url.scheme != 'https' or url.netloc != 'push2his.eastmoney.com'
            or params.get('secid') != ['105.KLAC'] or params.get('fqt') != ['0']
            or params.get('klt') != ['101'] or record.get('secid') != '105.KLAC'
            or record.get('mode') != 'fqt=0' or record['payload'].get('rc') != 0):
        raise ValueError('Unreviewed primary request identity')
    verify_exact_close(record['payload']['data'], FACT, 105)
    path = BASE / 'output/_nominal_daily/KLAC.json'
    independent = path.read_bytes(); daily = json.loads(independent)
    if daily.get('endpoint') != 'https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/x/US_MinKService.getDailyK?symbol=KLAC':
        raise ValueError('Unreviewed independent security identity')
    exact = [r for r in daily['rows'] if r['d'] == FACT['date']]
    if len(exact) != 1 or float(exact[0]['c']) != FACT['close']:
        raise ValueError('Independent exact-date close differs from filing')
    monthly = {}
    seen = set()
    for row in sorted(daily['rows'], key=lambda r: r['d']):
        dt.date.fromisoformat(row['d'])
        close = float(row['c'])
        if row['d'] in seen or not math.isfinite(close) or close <= 0:
            raise ValueError('Invalid independent daily observation')
        seen.add(row['d']); monthly[row['d'][:7]] = close
    cursor.execute("select month,adj_close from series_selected where symbol='KLAC' order by month")
    selected = {m: float(p) for m, p in cursor.fetchall()}
    if FACT['date'][:7] not in selected:
        raise ValueError('No eligible selected month for this fact')
    common = sorted(set(monthly) & set(selected)); deviations = []
    for a, b in zip(common, common[1:]):
        if (int(b[:4])-int(a[:4]))*12+int(b[5:])-int(a[5:]) != 1:
            continue
        deviations.append(abs(math.log((monthly[b]/monthly[a])/(selected[b]/selected[a]))))
    if len(deviations) < 24:
        raise ValueError('Insufficient independent return overlap')
    median = statistics.median(deviations); p90 = sorted(deviations)[int((len(deviations)-1)*.9)]
    if median > .005 or p90 > .05:
        raise ValueError('Selected-security returns disagree')
    proof = {'fact': FACT, 'primary_url': record['source_url'],
             'primary_cache_sha256': hashlib.sha256(content).hexdigest(),
             'independent_cache_sha256': hashlib.sha256(independent).hexdigest(),
             'return_pairs': len(deviations), 'median_log_deviation': median,
             'p90_log_deviation': p90, 'scope': 'Only 2025-12-31; no other date approved'}
    digest = hashlib.sha256(json.dumps(proof, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return proof, digest


def run(write=False):
    connection = db_sources.db()
    try:
        with connection, connection.cursor() as cursor:
            cursor.execute("select source,close,unit_evidence from nominal_monthly where symbol='KLAC' and month='2025-12'")
            existing = cursor.fetchall()
            accepted = [(source, price, evidence) for source, price, evidence in existing if evidence]
            if accepted:
                if any(float(price) != FACT['close'] for source, price, evidence in accepted):
                    raise RuntimeError('Existing nominal evidence needs review; no overwrite')
                print('KLAC 2025-12 already accepted; retained without reprocessing')
                return
            if any(source == 'eastmoney_native' for source, price, evidence in existing):
                raise RuntimeError('Unreviewed target-source row exists; no overwrite')
            cursor.execute("select source,basis,frozen from series_pick where symbol='KLAC' for share")
            pick = cursor.fetchone()
            if pick != ('yahoo_relay', 'total_return', False):
                raise RuntimeError('Reviewed selected source changed')
            # 094 explicitly separates missing screening facts from return
            # locks. Preserve the locked prices, choices and lock records.
            cursor.execute("select month,adj_close,raw_close,source,basis from series_selected where symbol='KLAC' order by month")
            original_returns = cursor.fetchall()
            cursor.execute("select id,status,fingerprint from data_lock where symbol='KLAC' order by id")
            original_locks = cursor.fetchall()
            proof, digest = review(cursor)
            if write:
                cursor.execute("""insert into nominal_monthly
                    (symbol,month,close,trade_date,source,source_url,fetched_at,evidence_sha256,
                     crosscheck_pairs,median_log_deviation,p90_log_deviation,unit_evidence)
                    values ('KLAC','2025-12',%s,'2025-12-31','eastmoney_native',%s,now(),%s,%s,%s,%s,%s)
                    on conflict(symbol,month,source) do nothing""",
                    (FACT['close'],proof['primary_url'],digest,proof['return_pairs'],
                     proof['median_log_deviation'],proof['p90_log_deviation'],
                     'Exact dated closing value in SEC filing '+FACT['url']+'; proof '+digest))
                inserted = cursor.rowcount
                cursor.execute("select close,trade_date from nominal_monthly where symbol='KLAC' and month='2025-12' and source='eastmoney_native'")
                price, date = cursor.fetchone()
                if float(price) != FACT['close'] or str(date) != FACT['date']:
                    raise RuntimeError('Stored nominal observation differs; transaction rolled back')
                cursor.execute("select source,close,unit_evidence from nominal_monthly where symbol='KLAC' and month='2025-12' and source<>'eastmoney_native'")
                if cursor.fetchall() != existing:
                    raise RuntimeError('Original nominal observations changed')
                cursor.execute("""insert into sys_event
                    (kind,source,subject,status,metrics,finished_at)
                    values ('validation','verified_closing_fact','KLAC:2025-12-31','pass',%s,now())""",
                    (json.dumps({'proof': proof, 'evidence_sha256': digest}),))
            else:
                inserted = 0
            cursor.execute("select month,adj_close,raw_close,source,basis from series_selected where symbol='KLAC' order by month")
            if cursor.fetchall() != original_returns:
                raise RuntimeError('Return prices or source decisions changed')
            cursor.execute("select id,status,fingerprint from data_lock where symbol='KLAC' order by id")
            if cursor.fetchall() != original_locks:
                raise RuntimeError('Original return lock records changed')
        result = {'write': write, 'inserted': inserted, 'proof': proof,
                  'evidence_sha256': digest, 'return_prices_picks_locks_and_published_outputs_changed': False}
        name = 'klac_nominal_fact_ingest.json' if write else 'klac_nominal_fact_review.json'
        (BASE / 'output' / name).write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result, indent=2))
    finally:
        connection.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true')
    run(parser.parse_args().write)
