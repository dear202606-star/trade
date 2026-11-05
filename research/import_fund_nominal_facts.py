"""Append missing closing facts with explicit primary and fund unit proof."""
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
from research import audit_fund_native_units as fund

FITB_PROXY_URL = 'https://s23.q4cdn.com/252949160/files/doc_downloads/2026/03/2026-FITB-Proxy.pdf'
FITB_PROXY_SHA = 'e6ddd8e52e6743100ea3bfbfc842cff785a8580445635b1db8d748eef1abba75'


def fitb_primary(close):
    """The retired Nasdaq route is unavailable; use an explicit issuer fact."""
    raw = (BASE/'output/_fund_reports/FITB_2026_proxy.pdf').read_bytes()
    if hashlib.sha256(raw).hexdigest() != FITB_PROXY_SHA or float(close) != 46.81:
        raise ValueError('FITB issuer closing fact differs')
    import fitz
    with fitz.open(stream=raw, filetype='pdf') as document:
        if 'fifth third' not in document[0].get_text().lower():
            raise ValueError('Wrong FITB issuer document')
        text = ' '.join(document[75].get_text().split())
    if 'Values are based on December 31, 2025, closing price of the Company’s common stock of $46.81' not in text:
        raise ValueError('FITB exact-date common-stock closing fact missing')
    return raw, {'source_url': FITB_PROXY_URL, 'location': 'PDF page 76, footnote 8',
                 'kind': 'issuer_proxy_closing_fact'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def review_symbol(cursor, unit, source):
    symbol = unit['symbol']
    if symbol not in fund.REVIEWED_NAMES or unit['fund_row']['name'] != fund.REVIEWED_NAMES[symbol]:
        raise ValueError('Unreviewed security/share class')
    market = fund.REVIEWED_MARKETS[symbol]
    if unit['date'] != '2025-12-31' or source['report_date'] != unit['date']:
        raise ValueError('Unit evidence date differs')
    if source['source_sha256'] != fund.PDF_SHA or source['source_url'] != fund.SOURCE:
        raise ValueError('Unreviewed fund source version')
    close = float(unit['independent_close'])
    fund.unit_support(unit['fund_row'], close)
    path = BASE/'output/_liquidity_windows'/(symbol+'-2025-01-01-2025-12-31.json')
    if symbol == 'FITB':
        raw, record = fitb_primary(close)
    else:
        raw = path.read_bytes(); record = json.loads(raw)
        url = urlparse(record['source_url']); params = parse_qs(url.query)
        if (url.scheme != 'https' or url.netloc != 'push2his.eastmoney.com'
                or params.get('secid') != [str(market)+'.'+symbol] or params.get('fqt') != ['0']
                or params.get('klt') != ['101'] or record.get('mode') != 'fqt=0'
                or record.get('secid') != str(market)+'.'+symbol or record['payload'].get('rc') != 0):
            raise ValueError('Primary request identity differs')
        verify_exact_close(record['payload']['data'], {'symbol':symbol,'date':unit['date'],'close':close}, market)
    path = BASE/'output/_nominal_daily'/(symbol+'.json')
    independent_raw = path.read_bytes(); independent = json.loads(independent_raw)
    if hashlib.sha256(independent_raw).hexdigest() != unit['independent_cache_sha256']:
        raise ValueError('Independent cache changed during review')
    monthly = {}; seen = set()
    for row in sorted(independent['rows'], key=lambda r:r['d']):
        dt.date.fromisoformat(row['d']); price = float(row['c'])
        if row['d'] in seen or not math.isfinite(price) or price <= 0:
            raise ValueError('Invalid independent daily observations')
        seen.add(row['d'])
        if row['d'] <= unit['date']:
            monthly[row['d'][:7]] = price
    cursor.execute("select month,adj_close from series_selected where symbol=%s and month<='2025-12' order by month", (symbol,))
    selected = {month:float(price) for month,price in cursor.fetchall()}
    if any(not math.isfinite(price) or price <= 0 for price in selected.values()):
        raise ValueError('Invalid selected history observation')
    if '2025-12' not in selected:
        raise ValueError('No eligible selected month')
    common = sorted(set(monthly)&set(selected)); deviations = []
    for a,b in zip(common, common[1:]):
        if (int(b[:4])-int(a[:4]))*12+int(b[5:])-int(a[5:]) != 1:
            continue
        deviations.append(abs(math.log((monthly[b]/monthly[a])/(selected[b]/selected[a]))))
    if len(deviations) < 24:
        raise ValueError('Insufficient independent return overlap')
    median = statistics.median(deviations); p90 = sorted(deviations)[int((len(deviations)-1)*.9)]
    if median > .005 or p90 > .05:
        raise ValueError('Selected-security returns disagree')
    return {'unit_proof':unit, 'fund_url':source['source_url'],'fund_sha256':source['source_sha256'],
            'nominal_source':'issuer_native' if symbol=='FITB' else 'eastmoney_native',
            'primary_url':record['source_url'],'primary_cache_sha256':hashlib.sha256(raw).hexdigest(),
            'primary_kind':record.get('kind','native_daily'),'primary_location':record.get('location','Exact-date daily bar'),
            'return_pairs':len(deviations),'median_log_deviation':median,'p90_log_deviation':p90,
            'selected_history_sha256':digest(selected),'scope':'Only 2025-12-31; no other date approved'}


def run(write=False):
    connection = db_sources.db()
    try:
        with connection, connection.cursor() as cursor:
            pending = []; preserved = {}; picks = {}; prices = {}; locks = {}
            for symbol in fund.REVIEWED_NAMES:
                cursor.execute("select source,close,unit_evidence from nominal_monthly where symbol=%s and month='2025-12' order by source", (symbol,))
                rows = cursor.fetchall()
                if any(evidence for source,close,evidence in rows):
                    print(symbol+' already accepted; no reprocessing')
                    continue
                if any(source == 'eastmoney_native' for source,close,evidence in rows):
                    raise RuntimeError('Unreviewed target-source observation exists; no overwrite')
                preserved[symbol] = rows
                cursor.execute('select source,basis,frozen from series_pick where symbol=%s for share', (symbol,))
                picks[symbol] = cursor.fetchone()
                if not picks[symbol] or picks[symbol][:2] != ('yahoo_relay','total_return'):
                    raise RuntimeError('Reviewed selected source differs')
                cursor.execute('select month,adj_close,raw_close,source,basis from series_selected where symbol=%s order by month',(symbol,))
                prices[symbol] = cursor.fetchall()
                cursor.execute('select id,status,fingerprint from data_lock where symbol=%s order by id',(symbol,))
                locks[symbol] = cursor.fetchall(); pending.append(symbol)
            if not pending:
                return
            source = fund.run(pending)
            proofs = []; unresolved = []
            for unit in source['reviewed_unit_proofs']:
                try:
                    proofs.append(review_symbol(cursor, unit, source))
                except ValueError as error:
                    unresolved.append({'symbol':unit['symbol'],'reason':str(error)})
            inserted = 0
            if write and proofs:
                reviewed = json.loads((BASE/'output/fund_nominal_fact_review.json').read_text())
                if (reviewed.get('write') is not False or digest(reviewed.get('proofs')) != digest(proofs)
                        or reviewed.get('unresolved',[]) != unresolved):
                    raise RuntimeError('Closing-fact batch differs from prior read-only review')
                for proof in proofs:
                    symbol = proof['unit_proof']['symbol']; close = proof['unit_proof']['independent_close']; sha = digest(proof)
                    nominal_source = proof['nominal_source']
                    cursor.execute("""insert into nominal_monthly
                        (symbol,month,close,trade_date,source,source_url,fetched_at,evidence_sha256,
                         crosscheck_pairs,median_log_deviation,p90_log_deviation,unit_evidence)
                        values (%s,'2025-12',%s,'2025-12-31',%s,%s,now(),%s,%s,%s,%s,%s)
                        on conflict(symbol,month,source) do nothing""",
                        (symbol,close,nominal_source,proof['primary_url'],sha,proof['return_pairs'],proof['median_log_deviation'],
                         proof['p90_log_deviation'],'Exact-date '+proof['primary_kind']+'; independent daily and fund native-share unit proof '+sha))
                    inserted += cursor.rowcount
                    cursor.execute("select close::text,evidence_sha256 from nominal_monthly where symbol=%s and month='2025-12' and source=%s",(symbol,nominal_source))
                    stored = cursor.fetchone()
                    if not stored or float(stored[0]) != float(close) or stored[1] != sha:
                        raise RuntimeError('Stored observation differs; batch rolled back')
                    cursor.execute("insert into sys_event(kind,source,subject,status,metrics,finished_at) values ('validation','fund_native_closing_fact',%s,'pass',%s,now())",
                                   (symbol+':2025-12-31',json.dumps({'proof':proof,'evidence_sha256':sha})))
                    cursor.execute("select source,close,unit_evidence from nominal_monthly where symbol=%s and month='2025-12' and source<>%s order by source",(symbol,nominal_source))
                    if cursor.fetchall() != preserved[symbol]:
                        raise RuntimeError('Original nominal observations changed')
            for symbol in pending:
                cursor.execute('select source,basis,frozen from series_pick where symbol=%s',(symbol,))
                if cursor.fetchone() != picks[symbol]:raise RuntimeError('Source decision changed')
                cursor.execute('select month,adj_close,raw_close,source,basis from series_selected where symbol=%s order by month',(symbol,))
                if cursor.fetchall() != prices[symbol]:raise RuntimeError('Return prices changed')
                cursor.execute('select id,status,fingerprint from data_lock where symbol=%s order by id',(symbol,))
                if cursor.fetchall() != locks[symbol]:raise RuntimeError('Return locks changed')
        result = {'write':write,'inserted':inserted,'proofs':proofs,'unresolved':unresolved,
                  'return_prices_picks_locks_and_published_outputs_changed':False}
        name = 'fund_nominal_fact_ingest.json' if write else 'fund_nominal_fact_review.json'
        (BASE/'output'/name).write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'write':write,'inserted':inserted,'reviewed':len(proofs),'unresolved':len(unresolved)}))
        if unresolved:
            raise RuntimeError('Closing facts still require review: '+','.join(r['symbol'] for r in unresolved))
    finally:
        connection.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--write',action='store_true')
    run(parser.parse_args().write)
