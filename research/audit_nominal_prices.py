"""Collect as-traded month-end closes; audit independently of strategy returns.

Read-only with respect to Postgres and published strategy artifacts. Daily
responses are retained locally for reproducibility, never inferred from jumps.
"""
import calendar
import concurrent.futures
import datetime as dt
import hashlib
import json
import math
import re
import statistics
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import requests
import db_prices
import db_ref

CACHE = BASE / 'output/_nominal_daily'
ENDPOINT = ('https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/x/'
            'US_MinKService.getDailyK?symbol=')


def parse_daily(rows):
    """Preserve the actual last trading date, not input order or calendar end."""
    seen, months, tombstone_months = set(), {}, set()
    for row in rows:
        date = dt.date.fromisoformat(row['d'])
        if date in seen:
            raise ValueError('duplicate trading date')
        seen.add(date)
        close = float(row['c'])
        # Sina emits all-zero rows after delisting. They are not trades. Drop
        # the entire affected month so an earlier day is not mislabelled month end.
        if close == 0 and all(str(row.get(k)) in ('0', '0.0', '0.00')
                              for k in ('o', 'h', 'l', 'v')):
            tombstone_months.add(date.strftime('%Y-%m'))
            continue
        if not math.isfinite(close) or close <= 0:
            raise ValueError('invalid close')
        if date > dt.datetime.now(dt.timezone.utc).date():
            raise ValueError('future trading date')
        month = date.strftime('%Y-%m')
        if month not in months or date.isoformat() > months[month]['date']:
            months[month] = {'date': date.isoformat(), 'close': close}
    return {m: r for m, r in months.items() if m not in tombstone_months}


def review(symbol, bars):
    path = CACHE / (symbol + '.json')
    try:
        if path.exists():
            payload = json.loads(path.read_text())
        else:
            response = requests.get(ENDPOINT + requests.utils.quote(symbol, safe=''),
                headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'},
                timeout=25)
            if response.status_code in (403, 429):
                return symbol, {'status': 'rate_limited', 'http_status': response.status_code}
            response.raise_for_status()
            match = re.fullmatch(r'\s*(?:/\*.*?\*/\s*)?[^\n]*?\((\[.*\])\);?\s*',
                                 response.text, re.S)
            if not match:
                raise ValueError('not a daily series')
            payload = {'fetched_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                       'endpoint': ENDPOINT + symbol, 'rows': json.loads(match.group(1))}
            path.write_text(json.dumps(payload, separators=(',', ':')))
        months = parse_daily(payload['rows'])
        # Check completed month ends only, avoiding an ongoing month's close.
        today = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m')
        months = {m: r for m, r in months.items() if m < today}
        months = {m: r for m, r in months.items()
                  if calendar.monthrange(int(m[:4]), int(m[5:]))[1]
                     - int(r['date'][-2:]) <= 7}
        common = sorted(set(months) & set(bars))
        deviations = []
        for before, after in zip(common, common[1:]):
            a = dt.date.fromisoformat(before + '-01')
            b = dt.date.fromisoformat(after + '-01')
            if (b.year-a.year)*12+b.month-a.month != 1:
                continue
            ratio = (months[after]['close']/months[before]['close']) / (bars[after][0]/bars[before][0])
            deviations.append(abs(math.log(ratio)))
        median = statistics.median(deviations) if deviations else None
        p90 = sorted(deviations)[int((len(deviations)-1)*.9)] if deviations else None
        # This comparison detects gross identity/currency failures. It does not
        # certify split events or override an issuer identity quarantine.
        matched = len(deviations) >= 24 and median <= .005 and p90 <= .05
        return symbol, {'status': 'crosscheck_pass' if matched else 'review_required',
                        'overlap_returns': len(deviations), 'median_log_deviation': median,
                        'p90_log_deviation': p90, 'endpoint': payload['endpoint'],
                        'fetched_at': payload['fetched_at'],
                        'zero_tombstone_rows': sum(float(r['c']) == 0 for r in payload['rows']),
                        'daily_sha256': hashlib.sha256(json.dumps(payload['rows'],
                            separators=(',', ':')).encode()).hexdigest(),
                        'months': months}
    except Exception as exc:
        return symbol, {'status': 'unavailable', 'reason': type(exc).__name__ + ': ' + str(exc)[:160]}


def run():
    CACHE.mkdir(exist_ok=True)
    prices = db_prices.load_all()
    members = db_ref.load_members()
    targets = sorted(set(prices) & set(members))
    snapshot = db_prices._db_stamp()
    results = {}
    # One small wave at a time; stop globally on an upstream rate limit.
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for i in range(0, len(targets), 4):
            for symbol, result in pool.map(lambda s: review(s, prices[s]), targets[i:i+4]):
                results[symbol] = result
            if any(r['status'] == 'rate_limited' for r in results.values()):
                break
            if i % 40 == 0:
                print('reviewed %d/%d' % (len(results), len(targets)), flush=True)
            time.sleep(.3)
    if tuple(snapshot or ()) != tuple(db_prices._db_stamp() or ()):
        raise RuntimeError('selected snapshot changed during review; do not accept this run')
    statuses = {s: sum(r['status'] == s for r in results.values())
                for s in sorted({r['status'] for r in results.values()})}
    out = {'purpose': 'as-traded price evidence; no DB or strategy artifact writes',
           'snapshot_stamp': snapshot, 'targets': len(targets), 'statuses': statuses,
           'results': results}
    path = BASE / 'output/nominal_price_review.json'
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(',', ':')) + '\n')
    print(json.dumps({'targets': len(targets), 'statuses': statuses}), flush=True)


if __name__ == '__main__':
    run()
