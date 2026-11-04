"""Check the online quote worker and public quote path against reviewed sessions."""
import datetime as dt
import json
from pathlib import Path

import requests
import db_sources
import market_calendar
from watchdog_checks import quote_problems
from live_quotes import read_snapshot

BASE=Path(__file__).resolve().parent
SITE='https://dear202606-star.github.io/trade/data/'


def public_snapshot():
    response=requests.get(SITE+'cloud.json',params={'monitor':dt.datetime.now(dt.timezone.utc).timestamp()},timeout=20)
    response.raise_for_status();cfg=response.json()
    if cfg.get('url','').rstrip('/')!='https://'+db_sources.REF+'.supabase.co' or not cfg.get('key'):
        raise RuntimeError('正式网页在线库配置不一致')
    response=requests.get(cfg['url']+'/rest/v1/live_quote_snapshot',
        params={'singleton':'eq.true','select':'source_as_of,collected_at,evidence_sha256,payload'},
        headers={'apikey':cfg['key'],'Cache-Control':'no-cache'},timeout=20)
    response.raise_for_status();rows=response.json()
    if len(rows)!=1:raise RuntimeError('公开报价快照缺失或不唯一')
    return rows[0]


def run(now=None):
    now=now or dt.datetime.now(dt.timezone.utc)
    problems=[];warnings=[];report={'checked_at':now.isoformat()}
    connection=None
    try:
        calendar=market_calendar.load();state=calendar.state(now)
        connection=db_sources.db()
        with connection.cursor() as cursor:
            cursor.execute("set local statement_timeout='20s'")
            row,heartbeat=read_snapshot(cursor)
            public=public_snapshot()
            if public['evidence_sha256']!=row['evidence_sha256']:
                # Publication can finish between the two reads. Reconcile once.
                row,heartbeat=read_snapshot(cursor)
                if public['evidence_sha256']!=row['evidence_sha256']:public=public_snapshot()
            problems.extend(quote_problems(row,heartbeat,calendar,now))
            problems.extend('公开读取：'+p for p in quote_problems(public,heartbeat,calendar,now))
            if row['evidence_sha256']!=public['evidence_sha256']:
                problems.append('公开报价与库内版本不一致')
            cursor.execute("select jobname,schedule,active from cron.job where jobname='trade-dispatch-market'")
            jobs=cursor.fetchall()
            if len(jobs)!=1 or not jobs[0][2]:warnings.append('备用五分钟派发任务未启用')
            report.update(calendar_sha256=calendar.sha,expected_session=str(state['session']),
                minimum_source_time=state['minimum_source_time'].isoformat(),refresh_due=state['refresh_due'],
                source_as_of=str(row['source_as_of']),collected_at=str(row['collected_at']),
                worker_finished_at=str(heartbeat['finished_at']) if heartbeat else None,
                evidence_sha256=row['evidence_sha256'],public_read_matches=row['evidence_sha256']==public['evidence_sha256'],
                dispatcher_jobs=jobs)
    except Exception as exc:
        problems.append('在线报价监控失败：'+type(exc).__name__+': '+str(exc)[:140])
    finally:
        if connection is not None:connection.close()
    report.update(problems=list(dict.fromkeys(problems)),warnings=warnings)
    (BASE/'output/realtime_check.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps(report,indent=2,ensure_ascii=False))
    return 1 if problems else 0


if __name__=='__main__':
    raise SystemExit(run())
