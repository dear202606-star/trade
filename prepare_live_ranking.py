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


def factor_rebase_evidence(symbol,old,data,begin,quote,prev_raw,prev_adj,session):
    """Audit a change of the adjustment basis at the splice point.

    A dividend/split makes the provider re-base its whole forward-adjusted
    history: bars before the ex-date are scaled down while the latest bar
    stays at factor 1.0. A context sealed before the ex-date therefore has
    f_old = adj[begin]/raw[begin] ~ 1.0, while the freshly fetched window
    has f_new < 1.0. The splice itself

        normalized[d] = values[begin] * adjusted[d] / adjusted[begin]

    is anchored at `begin` and only depends on the *relative shape* of the
    new window, so a pure re-base is already handled correctly -- it even
    restores the dividend into the total return. Requiring f_old == f_new
    rejects every ex-dividend day, which is a false positive, not a guard.

    We do NOT simply loosen the tolerance. A re-base is accepted only when
    three independent facts hold; anything else keeps the original
    fail-closed rejection (wrong symbol, corrupted source, method change):

      1. The sealed quote snapshot independently proves an ex-dividend:
         `pc` equals the forward-adjusted previous close (adjusted[begin])
         but not the unadjusted previous close (raw[begin]).
      2. The new window's adjusted/raw factor jumps at most once, and only
         on the latest session (the ex-date), i.e. the window is a single
         constant scaling of the raw series.
      3. The scaling ratio stays within 5%, catching gross mismatches.

    Returns the evidence dict to be written into the context, or None when
    no re-base occurred.
    """
    a=old['raw'][begin];b=data['raw'][begin]
    f_old=old['adjusted'][begin]/a;f_new=data['adjusted'][begin]/b
    gap=abs(f_old-f_new)/max(f_old,f_new)
    if gap<=0.001:return None
    problems=[]
    if not (quote and prev_raw is not None and prev_adj is not None
            and close_enough(prev_adj,quote['pc'])
            and not close_enough(prev_raw,quote['pc'])):
        problems.append('quote snapshot does not confirm ex-dividend')
    days=sorted(data['raw'])
    factors=[data['adjusted'][d]/data['raw'][d] for d in days]
    jumps=[days[i] for i in range(1,len(factors))
           if abs(factors[i]-factors[i-1])>1e-6]
    if len(jumps)>1 or (jumps and jumps[0]!=session):
        problems.append('unstable factor shape: %s' % jumps)
    ratio=f_new/f_old
    if not 0.95<=ratio<=1.05:
        problems.append('gross factor ratio %.6f' % ratio)
    if problems:
        raise ValueError('Incremental source adjustment factor discontinuity: %s (%s)'
                         %(symbol,'; '.join(problems)))
    return {'begin':begin,'session':session,'f_old':f_old,'f_new':f_new,
            'ratio':ratio,'gap':gap,'ex_dividend_days':jumps,
            'verified_by':'sealed_quote_snapshot_pc'}


def classify_errors(errors,pool):
    """Split failures into blocking (ranking universe) and advisory (auxiliary codes).

    Ranking only consumes symbols in `pool`. Legacy helper / warrant codes are
    collected as a convenience, so their failure is recorded but must not stop
    the whole pipeline. Nothing is dropped: advisory entries stay in the event.
    """
    return ([e for e in errors if e['symbol'] in pool],
            [e for e in errors if e['symbol'] not in pool])


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
            prev_raw=prev_adj=None
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
                if begin not in values or begin not in data['raw']:
                    raise ValueError('Incremental source cannot reconcile previous close: '+symbol)
                # 2026-11-06：Yahoo 为 float32 精度（376.5299987792969 vs 东财
                # 376.53），跨供应商对账必须用相对容差而非精确相等。
                a=old['raw'][begin];b=data['raw'][begin]
                if abs(a-b)>1e-6*max(abs(a),abs(b)):
                    raise ValueError('Incremental source cannot reconcile previous close: '+symbol)
                # 衔接点的「绝对复权因子」在标的除息后必然改变（主源把除息
                # 日之前的历史整体下调）。拼接算法按 begin 锚定，本来就能
                # 正确处理这种整体缩放并还原分红；此处不再直接拒绝，而是
                # 要求行情快照独立佐证除息 + 新序列形状自洽 + 缩放幅度合理，
                # 三项全过才放行并写入可审计证据，其余情况仍 fail-closed。
                rebase=factor_rebase_evidence(symbol,old,data,begin,quote,
                                              prev_raw,prev_adj,session)
                body['normalized']={d:values[begin]*v/data['adjusted'][begin] for d,v in data['adjusted'].items()}
                if rebase:body['factor_rebase']=rebase
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
        # pool 之外的符号是历史遗留的辅助/权证代码（如 SKYD_WS），排名计算
        # 只消费 pool 内标的；它们采集失败不该让整条链停摆。但仍完整记录并
        # 标记 status=warn，绝不静默降级。pool 内任何失败仍然 fail-closed。
        blocking,advisory=classify_errors(errors,pool)
        summary={'session':session,'requested':len(targets),'stored':success,
                 'already_online':len(done),'errors':blocking,
                 'advisory_errors':advisory}
        with connection.cursor() as cursor:
            cursor.execute("""insert into sys_event(kind,source,subject,status,metrics,finished_at)
                values('fetch','live_ranking_context',%s,%s,%s,now())""",
                (session,'fail' if blocking else ('warn' if advisory else 'ok'),Json(summary)))
        connection.commit();print(json.dumps(summary,ensure_ascii=False),flush=True)
        return not blocking
    finally:connection.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--initialize-directory',type=Path)
    a=p.parse_args();raise SystemExit(0 if prepare(a.initialize_directory) else 1)
