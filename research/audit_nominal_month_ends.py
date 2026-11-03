"""Read-only classification of reviewed-unit nominal source disagreements."""
import datetime as dt
import hashlib
import json
import math
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import db_sources


def daily_month_end(path, symbol, month):
    content = path.read_bytes()
    record = json.loads(content)
    expected = ('https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/'
                'x/US_MinKService.getDailyK?symbol=' + symbol)
    if record.get('endpoint') != expected:
        raise ValueError('Unexpected daily security identity')
    rows = [r for r in record['rows'] if r['d'].startswith(month + '-')]
    if not rows or len({r['d'] for r in rows}) != len(rows):
        raise ValueError('Missing or duplicate daily dates')
    for row in rows:
        dt.date.fromisoformat(row['d'])
        if not math.isfinite(float(row['c'])) or float(row['c']) <= 0:
            raise ValueError('Invalid daily closing price')
    last = max(rows, key=lambda r: r['d'])
    return {'date': last['d'], 'close': float(last['c']),
            'cache_sha256': hashlib.sha256(content).hexdigest()}


def run():
    connection = db_sources.db()
    try:
        connection.set_session(isolation_level='REPEATABLE READ')
        with connection, connection.cursor() as cursor:
            cursor.execute("set local statement_timeout='45s'")
            cursor.execute("""create temporary table audit_nominal_groups on commit drop as
                with eligible as materialized (select distinct symbol,month from series_selected)
                select n.symbol,n.month,min(n.close) lo,max(n.close) hi,
                    count(distinct n.trade_date) dates,
                    jsonb_agg(jsonb_build_object('source',n.source,'close',n.close,
                                                'date',n.trade_date) order by n.source) observations
                from nominal_monthly n join eligible p using(symbol,month)
                where n.unit_evidence is not null
                group by n.symbol,n.month
                having min(n.close)<>max(n.close) or count(distinct n.trade_date)>1""")
            cursor.execute("""select count(*),count(*) filter(where hi-lo<=.01),
                count(*) filter(where lo<5 and hi>=5),count(*) filter(where hi/lo>1.01),
                count(*) filter(where dates>1) from audit_nominal_groups""")
            counts = dict(zip(('eligible_disagreements','within_one_cent',
                               'five_dollar_crossings','over_one_percent','different_dates'),
                              cursor.fetchone()))
            cursor.execute('select symbol,month,observations from audit_nominal_groups where dates>1 order by symbol,month')
            groups = {(s,m): rows for s,m,rows in cursor.fetchall()}
            selected = {}
            for symbol, month in groups:
                cursor.execute('select source,close,trade_date from v_nominal_monthly where symbol=%s and month=%s', (symbol,month))
                src, price, date = cursor.fetchone()
                selected[(symbol,month)] = {'source':src,'close':float(price),'date':str(date)}
        cases = []
        for (symbol, month), observations in groups.items():
            dates = {r['date'] for r in observations}
            if len(dates) > 1:
                stock = daily_month_end(BASE/'output/_nominal_daily'/f'{symbol}.json', symbol, month)
                calendar = daily_month_end(BASE/'output/_nominal_daily/SPY.json', 'SPY', month)
                corroborated = stock['date'] == calendar['date'] and any(
                    r['date'] == stock['date'] and abs(r['close']-stock['close']) < .001
                    for r in observations)
                cases.append({'symbol': symbol, 'month': month,
                              'observations': observations,
                              'selected': selected.get((symbol, month)),
                              'independent_daily': stock, 'benchmark_calendar': calendar,
                              'later_observation_corroborated': corroborated,
                              'status': 'pending_explicit_acceptance'})
        result = {'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                  'scope': 'Online eligible nominal observations with unit evidence; read only',
                  'counts': counts, 'date_conflicts': cases,
                  'database_and_published_outputs_changed': False,
                  'limitation': 'Agreement does not establish complete historical acceptance; '
                                'no prices rounded, replaced or locked by this audit'}
        (BASE/'output/nominal_month_end_review.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(counts))
        print('Date conflicts:', len(cases), '; corroborated:', sum(
            c['later_observation_corroborated'] for c in cases))
    finally:
        connection.close()


if __name__ == '__main__':
    run()
