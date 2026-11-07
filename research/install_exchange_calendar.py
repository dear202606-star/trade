"""Install explicitly reviewed published exchange exceptions, without price writes."""
import argparse
import json
import sys
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import db_sources
from market_calendar import Calendar, fingerprint

# 2026-11-08：日历数据抽成纯 JSON，让「交易时段门禁」能在 pip install 之前
# 用系统 Python 零依赖读取，同时供公开 cloud.json 生成同一份日历。内容与原先
# 的内嵌字典逐字一致（证据 sha256 7ae1e5716877...），数据库证据哈希不变。
# 修改日历必须人工复核 NYSE 官方公告后重新发布证据，任何自动任务都不得写入。
CALENDAR = json.loads(
    (Path(__file__).with_name('exchange_calendar.json')).read_text(encoding='utf-8'))


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
