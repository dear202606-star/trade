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
import market_calendar
from live_strategy import fetch_daily,fetch_daily_yahoo,sha,close_enough


def normalized(body):
    if 'normalized' in body:return body['normalized']
    dates=sorted(body['adjusted']);first=dates[0]
    return {d:body['adjusted'][d]/body['adjusted'][first] for d in dates}


def splice_raw_ok(a,b,rel_tol=3e-3):
    """Reconcile the anchor bar across two independent collection passes.

    The original 1e-6 relative tolerance assumed the *same* vendor series.
    When a symbol falls back to another provider (SKYD_WS routinely does),
    the same historical close legitimately differs by a few cents. Use the
    same 0.3% relative / 0.02 absolute envelope as the closing-price check
    in live_ranking; wrong symbols or wrong dates are still far outside it.
    """
    return abs(float(a)-float(b))<=max(0.02,abs(float(b))*rel_tol)


def splice_anchor(old,data,begin):
    """Latest trading day that exists in both the sealed and the fresh series.

    Thinly traded instruments (e.g. the SKYD spin-off warrant) can have no
    bar on `begin`. The splice must still be anchored on a day where both
    series have a real, reconcilable raw price -- never on a fabricated bar.
    """
    overlap=sorted(d for d in set(old['raw'])&set(data['raw']) if d<=begin)
    return overlap[-1] if overlap else None


def factor_rebase_evidence(symbol,old,data,begin,quote,prev_raw,prev_adj,session,
                           calendar=None):
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
      2. The new window contains exactly ONE real factor jump, on a reviewed
         exchange session inside (anchor, session]. The ex-date need NOT be
         the current session: `begin` is deliberately allowed to be several
         sessions old (incremental splice after a missed collection), so a
         dividend paid on an intermediate session is perfectly legitimate.
      3. The scaling ratio stays within 5%, catching gross mismatches.

    2026-11-11: the old guard required `jumps[0] == session`. When the
    2026-11-09 collection was skipped, AMP/GWW/IBM/VMC (all ex-dividend on
    11-09) were sealed from 11-06 and spliced only on 11-10, so their lawful
    re-base was rejected forever as "source corruption". Measured source bars
    (push2his 106.*, fqt=0/1): AMP raw 501.49 / adj 499.79 on 11-06, then
    raw==adj from 11-09; GWW 2.49, VMC 0.52, IBM 1.69 (ex-date 11-10).
    This was a wrong assumption, not a data-quality exception -- no tolerance
    is loosened below; three new self-consistency checks are ADDED.

    Jump detection uses the absolute (raw - adjusted) cash difference, not the
    adjusted/raw ratio: the vendor quotes prices to 2 decimals, so the ratio
    carries ~1e-4 rounding noise (IBM produced a phantom 11-09 jump) while a
    single cash dividend is an exactly constant difference. The pre-ex-date
    difference must be one positive constant and the post-ex-date series must
    sit exactly at factor 1.0; any other shape stays fail-closed.

    Returns the evidence dict to be written into the context, or None when
    no re-base occurred.
    """
    a=old['raw'][begin];b=data['raw'][begin]
    f_old=old['adjusted'][begin]/a;f_new=data['adjusted'][begin]/b
    gap=abs(f_old-f_new)/max(f_old,f_new)
    if gap<=0.001:return None
    problems=[]
    # Vendor prices carry 2 decimals. A dividend re-base subtracts ONE fixed
    # cash amount from every pre-ex-date bar, so raw-adjusted is an exact
    # constant while the adjusted/raw ratio wobbles by ~1e-4. Detecting the
    # jump on the cash difference removes that phantom noise (IBM 11-09).
    days=sorted(d for d in data['raw'] if d>=begin)
    diffs=[float(data['raw'][d])-float(data['adjusted'][d]) for d in days]
    TOL=0.02
    jumps=[i for i in range(1,len(diffs)) if abs(diffs[i]-diffs[i-1])>TOL]
    ex_days=[days[i] for i in jumps]
    ex_date=ex_days[0] if len(ex_days)==1 else None
    if ex_date is None:
        problems.append('unstable factor shape: %s' % ex_days)
    else:
        head,tail=diffs[:jumps[0]],diffs[jumps[0]:]
        if not head or head[0]<=TOL or any(abs(v-head[0])>TOL for v in head):
            problems.append('pre-ex-date cash adjustment is not one constant dividend')
        if not tail or any(abs(v)>TOL for v in tail):
            problems.append('post-ex-date series is not at factor 1.0')
        if not (begin<ex_date<=session):
            problems.append('ex-date outside splice window: %s' % ex_date)
        if calendar is not None:
            try:
                valid=calendar.bounds(dt.date.fromisoformat(ex_date)) is not None
            except Exception:
                valid=False
            if not valid:
                problems.append('ex-date is not a reviewed exchange session: %s' % ex_date)
        elif dt.date.fromisoformat(ex_date).weekday()>4:
            # Offline/unit-test path: at least reject weekends. Production
            # always passes the reviewed online NYSE calendar (see prepare()).
            problems.append('ex-date is not an exchange weekday: %s' % ex_date)
    ratio=f_new/f_old
    if not 0.95<=ratio<=1.05:
        problems.append('gross factor ratio %.6f' % ratio)
    # Independent evidence from the sealed quote snapshot. When the ex-date IS
    # the current session, `pc` must be the ex-dividend adjusted previous close
    # (adj != raw). When the ex-date is an EARLIER session (a splice spanning
    # missed collections), `pc` is already post-ex-dividend, so instead require
    # it to reconcile exactly with the source bar at the sealed previous
    # session -- this still rejects wrong symbols / wrong dates / corrupted
    # series while accepting the legitimate cross-session re-base.
    if quote is None or prev_raw is None or prev_adj is None:
        problems.append('quote snapshot does not confirm ex-dividend')
    elif ex_date==session:
        if not (close_enough(prev_adj,quote['pc'])
                and not close_enough(prev_raw,quote['pc'])):
            problems.append('quote snapshot does not confirm ex-dividend')
    elif not (close_enough(prev_raw,quote['pc'])
              or close_enough(prev_adj,quote['pc'])):
        problems.append('quote snapshot does not confirm the sealed previous close')
    if problems:
        raise ValueError('Incremental source adjustment factor discontinuity: %s (%s)'
                         %(symbol,'; '.join(problems)))
    return {'begin':begin,'session':session,'ex_date':ex_date,
            'f_old':f_old,'f_new':f_new,'ratio':ratio,'gap':gap,
            'cash_amount':diffs[0] if diffs else None,
            'ex_dividend_days':ex_days,'verified_by':'sealed_quote_snapshot_pc'}


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


def prepare(path=None, *, snapshot=None):
    q=snapshot if snapshot is not None else live_quotes.load_snapshot()
    session=q['session'];year=int(session[:4]);base=str(year-1)+'-12-31'
    phase='intraday' if q['intraday'] else 'close'
    pool,_,_=db_ref.load_market_reference(session)
    connection=db_sources.db()
    try:
        # The ex-date of a cross-session dividend must be a REVIEWED exchange
        # session, never a date invented by the price vendor. Reuse the same
        # connection (no extra egress) and bind it to the sealed quote
        # snapshot's calendar fingerprint so a calendar swap cannot slip in.
        with connection.cursor() as cursor:
            cursor.execute('select calendar,evidence_sha256 from v_market_calendar')
            rows=cursor.fetchall()
        if len(rows)!=1:raise RuntimeError('No unique reviewed online exchange calendar')
        calendar=market_calendar.Calendar(*rows[0])
        if calendar.sha!=q.get('calendar_sha256'):
            raise RuntimeError('Reviewed exchange calendar changed before ranking collection')
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
                where base_year=%s and session<%s and phase='close'
                order by symbol,session desc,created_at desc''',(year,session))
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
            def fetch_window(start):
                try:
                    d=fetch_daily(provider_symbol,market,start,session)
                    d['provider']='eastmoney'
                except (requests.RequestException,ValueError) as exc:
                    if isinstance(exc,ValueError) and 'Daily source identity mismatch' not in str(exc):
                        raise
                    d=fetch_daily_yahoo(provider_symbol,start,session)
                    d['fallback_reason']=str(exc)[:120]
                return d
            data=fetch_window(begin)
            # 低流动性证券（如 SKYD_WS 分拆权证）在衔接日可能无成交，新窗口
            # 里就没有 begin 这根 bar。往前扩 20 个自然日重取一次，让新旧
            # 序列至少有一个共同交易日可作拼接锚点；只影响确有缺口的标的。
            if previous and begin not in data['raw']:
                wide=max(base,(dt.date.fromisoformat(begin)-dt.timedelta(days=20)).isoformat())
                data=fetch_window(wide)
                data['splice_window']={'requested':begin,'used':wide,
                                       'reason':'no bar on splice begin'}
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
                if session not in data['raw'] or session not in data['adjusted']:
                    raise ValueError('Session raw/adjusted pair unavailable; retry collection: '+symbol)
                if phase=='close' and not close_enough(data['raw'][session],quote['p']):
                    raise ValueError('Closing bar not settled; retry collection: '+symbol)
            body={'symbol':symbol,'provider_symbol':provider_symbol,'market':market,
                  'source_start':min(data['raw']),'source_end':session,
                  'splice_begin':begin,**data}
            if previous:
                old,digest=previous;values=normalized(old)
                # 拼接锚点取「新旧序列共有、且不晚于 begin」的最近交易日。
                # 稀疏标的 begin 当日可能无成交，但锚点必须真实存在，且两侧
                # raw 完全一致——绝不捏造价格或放宽对账。
                overlap_count=len(set(old['raw'])&set(data['raw']))
                anchor=splice_anchor(old,data,begin)
                if anchor is None or anchor not in values or anchor not in data['adjusted']:
                    raise ValueError('Incremental source cannot reconcile previous close: %s (begin=%s overlap=%s new_days=%s)'
                                     %(symbol,begin,overlap_count,sorted(data['raw'])[:6]))
                # 2026-11-06：Yahoo 为 float32 精度（376.5299987792969 vs 东财
                # 376.53）；2026-11-07：兜底切换供应商后，同一历史收盘价也会
                # 有几分钱尾差。跨通路锚点对账用相对容差，仍远严于错标的。
                a=old['raw'][anchor];b=data['raw'][anchor]
                if not splice_raw_ok(a,b):
                    raise ValueError('Incremental source cannot reconcile previous close: %s (anchor=%s old=%.6f new=%.6f delta=%.6f)'
                                     %(symbol,anchor,a,b,float(a)-float(b)))
                # 衔接点的「绝对复权因子」在标的除息后必然改变（主源把除息
                # 日之前的历史整体下调）。拼接算法按锚点归一化，本来就能
                # 正确处理这种整体缩放并还原分红；此处不再直接拒绝，而是
                # 要求行情快照独立佐证除息 + 新序列形状自洽 + 缩放幅度合理，
                # 三项全过才放行并写入可审计证据，其余情况仍 fail-closed。
                rebase=factor_rebase_evidence(symbol,old,data,anchor,quote,
                                              prev_raw,prev_adj,session,calendar)
                body['normalized']={d:values[anchor]*v/data['adjusted'][anchor] for d,v in data['adjusted'].items()}
                if rebase:body['factor_rebase']=rebase
                body['splice_anchor']=anchor
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
        # targets 里的每个符号都是 calculate 的实际输入：pool 之外还包括
        # FDXF/HONA/SPY 等基准，以及 SKYD 分拆权证 SKYD_WS（calculate 直接
        # 索引 contexts[warrant]）。因此任何采集失败都必须 fail-closed，
        # 绝不因「不在 pool」而降级放行——否则只会把故障推迟成 KeyError。
        summary={'session':session,'requested':len(targets),'stored':success,
                 'already_online':len(done),'errors':errors}
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
