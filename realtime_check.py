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
# 网关侧不可用（配额/鉴权/限流/服务故障）不等于「页面数据不新鲜」：
# 页面已由 CI 直连导出的同源镜像兜底。这类异常记入 warnings 而非 problems。
GATEWAY_STATUS={402,403,429}


def is_gateway_outage(exc):
    status=getattr(getattr(exc,'response',None),'status_code',None)
    return status in GATEWAY_STATUS or (isinstance(status,int) and 500<=status<600)


def public_snapshot():
    response=requests.get(SITE+'cloud.json',params={'monitor':dt.datetime.now(dt.timezone.utc).timestamp()},timeout=20)
    response.raise_for_status();cfg=response.json()
    # 2026-11-08：cloud.json 改为多端点格式 {url,key,endpoints:[...]}，
    # 同时兼容只带 url/key 的老配置；主库地址仍强制锁定，绝不跟随备库。
    eps=cfg.get('endpoints') or []
    primary=next((e for e in eps if (e.get('type') or 'supabase')=='supabase'),None) or {}
    url=primary.get('url') or cfg.get('url') or ''
    key=primary.get('key') or cfg.get('key') or ''
    if url.rstrip('/')!='https://'+db_sources.REF+'.supabase.co' or not key:
        raise RuntimeError('正式网页在线库配置不一致')
    response=requests.get(url+'/rest/v1/live_quote_snapshot',
        params={'singleton':'eq.true','select':'source_as_of,collected_at,evidence_sha256,payload'},
        headers={'apikey':key,'Cache-Control':'no-cache'},timeout=20)
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
            # 2026-11-07：调度权统一收归 GitHub Actions，两个 pg_cron
            # dispatch 作业（trade-dispatch / trade-dispatch-market）已由
            # sql/124_egress_schedule.sql 停用。它们若重新出现，就会与
            # GitHub schedule 双触发并再次打穿 5GB Egress 配额，属真实故障。
            cursor.execute("select jobname,schedule,active from cron.job where jobname like 'trade-dispatch%'")
            jobs=cursor.fetchall()
            if jobs:problems.append('检测到重复的 pg_cron 派发作业 %s（双触发会打穿 Egress 配额）'
                                    % [j[0] for j in jobs])
            report.update(calendar_sha256=calendar.sha,expected_session=str(state['session']),
                minimum_source_time=state['minimum_source_time'].isoformat(),refresh_due=state['refresh_due'],
                source_as_of=str(row['source_as_of']),collected_at=str(row['collected_at']),
                worker_finished_at=str(heartbeat['finished_at']) if heartbeat else None,
                evidence_sha256=row['evidence_sha256'],public_read_matches=row['evidence_sha256']==public['evidence_sha256'],
                dispatcher_jobs=jobs)
    except Exception as exc:
        line='在线报价监控失败：'+type(exc).__name__+': '+str(exc)[:140]
        if is_gateway_outage(exc):
            warnings.append(line+'（在线网关不可用，已由 Pages 同源镜像兜底）')
        else:
            problems.append(line)
    finally:
        if connection is not None:connection.close()
    report.update(problems=list(dict.fromkeys(problems)),warnings=warnings)
    (BASE/'output/realtime_check.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps(report,indent=2,ensure_ascii=False))
    return 1 if problems else 0


if __name__=='__main__':
    raise SystemExit(run())
