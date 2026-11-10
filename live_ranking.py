"""Current-year ranking, independent of historical backfills and site deployment."""
import datetime as dt
import hashlib
import json
from pathlib import Path

import db_ref
import db_sources
import live_quotes
import market_calendar
import run_guard
from live_strategy import sha,positive,store
from prepare_live_ranking import normalized,prepare


def independent_close_ok(quote_price,context_price,rel_tol=3e-3):
    """Compare two *independent* readings of the same closing bar.

    `close_enough` uses a fixed 0.0011 absolute tolerance and is correct for
    replaying one archived series against itself. Here the quote snapshot and
    the ranking context come from different collection passes (and sometimes
    different vendors), so the same 2026-11-06 close legitimately differs by
    a few cents. Measured 2026-11-07 across the live universe: 287/504
    symbols drifted, p95 relative 0.077%, maximum 0.1775% (HUM 433.74 vs
    432.97). The absolute tolerance rejected all of them and froze the
    next-year preview.

    A 0.3% relative tolerance (1.7x headroom over the observed maximum) plus
    a 0.02 absolute floor admits that noise (the floor covers low-priced
    bars where a 0.015 rounding difference is already 0.1%). Wrong symbols,
    stale sessions or a shifted price basis are still rejected: the existing
    test uses a 5% mismatch, and the new boundary tests pin 0.4%-10% down.
    Every drift stays observable via CLOSING_PRICE_DRIFT, so a future
    systematic basis shift is reported even while small noise is tolerated.
    """
    q=positive(quote_price);c=float(context_price)
    return abs(q-c)<=max(0.02,abs(c)*rel_tol)


def joined_interval(review,current,initial,quote_price):
    """Keep original-security native units; bound unresolved ordinary dividends."""
    effective=review['effective_date'];old=review['raw'];base=min(old);last=max(old)
    valid=sorted(d for d in initial['raw'] if d>=effective)
    if not valid:raise ValueError('Missing post-rename boundary')
    first=valid[0]
    ratio=(old[last]/old[base])*(initial['raw'][first]/old[last])
    ratio*=normalized(current)[max(current['raw'])]/normalized(initial)[first]
    ratio*=quote_price/current['raw'][max(current['raw'])]
    upper=ratio;minimum=min(old.values())
    for cash in review['pre_boundary_cash_distributions']:
        # Covers both ex-close reinvestment and vendor previous-close factors.
        if not 0<cash<minimum:raise ValueError('Invalid distribution bound')
        upper/=1-cash/minimum
    return ratio,upper


def continuous_member(symbol,session,members,reviews):
    start=dt.date(int(session[:4]),1,1);end=dt.date.fromisoformat(session)
    review=reviews.get(symbol,{});effective=review.get('effective_date')
    names={symbol,review.get('old_symbol',symbol)}
    intervals=[]
    for name,a,b in members:
        if name not in names:continue
        renamed=effective and review.get('old_symbol')!=symbol
        if renamed and name!=symbol:b=min(b,dt.date.fromisoformat(effective)-dt.timedelta(days=1))
        if renamed and name==symbol:a=max(a,dt.date.fromisoformat(effective))
        a=max(a,start);b=min(b,end)
        if a<=b:intervals.append((a,b))
    covered=start-dt.timedelta(days=1)
    for a,b in sorted(intervals):
        if a>covered+dt.timedelta(days=1):break
        covered=max(covered,b)
    return covered>=end


def select(rows):
    picks=[];counts={}
    for row in sorted(rows,key=lambda r:(-r['ytd'],r['ticker'])):
        if counts.get(row['sector'],0)>=2:continue
        picks.append(dict(row));counts[row['sector']]=counts.get(row['sector'],0)+1
        if len(picks)==3:break
    if len(picks)!=3:raise ValueError('No complete constrained portfolio')
    duplicate=next((s for s,c in counts.items() if c==2),None)
    for i,p in enumerate(picks):
        p.update(rank=i+1,weight=(.3 if p['sector']==duplicate else .4) if duplicate else 1/3)
    return picks


def read_contexts(cursor,year,session,phase,dates,initial=False):
    """Project a few immutable price points; do not transfer source archives each tick."""
    order='symbol,session,created_at' if initial else 'symbol,session desc,created_at desc'
    condition='' if initial else ' and session=%s and phase=%s'
    params=[year] if initial else [year,session,phase]
    cursor.execute('''with chosen as (
        select distinct on(symbol) symbol,payload,evidence_sha256 from live_ranking_context
        where base_year=%s'''+condition+' order by '+order+''')
        select symbol,evidence_sha256,
          jsonb_build_object('raw',jsonb_object_agg(point.key,point.value::numeric),
            'normalized',jsonb_object_agg(point.key,
              coalesce((payload->'normalized'->>point.key)::numeric,
                (payload->'adjusted'->>point.key)::numeric/(payload->'adjusted'->>limits.first_day)::numeric)))
        from chosen cross join lateral (
          select min(key) first_day,max(key) last_day from jsonb_object_keys(payload->'raw') key
        ) limits cross join lateral jsonb_each_text(payload->'raw') point
        where point.key=any(%s) or point.key in(limits.first_day,limits.last_day)
        group by symbol,evidence_sha256''',params+[sorted(dates)])
    result={}
    for symbol,digest,body in cursor.fetchall():
        if len(digest)!=64:raise ValueError('Invalid immutable source evidence pointer')
        body['source_evidence_sha256']=digest;result[symbol]=body
    return result


def calculate(variant,q,pool,sectors,members,reviews,contexts,initial,terms,reference_version):
    rows=[];bounded=[];excluded=[];audit=[]
    session=q['session'];year=int(session[:4]);base=str(year-1)+'-12-31'
    selected_pool={s for s in pool if variant=='A' or continuous_member(s,session,members,reviews)}
    # 观测性：把「跨源精度漂移」与「真正阻断的错价」分开统计。
    # 只看首个标的的报错无法区分零星精度差与整批基准漂移。
    drifts=[];blocking=[]
    for symbol in sorted(selected_pool):
        if symbol not in contexts:continue
        quote=q['quotes'].get(symbol)
        if not quote or q['intraday']:continue
        raw=contexts[symbol]['raw'].get(session)
        if raw is None:continue
        delta=float(quote['p'])-float(raw)
        if abs(delta)>1e-9 and quote['p']:
            drifts.append(abs(delta)/abs(float(quote['p'])))
        if not independent_close_ok(quote['p'],raw):
            blocking.append({'symbol':symbol,'quote':quote['p'],'context':float(raw),
                             'delta':round(delta,6)})
    if drifts or blocking:
        drifts.sort(reverse=True)
        print('CLOSING_PRICE_DRIFT count=%d max_rel=%.6f p95_rel=%.6f blocking=%d %s'
              %(len(drifts),drifts[0] if drifts else 0,
                drifts[min(int(len(drifts)*0.05),len(drifts)-1)] if drifts else 0,
                len(blocking),json.dumps(blocking[:10],ensure_ascii=False)),flush=True)
    def current_value(symbol):
        body=contexts[symbol];day=max(body['raw']);quote=q['quotes'].get(symbol)
        price=positive(quote['p'] if quote else body['raw'][day])
        if day!=session:raise ValueError('Source context session mismatch: '+symbol)
        if quote and not q['intraday'] and not independent_close_ok(price,body['raw'][day]):
            raise ValueError('Independent closing prices disagree: %s (quote=%s context=%s)'
                             %(symbol,price,body['raw'][day]))
        return normalized(body)[day]*price/body['raw'][day]
    def growth(symbol,day):return current_value(symbol)/normalized(initial[symbol])[day]
    for symbol in sorted(selected_pool):
        body=contexts[symbol];first=initial[symbol];review=reviews.get(symbol)
        if review:
            boundary=review.get('first_regular_way_date') or review.get('effective_date')
            if boundary and not base<boundary<=session:review=None
        quote=q['quotes'].get(symbol)
        if not quote:raise ValueError('Missing candidate quote: '+symbol)
        price=positive(quote['p']);current_value(symbol)
        if price<5:
            excluded.append({'ticker':symbol,'reason':'current_as_traded_price_below_5'});continue
        if not sectors.get(symbol) or sectors[symbol]=='Unknown':raise ValueError('Unknown candidate sector: '+symbol)
        if review and review.get('kind')=='new_listing':
            if min(first['raw'])!=review['first_regular_way_date'] or min(first['raw'])<=base:
                raise ValueError('New listing evidence differs: '+symbol)
            excluded.append({'ticker':symbol,'reason':'verified_listing_after_base','source':review['sources'][0]});continue
        if review:
            lower,upper=joined_interval(review,body,first,price)
        else:
            if min(first['raw'])!=base:raise ValueError('Unresolved base identity or price: '+symbol)
            lower=upper=current_value(symbol)
        event=terms.get(symbol)
        if event and base<event['event_date']<=session and event.get('kind') in ('spinoff','spinoff_and_reverse_split'):
            day=event['event_date'];pre=max(d for d in first['raw'] if d<day)
            child=event['distributed_symbol'];child_first=initial[child]
            shares=normalized(first)[pre]/first['raw'][pre]
            parent=shares*float(event['retained_parent_units_per_original_share'])*first['raw'][day]*growth(symbol,day)
            distributed=shares*float(event['distributed_units_per_original_share'])*child_first['raw'][day]*growth(child,day)
            lower=upper=parent+distributed
        asset=terms.get('PSKY',{}).get('additional_asset',{})
        if symbol=='SKYD' and review and not asset:raise ValueError('Missing warrant entitlement evidence')
        if symbol=='SKYD' and base<asset.get('issued_on','')<=session:
            if not review or not terms.get('PSKY',{}).get('additional_asset'):
                raise ValueError('Missing warrant entitlement evidence')
            asset=terms['PSKY']['additional_asset'];warrant=asset['trading_symbol'].replace(' ','_')
            if asset['units_per_common_share']!='1':raise ValueError('Unreviewed warrant units')
            # No ordinary dividends in either common-security segment.
            lower=upper=(price+contexts[warrant]['raw'][session])/review['raw'][base]
        positive(lower);positive(upper)
        audit.append({'ticker':symbol,'lower':lower-1,'upper':upper-1})
        row={'ticker':symbol,'sector':sectors[symbol],'price':price,'ytd':lower-1}
        if abs(upper-lower)>1e-10:bounded.append({'ticker':symbol,'upper':upper-1})
        else:rows.append(row)
    picks=select(rows);threshold=min(p['ytd'] for p in picks)
    if any(r['upper']>=threshold for r in bounded):
        raise ValueError('Unresolved distribution interval could change the selected top three')
    evidence={'variant':variant,'candidate_intervals':audit,'bounded_non_winners':bounded,
              'context_sha256':{s:sha(contexts[s]) for s in sorted(contexts)},
              'identity_sha256':sha(reviews),'corporate_terms_sha256':sha(terms),
              'calculator_sha256':sha({name:hashlib.sha256((Path(__file__).resolve().parent/name).read_bytes()).hexdigest()
                  for name in ('live_ranking.py','prepare_live_ranking.py','live_strategy.py')})}
    payload={'variant':variant,'final':False,'as_of':q['ts_local'],'as_of_ts':q['ts'],
             'fresh_until':q['fresh_until'],
             'session':session,'base_month':str(year-1)+'-12','hold_year':year+1,
             'quote_version':q['version'],'reference_version':reference_version,
             'universe':len(selected_pool),'ranked':len(rows)+len(bounded),
             'excluded':excluded,'excluded_n':len(excluded),'top':picks,
             'near':[r for r in sorted(rows,key=lambda r:(-r['ytd'],r['ticker'])) if r['ticker'] not in {p['ticker'] for p in picks}][:5],
             'selection_proof':'all unresolved return intervals are strictly below the selected portfolio',
             'note':'年初至今动态预览；年末按正式策略完成最终筛选'}
    return payload,evidence


def run():
    # 门禁必须先于 prepare()：采集增量 context 本身就要读已封存证据，
    # 盘中或重复场次跳过它才能真正省下出口流量。
    q=live_quotes.load_snapshot(strict=False)
    if run_guard.should_skip('preview',q):return
    # 收盘场次真要重算时再严格复核报价时效，盘中跳过不受 15 分钟心跳误伤。
    q=live_quotes.load_snapshot()
    prepare()
    pool,sectors,version=db_ref.load_market_reference(q['session'])
    phase='intraday' if q['intraday'] else 'close';year=int(q['session'][:4])
    c=db_sources.db()
    try:
        c.set_session(isolation_level='REPEATABLE READ',readonly=True)
        with c.cursor() as x:
            x.execute('select symbol,start_date,end_date from members order by symbol,start_date');members=x.fetchall()
            x.execute("select metrics from sys_event where source='live_identity_review' and status='pass' order by id");identity=x.fetchall()
            x.execute("select metrics from sys_event where source='verified_corporate_terms' and status='pass' order by id");events=x.fetchall()
            dates={str(year-1)+'-12-31',q['session'],q['previous_session']}
            calendar=market_calendar.load()
            if calendar.sha!=q['calendar_sha256']:raise ValueError('Exchange calendar advanced during ranking')
            for (metrics,) in identity+events:
                proof=metrics['proof'];event=proof.get('terms',proof)
                day=event.get('event_date') or event.get('effective_date') or event.get('first_regular_way_date')
                if day and day[:4]==str(year):
                    dates.add(day);dates.add(calendar.previous(dt.date.fromisoformat(day)).isoformat())
            contexts=read_contexts(x,year,q['session'],phase,{q['session'],q['previous_session']})
            initial=read_contexts(x,year,q['session'],phase,dates,initial=True)
    finally:c.close()
    reviews={};terms={}
    for (metrics,) in identity+events:
        proof=metrics['proof']
        if sha(proof)!=metrics['evidence_sha256']:raise ValueError('Review evidence hash mismatch')
        if 'terms' in proof:terms[proof['terms']['symbol']]=proof['terms']
        else:reviews[proof['symbol']]=proof
    if not pool<=set(contexts):raise ValueError('Incomplete current universe context')
    for variant in ('A','B'):
        payload,evidence=calculate(variant,q,pool,sectors,members,reviews,contexts,initial,terms,version)
        store(variant,'preview',q,payload,evidence)
        print(variant,'preview',[(r['ticker'],round(100*r['ytd'],3)) for r in payload['top']],
              'pool',payload['universe'],'bounded',len(evidence['bounded_non_winners']),flush=True)
    run_guard.mark_done('preview',q)


if __name__=='__main__':run()
