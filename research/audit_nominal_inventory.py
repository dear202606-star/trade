"""Read-only, consistent live inventory of screening units and security windows."""
import datetime as dt
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import db_select


def run():
    connection = db_select.db()
    connection.autocommit = False
    try:
        with connection.cursor() as cursor:
            cursor.execute('set transaction isolation level repeatable read read only')
            cursor.execute('''select source,count(*),count(distinct symbol) from v_nominal_monthly
                              group by source order by source''')
            sources = [{'source':s, 'rows':n, 'symbols':symbols} for s,n,symbols in cursor.fetchall()]
            cursor.execute('''select symbol,count(*) from nominal_monthly
                              where source='alphavantage_native' and unit_evidence is null
                              group by symbol order by symbol''')
            withdrawn = [{'symbol':s, 'rows':n} for s,n in cursor.fetchall()]
            cursor.execute('''select count(*) from series_selected p join security_history_window w using(symbol)
                              where p.month>w.month_to''')
            outside = cursor.fetchone()[0]
            cursor.execute('select count(*) from v_nominal_monthly where unit_evidence is null')
            unproved = cursor.fetchone()[0]
            cursor.execute('''select coalesce(sum((metrics->>'requested')::int),0) from sys_event
                              where kind='fetch' and source='alphavantage_quota'
                              and created_at>=now()-interval '24 hours' ''')
            quota = int(cursor.fetchone()[0])
    finally:
        connection.close()
    if outside or unproved:
        raise RuntimeError('selected security/unit guard failed: %d outside windows, %d unproved' % (outside, unproved))
    result = {'audited_at':dt.datetime.now(dt.timezone.utc).isoformat(),
              'selected_native_sources':sources, 'withdrawn_native_observations':withdrawn,
              'selected_post_security_window_rows':outside, 'selected_unproved_native_rows':unproved,
              'rolling_av_reserved_calls':quota}
    (BASE/'output/nominal_live_inventory.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    run()
