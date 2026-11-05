"""Seal current-year account basis from paired prices, retaining the migration version."""
import copy
import datetime as dt
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from psycopg2.extras import Json
import db_sources
import live_quotes
import market_calendar
from live_strategy import sha,positive,close_enough,validate_anchor


def rebuild(original,contexts,calendar,session):
    body=copy.deepcopy(original);equity=body['equity'];base=dt.datetime.strptime(equity['base_date'],'%Y%m%d').date()
    dates=[];day=base
    while day<=dt.date.fromisoformat(session):
        if calendar.bounds(day):dates.append(day.isoformat())
        day+=dt.timedelta(days=1)
    positions=equity['position'];symbols={p['ticker'] for p in positions}|{'SPY'}
    for symbol in symbols:
        raw,adj=contexts[symbol]['raw'],contexts[symbol]['adjusted']
        if not set(dates)<=set(raw) or not set(dates)<=set(adj):raise ValueError('Incomplete current-year account source: '+symbol)
        for day in dates:positive(raw[day]);positive(adj[day])
    for p in positions:
        source=contexts[p['ticker']]
        if not close_enough(source['raw'][str(base)],p['entry_raw']):raise ValueError('Year-end native entry disagrees')
        p.update(entry=source['adjusted'][str(base)],entry_raw=source['raw'][str(base)],
                 last=source['adjusted'][session],last_raw=source['raw'][session],last_date=session.replace('-',''))
    equity['points']=[{'d':d.replace('-',''),
        's':sum(p['weight']*contexts[p['ticker']]['adjusted'][d]/p['entry'] for p in positions),
        'b':contexts['SPY']['adjusted'][d]/contexts['SPY']['adjusted'][str(base)]} for d in dates]
    last=equity['points'][-1];peak=0;drawdown=0
    for point in equity['points']:
        peak=max(peak,point['s']);drawdown=min(drawdown,point['s']/peak-1)
    equity['stats'].update(strategy=last['s']-1,benchmark=last['b']-1,excess=last['s']-last['b'],mdd=drawdown,days=len(dates))
    equity['last_session']=session.replace('-','')
    body['basis_review']={'scope':'current 2026 account only; no accepted prior-year history changed',
        'ordinary_cash_dividends':'same-vintage provider adjusted-return convention',
        'corporate_distributions':'no non-price corporate distribution in these 2026 holdings',
        'input_sha256':{s:sha(contexts[s]) for s in sorted(symbols)}}
    validate_anchor(body)
    return body


def run():
    q=live_quotes.load_snapshot();calendar=market_calendar.load();c=db_sources.db()
    try:
        with c.cursor() as x:
            x.execute("select variant,payload from live_strategy_anchor where hold_year=2026 and revision=1")
            anchors=x.fetchall()
            x.execute("select symbol,payload,evidence_sha256 from live_ranking_context where base_year=2026 and session=%s and phase='close'",(q['session'],))
            sources=x.fetchall();contexts={}
            for symbol,payload,digest in sources:
                if sha(payload)!=digest:raise ValueError('Paired-price evidence hash mismatch')
                contexts[symbol]=payload
            x.execute("select metrics from sys_event where source='verified_corporate_terms' and status='pass'")
            events=[]
            for (metrics,) in x.fetchall():
                if sha(metrics['proof'])!=metrics['evidence_sha256']:raise ValueError('Corporate event proof mismatch')
                events.append(metrics['proof']['terms'])
            for variant,old in anchors:
                x.execute('select 1 from live_strategy_anchor where variant=%s and hold_year=2026 and revision=2',(variant,))
                if x.fetchone():continue
                held={p['ticker'] for p in old['equity']['position']}|{'SPY'}
                if any(t['symbol'] in held and '2025-12-31'<t['event_date']<=q['session'] for t in events):
                    raise ValueError('Account basis crosses a non-price corporate distribution')
                for position in old['equity']['position']:
                    s=position['ticker']
                    if not close_enough(contexts[s]['raw'][q['session']],q['quotes'][s]['p']):raise ValueError('Independent current close differs')
                body=rebuild(old,contexts,calendar,q['session'])
                x.execute('''insert into live_strategy_anchor(variant,hold_year,revision,payload,evidence_sha256)
                    values(%s,2026,2,%s,%s)''',(variant,Json(body),sha(body)))
                print(variant,'verified current-year basis',body['equity']['stats']['strategy'],flush=True)
        c.commit()
    finally:c.close()


if __name__=='__main__':run()
