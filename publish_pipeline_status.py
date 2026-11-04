"""Append one GitHub job receipt online, with no Git writes or history updates."""
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path

from runner import STATUS


def snapshot(data, env):
    context={k:env.get(v,'') for k,v in [('run_id','GITHUB_RUN_ID'),('attempt','GITHUB_RUN_ATTEMPT'),
             ('job','GITHUB_JOB'),('commit','GITHUB_SHA')]}
    if (not context['run_id'].isdigit() or not context['attempt'].isdigit()
            or not context['job'] or len(context['commit'])!=40
            or any(c not in '0123456789abcdef' for c in context['commit'])):
        raise ValueError('Missing or invalid GitHub execution identity')
    job_status=env.get('PIPELINE_JOB_STATUS')
    if job_status not in ('success','failure','cancelled'):
        raise ValueError('Missing actual job outcome at capture')
    if not isinstance(data,dict) or not isinstance(data.get('runs'),list):
        raise ValueError('Missing current job receipts')
    records=[]
    for row in data['runs']:
        if not isinstance(row,dict):raise ValueError('Malformed step receipt')
        if any(row.get(k)!=context[k] for k in ('run_id','attempt','job')):continue
        if row.get('commit')!=context['commit']:raise ValueError('Mixed commit receipts')
        if (not isinstance(row.get('step'),str) or not row['step']
                or type(row.get('rc')) is not int or type(row.get('ok')) is not bool
                or row['ok']!=(row['rc']==0)
                or type(row.get('dur')) not in (int,float) or not math.isfinite(row['dur']) or row['dur']<0):
            raise ValueError('Invalid step outcome')
        stamp=dt.datetime.fromisoformat(row['ts'].replace('Z','+00:00'))
        if stamp.tzinfo is None:raise ValueError('Step timestamp lacks timezone')
        # Raw console tails remain in the run artifact, not the public DB.
        records.append({k:row[k] for k in ('ts','step','rc','ok','dur')})
    if not records:raise ValueError('No receipts for this run, attempt and job')
    result={'context':context,'job_status_at_capture':job_status,'runs':records,
            'scope':'job steps before final status persistence; not a deployment acceptance',
            'run_url':'https://github.com/dear202606-star/trade/actions/runs/'+context['run_id']}
    result['evidence_sha256']=hashlib.sha256(json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    return result


def publish(receipt, source='pipeline_status'):
    import db_sources
    context=receipt['context']
    key=':'.join(context[k] for k in ('run_id','attempt','job'))
    status='fail' if receipt['job_status_at_capture']!='success' else (
        'warn' if any(not r['ok'] for r in receipt['runs']) else 'pass')
    connection=db_sources.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute("set local statement_timeout='20s'")
            cursor.execute("set local lock_timeout='5s'")
            cursor.execute('select pg_advisory_xact_lock(hashtextextended(%s,0))',(source+':'+key,))
            cursor.execute("select metrics,status from sys_event where kind='validation' and source=%s and run_id=%s",(source,key))
            old=cursor.fetchall()
            if old:
                if len(old)!=1 or old[0]!=(receipt,status):
                    raise RuntimeError('Conflicting existing execution receipt; no overwrite')
                changed=False
            else:
                cursor.execute("""insert into sys_event(kind,source,subject,status,run_id,metrics,finished_at)
                    values('validation',%s,%s,%s,%s,%s,now())""",
                    (source,context['job'],status,key,json.dumps(receipt)))
                changed=True
        connection.commit()
        return changed
    finally:
        connection.rollback();connection.close()


def main():
    with Path(STATUS).open(encoding='utf-8') as f:
        receipt=snapshot(json.load(f),os.environ)
    changed=publish(receipt)
    print(json.dumps({'stored':True,'changed':changed,'run':receipt['context'],
                      'evidence_sha256':receipt['evidence_sha256']}))


if __name__=='__main__':
    main()
