"""Collect only missing current-year ranking evidence, retained in the online DB."""
import argparse
import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path

from psycopg2.extras import Json,execute_values

import db_ref
import db_sources
import live_quotes
from live_strategy import fetch_daily,sha


def normalized(body):
    if 'normalized' in body:return body['normalized']
    dates=sorted(body['adjusted']);first=dates[0]
    return {d:body['adjusted'][d]/body['adjusted'][first] for d in dates}


def directory(connection,path=None):
    with connection.cursor() as cursor:
        cursor.execute("select metrics from sys_event where source='live_security_directory' and status='pass' order by id desc limit 1")
        row=cursor.fetchone()
        if row:
            body=row[0]['proof']
            if sha(body)!=row[0]['evidence_sha256']:raise ValueError('Directory evidence hash mismatch')
            return body
        if path is None:raise ValueError('Online security directory has not been initialized')
        body=json.loads(Path(path).read_text())
        if not body['source_url'].startswith('https://push2.eastmoney.com/'):raise ValueError('Unrecognized directory source')
        cursor.execute("""insert into sys_event(kind,source,subject,status,metrics,finished_at)
            values('validation','live_security_directory','current','pass',%s,now())""",
            (Json({'proof':body,'evidence_sha256':sha(body)}),))
    connection.commit();return body


def prepare(path=None):
    q=live_quotes.load_snapshot();session=q['session'];year=int(session[:4]);base=str(year-1)+'-12-31'
    phase='intraday' if q['intraday'] else 'close'
    pool,_,_=db_ref.load_market_reference(session)
    connection=db_sources.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute('select symbol from live_ranking_context where base_year=%s and session=%s and phase=%s',(year,session,phase))
            done={r[0] for r in cursor.fetchall()}
        targets=sorted((pool|{'FDXF','HONA','SPY','SKYD_WS'})-done)
        if not targets:
            print('Current ranking evidence already online; no collection',flush=True);return True
        records=directory(connection,path)['records'];mapping={}
        for r in records:mapping.setdefault(r['code'],[]).append(r['market'])
        with connection.cursor() as cursor:
            cursor.execute('''select distinct on(symbol) symbol,payload,evidence_sha256 from live_ranking_context
                where base_year=%s order by symbol,session desc,created_at desc''',(year,))
            prior={s:(p,h) for s,p,h in cursor.fetchall()}
        errors=[];success=0;pending=[]
        def flush():
            nonlocal success
            if not pending:return
            with connection.cursor() as cursor:
                execute_values(cursor,'''insert into live_ranking_context(symbol,base_year,session,phase,payload,evidence_sha256)
                    values %s on conflict do nothing''',pending)
            connection.commit();success+=len(pending);pending.clear()
        def collect(symbol):
            provider_symbol=symbol.replace('.','_')
            markets=mapping.get(provider_symbol,[])
            if len(markets)!=1:raise ValueError('No unique provider identity: '+symbol)
            market=markets[0]
            previous=prior.get(symbol)
            begin=base
            if previous:
                old=previous[0]
                begin=q['previous_session'] if old['source_end']==session else old['source_end']
            data=fetch_daily(provider_symbol,market,begin,session)
            body={'symbol':symbol,'provider_symbol':provider_symbol,'market':market,'source_start':begin,'source_end':session,**data}
            if previous:
                old,digest=previous;values=normalized(old)
                if begin not in values or begin not in data['raw'] or old['raw'].get(begin)!=data['raw'][begin]:
                    raise ValueError('Incremental source cannot reconcile previous close: '+symbol)
                body['normalized']={d:values[begin]*v/data['adjusted'][begin] for d,v in data['adjusted'].items()}
                body['base_day']=old.get('base_day',min(old['raw']))
                body['base_raw']=old.get('base_raw',old['raw'][min(old['raw'])])
                body['previous_context_sha256']=digest
            return body
        with ThreadPoolExecutor(max_workers=8) as executor:
            tasks={executor.submit(collect,s):s for s in targets}
            for future in as_completed(tasks):
                symbol=tasks[future]
                try:
                    body=future.result()
                except Exception as exc:
                    errors.append({'symbol':symbol,'error':str(exc)[:160]});continue
                pending.append((symbol,year,session,phase,Json(body),sha(body)))
                if len(pending)>=25:
                    flush();print('Online ranking inputs',success,'/',len(targets),flush=True)
        flush()
        summary={'session':session,'requested':len(targets),'stored':success,'already_online':len(done),'errors':errors}
        with connection.cursor() as cursor:
            cursor.execute("""insert into sys_event(kind,source,subject,status,metrics,finished_at)
                values('fetch','live_ranking_context',%s,%s,%s,now())""",
                (session,'fail' if errors else 'ok',Json(summary)))
        connection.commit();print(json.dumps(summary,ensure_ascii=False),flush=True)
        return not errors
    finally:connection.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--initialize-directory',type=Path)
    a=p.parse_args();raise SystemExit(0 if prepare(a.initialize_directory) else 1)
