"""Audit dated issuer holdings as native-share unit evidence, without DB writes."""
import hashlib
import json
import re
import csv
import datetime as dt
import io
from decimal import Decimal
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
PDF = BASE / 'output/_fund_reports/ishares_equity_20251231.pdf'
SOURCE = 'https://www.ishares.com/us/literature/third-quarter-report/tqr-ishares-trust-equity-jpm-12-31.pdf'
PDF_SHA = 'be504252b271284ab1d56705cf5e390f4e863811a3adc94d732a24e40b62e0fd'
FUND = 'iShares® Core S&P 500 ETF'
REVIEWED_NAMES = {'ABNB': 'Airbnb, Inc., Class A(a)(b)',
                  'CRWD': 'Crowdstrike Holdings, Inc., Class A(a)(b)'}


def check_csv_date(content, expected):
    rows = list(csv.reader(io.StringIO(content.decode('utf-8-sig'))))
    if len(rows) < 2 or rows[0] != ['iShares Core S&P 500 ETF'] or rows[1][0] != 'Fund Holdings as of':
        raise ValueError('Unknown holdings header')
    actual = dt.datetime.strptime(rows[1][1], '%b %d, %Y').date().isoformat()
    if actual != expected:
        raise ValueError('Requested historical date differs from actual holdings date: '+actual)
    return actual


def parse_rows(text, page_number):
    if FUND not in text[:180] or 'December 31, 2025' not in text[:180]:
        raise ValueError('Wrong fund or reporting date')
    lines = [line.strip().replace('\u200c', '') for line in text.splitlines() if line.strip()]
    number = re.compile(r'\$?\s*([0-9][0-9,]*)(?:\s+\$?\s*([0-9][0-9,]*))?')
    rows = []
    for i, line in enumerate(lines[:-1]):
        if not re.search(r'(?:\.\s*){2,}$', line):
            continue
        values = []
        for candidate in lines[i+1:i+3]:
            match = number.fullmatch(candidate)
            if not match:
                break
            values.extend(int(value.replace(',', '')) for value in match.groups() if value)
            if len(values) >= 2:
                break
        if len(values) != 2 or values[0] <= 0:
            continue
        rows.append({'page': page_number,
                     'name': re.sub(r'(?:\.\s*){2,}$', '', line).strip(),
                     'shares': values[0], 'reported_usd_value': values[1]})
    return rows


def unit_support(row, close):
    price = Decimal(str(close)); shares = Decimal(row['shares'])
    value = Decimal(row['reported_usd_value'])
    if not price.is_finite() or price <= 0 or shares <= 0:
        raise ValueError('Invalid native price or share count')
    difference = abs(price * shares - value)
    # Conservative one-dollar band: the report displays whole-dollar values.
    # This checks share units, not a distinct exact market-price observation.
    if difference > 1:
        raise ValueError('Native price disagrees with reported shares/value')
    return {'implied_price': str(value / shares),
            'unit_interval_low': str((value-1)/shares),
            'unit_interval_high': str((value+1)/shares),
            'usd_value_difference': str(difference)}


def run(symbols=None):
    import fitz
    symbols = list(REVIEWED_NAMES) if symbols is None else list(symbols)
    if not symbols or len(set(symbols)) != len(symbols) or not set(symbols) <= set(REVIEWED_NAMES):
        raise ValueError('Only explicitly reviewed securities may be audited')
    raw = PDF.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PDF_SHA:
        raise ValueError('Pinned source report changed')
    document = fitz.open(stream=raw, filetype='pdf')
    candidates = []
    for page in document:
        text = page.get_text()
        if FUND in text[:180]:
            candidates.extend(parse_rows(text, page.number+1))
    proofs = []
    for symbol in symbols:
        name = REVIEWED_NAMES[symbol]
        matches = [row for row in candidates if row['name'] == name]
        if len(matches) != 1:
            raise ValueError('Reviewed company/share class is missing or ambiguous: '+symbol)
        path = BASE / 'output/_nominal_daily' / (symbol+'.json')
        independent_raw = path.read_bytes(); independent = json.loads(independent_raw)
        expected_url = 'https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/x/US_MinKService.getDailyK?symbol='+symbol
        if independent.get('endpoint') != expected_url:
            raise ValueError('Independent security identity differs')
        rows = [row for row in independent['rows'] if row['d'] == '2025-12-31']
        if len(rows) != 1:
            raise ValueError('Independent exact-date observation missing/duplicated')
        close = rows[0]['c']
        proofs.append({'symbol': symbol, 'date': '2025-12-31', 'independent_close': close,
                       'fund_row': matches[0], **unit_support(matches[0], close),
                       'independent_cache_sha256': hashlib.sha256(independent_raw).hexdigest()})
    result = {'source_url': SOURCE, 'source_sha256': PDF_SHA,
              'fund': FUND, 'report_date': '2025-12-31',
              'parsed_candidate_rows': len(candidates), 'reviewed_unit_proofs': proofs,
              'scope': 'Explicitly reviewed names/classes only. Candidate extraction is incomplete; no fuzzy identity matching, no wholesale price approval.',
              'database_and_published_outputs_changed': False}
    csv_path = BASE / 'output/_fund_reports/ivv_holdings_requested_20251231.csv'
    if csv_path.exists():
        content = csv_path.read_bytes()
        try:
            check_csv_date(content, '2025-12-31')
        except ValueError as exc:
            result['rejected_csv_probe'] = {'sha256': hashlib.sha256(content).hexdigest(),
                'source_url': 'https://www.ishares.com/us/products/239726/ishares-core-s-p-500-etf/latest-holdings.csv?asOfDate=20251231',
                'requested_date': '2025-12-31', 'reason': str(exc),
                'used_as_historical_price_data': False}
    (BASE / 'output/fund_native_unit_review.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    run()
