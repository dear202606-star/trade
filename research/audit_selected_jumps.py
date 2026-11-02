"""Audit underlying source decisions, including quarantined rows; read-only."""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import db_sources


def run():
    with db_sources.db() as connection, connection.cursor() as cursor:
        cursor.execute("""
            with s as (
              select p.symbol,p.month,p.source,p.basis,p.adj_close,
                     lag(p.month) over w as prev_month, lag(p.adj_close) over w as prev_close
              from prices_monthly p join series_pick k using(symbol,source)
              where p.month >= '2008-01'
              window w as(partition by p.symbol order by p.month)
            )
            select symbol,month,source,basis,adj_close::float,prev_month,prev_close::float,
                   (adj_close/prev_close)::float,
                   exists(select 1 from locked_symbols l where l.symbol=s.symbol),
                   exists(select 1 from members m where m.symbol=s.symbol
                          and to_char(m.start_date,'YYYY-MM')<=s.month
                          and to_char(m.end_date,'YYYY-MM')>=s.month),
                   exists(select 1 from series_quarantine q where q.symbol=s.symbol)
            from s where prev_close>0 and (adj_close/prev_close>4 or adj_close/prev_close<.25)
            order by symbol,month
        """)
        names = ['symbol','month','source','basis','close','previous_month','previous_close',
                 'ratio','locked','within_membership_month','quarantined']
        rows = [dict(zip(names, values)) for values in cursor.fetchall()]
    for row in rows:
        a, b = row['previous_month'], row['month']
        row['month_gap'] = (int(b[:4])-int(a[:4]))*12 + int(b[5:])-int(a[5:])
    (BASE / 'output/selected_jump_audit.json').write_text(json.dumps(rows, indent=2)+'\n')
    print('Underlying jumps:', len(rows), 'consecutive:', sum(r['month_gap']==1 for r in rows),
          'within member month:', sum(r['within_membership_month'] for r in rows),
          'quarantined:', sum(r['quarantined'] for r in rows))


if __name__ == '__main__':
    run()
