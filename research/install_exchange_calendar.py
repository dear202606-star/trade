"""Install explicitly reviewed published exchange exceptions, without price writes."""
import argparse
import json
import sys
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import db_sources
from market_calendar import Calendar, fingerprint

CALENDAR = {
    'market':'XNYS','coverage_from':'2025-12-29','coverage_to':'2028-12-31',
    'timezone':'America/New_York','regular_open':'09:30','regular_close':'16:00',
    'closed':[
        '2026-01-01','2026-01-19','2026-02-16','2026-04-03','2026-05-25',
        '2026-06-19','2026-07-03','2026-09-07','2026-11-26','2026-12-25',
        '2027-01-01','2027-01-18','2027-02-15','2027-03-26','2027-05-31',
        '2027-06-18','2027-07-05','2027-09-06','2027-11-25','2027-12-24',
        '2028-01-17','2028-02-21','2028-04-14','2028-05-29','2028-06-19',
        '2028-07-04','2028-09-04','2028-11-23','2028-12-25'],
    'early_close':{'2026-11-27':'13:00','2026-12-24':'13:00',
                   '2027-11-26':'13:00','2028-07-03':'13:00','2028-11-24':'13:00'},
    'sources':[
        'https://www.nyse.com/markets/hours-calendars',
        'https://ir.theice.com/press/news-details/2025/NYSE-Group-Announces-2026-2027-and-2028-Holiday-and-Early-Closings-Calendar/',
        'https://ir.theice.com/press/news-details/2024/NYSE-Group-Announces-2025-2026-and-2027-Holiday-and-Early-Closings-Calendar/default.aspx'],
    'scope':'NYSE cash-equity regular sessions only; emergency closure revisions require new reviewed evidence',
}


def run(write=False):
    sha=fingerprint(CALENDAR);Calendar(CALENDAR,sha)
    connection=db_sources.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute("set local lock_timeout='5s'")
            cursor.execute("set local statement_timeout='20s'")
            cursor.execute((BASE/'sql/111_verified_exchange_calendar.sql').read_text())
            cursor.execute("select metrics from sys_event where kind='validation' and source='verified_exchange_calendar' and status='pass' order by created_at desc,id desc limit 1")
            existing=cursor.fetchone()
            if existing and existing[0] != {'calendar':CALENDAR,'evidence_sha256':sha}:
                raise RuntimeError('Reviewed calendar version differs; no overwrite')
            if not existing:
                cursor.execute("""insert into sys_event(kind,source,subject,status,metrics,finished_at)
                  values ('validation','verified_exchange_calendar','XNYS:2025-12-29:2028-12-31','pass',%s,now())""",
                  (json.dumps({'calendar':CALENDAR,'evidence_sha256':sha}),))
            cursor.execute('select calendar,evidence_sha256 from v_market_calendar')
            if cursor.fetchone()!=(CALENDAR,sha):raise RuntimeError('Online calendar differs')
        if write:connection.commit()
        else:connection.rollback()
        result={'write':write,'evidence_sha256':sha,'calendar':CALENDAR,'historical_price_writes':0}
        name='exchange_calendar_applied.json' if write else 'exchange_calendar_rollback.json'
        (BASE/'output'/name).write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'write':write,'evidence_sha256':sha,'closures':len(CALENDAR['closed']),'early_closes':len(CALENDAR['early_close'])}))
    finally:
        connection.rollback();connection.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--write',action='store_true')
    run(parser.parse_args().write)
