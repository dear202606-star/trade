"""Collect only missing current-year ranking evidence, retained in the online DB."""
import argparse
import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path

import requests
from psycopg2.extras import Json,execute_values

import db_ref
import db_sources
import live_quotes
from live_strategy import fetch_daily,fetch_daily_yahoo,sha,close_enough


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
                if sha(old)!=previous[1]:raise ValueError('Previous ranking context hash mismatch')
                begin=q['previous_session'] if old['source_end']==session else old['source_end']
            # 2026-11-06：东方财富对 CI 出口 IP 间歇断连，增加 Yahoo 日线兜底。
            # 只在「网络故障」与「目录身份不匹配」两类错误时切换；schema/空数据
            # 等数据性错误仍然 fail-closed，防止用兜底掩盖真实源损坏。
            try:
                data=fetch_daily(provider_symbol,market,begin,session)
                data['provider']='eastmoney'
            except (requests.RequestException,ValueError) as exc:
                if isinstance(exc,ValueError) and 'Daily source identity mismatch' not in str(exc):
                    raise
                data=fetch_daily_yahoo(provider_symbol,begin,session)
                data['fallback_reason']=str(exc)[:120]
            quote=q['quotes'].get(symbol)
            if quote:
                previous_session=q['previous_session']
                # 2026-11-06：除息日供应商的「昨收」是除息调整后口径（实测 SYF
                # 2026-11-05 除息：raw 前收 76.03、前复权前收 74.69、实时 pc=74.69）。
                # 因此 pc 允许匹配 raw 前收（普通日）或前复权前收（除息日）——
                # 两者来自同一供应商自身的序列，均需通过 close_enough 容差。
                prev_raw=data['raw'].get(previous_session)
                prev_adj=data['adjusted'].get(previous_session)
                settled=((prev_raw is not None and close_enough(prev_raw,quote['pc']))
                         or (prev_adj is not None and close_enough(prev_adj,quote['pc'])))
                if not settled:
                    raise ValueError('Previous close not settled; retry collection: '+symbol)
                if phase=='close' and (session not in data['raw'] or not close_enough(data['raw'][session],quote['p'])):
                    raise ValueError('Closing bar not settled; retry collection: '+symbol)
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
