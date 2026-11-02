"""Collect CC0 Yahoo-vintage-2020 archive candidates; --write uses staging.

This is an audit archive, never an automatic source-pick approval. Close is
split-adjusted; Adj Close also adjusts dividends. Neither is called a nominal
historical transaction price here. April 2020 is incomplete and excluded.
"""
import argparse
import csv
import datetime as dt
import io
import json
import math
from collections import defaultdict
from pathlib import Path

import db_sources
import import_kaggle_cc0 as kaggle
import ingest_validate
import staging_write

BASE = Path(__file__).resolve().parent
SOURCE = 'kaggle_yahoo2020'


def parse_monthly(raw):
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    if not {'Date', 'Close', 'Adj Close'}.issubset(reader.fieldnames or []):
        raise ValueError('CSV needs Date, Close and Adj Close')
    days = {}
    for row in reader:
        day = dt.date.fromisoformat(row['Date']).isoformat()
        if not '2008-01' <= day[:7] <= '2020-03':
            continue
        close, adjusted = float(row['Close']), float(row['Adj Close'])
        if not all(math.isfinite(value) and value > 0 for value in (close, adjusted)):
            raise ValueError('nonpositive or nonfinite archive price')
        if day in days:
            raise ValueError('duplicate trading date')
        days[day] = (adjusted, close)
    monthly = {}
    for day in sorted(days):
        monthly[day[:7]] = days[day]
    return monthly


def collect():
    metadata = json.loads((BASE / 'static/kaggle_yahoo2020_symbols.json').read_text())['symbols']
    targets = defaultdict(set)
    existing = set()
    existing_symbols = set()
    with db_sources.db() as connection, connection.cursor() as cursor:
        cursor.execute("""
            with need as (
              select distinct m.symbol,y as year,to_char(make_date(y,12,31),'YYYY-MM') as month
              from members m cross join generate_series(2010,2019) y
              where m.start_date<=make_date(y,12,31) and m.end_date>=make_date(y,12,31)
            )
            select n.symbol,n.year from need n
            left join series_selected s on s.symbol=n.symbol and s.month=n.month
            where s.basis is distinct from 'total_return'
              and not exists(select 1 from locked_symbols l where l.symbol=n.symbol)
              and not exists(select 1 from series_quarantine q where q.symbol=n.symbol)
              and not exists(select 1 from prices_monthly p where p.symbol=n.symbol
                             and p.month=n.month and p.source=%s)
              and not exists(select 1 from ingest_staging p where p.symbol=n.symbol
                             and p.month=n.month and p.source=%s)
            order by n.symbol,n.year
        """, (SOURCE, SOURCE))
        for symbol, year in cursor.fetchall():
            if symbol in metadata:
                targets[symbol].add(year)
        if targets:
            cursor.execute('select distinct symbol from prices_monthly where symbol=any(%s)',
                           (list(targets),))
            existing_symbols = {row[0] for row in cursor.fetchall()}
            cursor.execute("""select symbol,month from prices_monthly where source=%s
                              union select symbol,month from ingest_staging where source=%s""",
                           (SOURCE, SOURCE))
            existing = set(cursor.fetchall())
    if not targets:
        return [], {'targets': 0, 'valid': 0, 'rows': 0, 'candidate_cells': 0, 'available_cells': 0, 'errors': {}}

    token = kaggle._token()
    rows, errors, valid, available = [], {}, 0, 0
    rate_limited = False
    for number, (symbol, years) in enumerate(sorted(targets.items()), 1):
        try:
            raw = kaggle.download_file(token, 'jacksoncrow', 'stock-market-dataset', metadata[symbol]['file'])
            bars = parse_monthly(raw)
            ok, reason, cleaned = ingest_validate.check_payload(
                symbol, bars, 'total_return', allow_stale=True,
                min_months=12 if symbol in existing_symbols else 24)
            if not ok:
                raise ValueError(reason)
            matches = sum('%d-12' % year in cleaned for year in years)
            if not matches:
                raise ValueError('no missing target December in archive')
            eligible = [(month, adj, close) for month, (adj, close) in cleaned.items()
                        if (symbol, month) not in existing]
            if not eligible:
                raise ValueError('all archive months already present')
            rows.extend((symbol, month, adj, close, SOURCE, 'ok', 'total_return')
                        for month, adj, close in eligible)
            valid += 1
            available += matches
            print('%d/%d %s: %d months / %d target cells' %
                  (number, len(targets), symbol, len(eligible), matches), flush=True)
        except kaggle.KaggleRateLimit as error:
            errors[symbol] = str(error)
            rate_limited = True
            print('Kaggle rate limited; stopping further downloads.', flush=True)
            break
        except Exception as error:
            errors[symbol] = str(error)[:150]
            print('%d/%d %s: skipped (%s)' % (number, len(targets), symbol, errors[symbol]), flush=True)
    return rows, {'targets': len(targets), 'valid': valid, 'rows': len(rows),
                  'candidate_cells': sum(map(len, targets.values())),
                  'available_cells': available, 'rate_limited': rate_limited, 'errors': errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    if args.write:
        # This validates the DB constraint before downloading or staging data.
        kaggle._register_source(SOURCE)
    rows, summary = collect()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.write and rows:
        accepted = staging_write.push(rows)
        if accepted != len(rows):
            raise SystemExit('Staging accepted %d/%d; inspect before retry' % (accepted, len(rows)))
        print('Staged %d rows; source-pick guard remains active.' % accepted)
    if args.write:
        (BASE / 'output/kaggle_yahoo2020_ingest.json').write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
