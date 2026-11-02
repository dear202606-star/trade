"""Normalize four documented same-equity renames using existing live DB data.

No external fetch, numerical price change, source substitution, or merger
exchange mapping. Default is read-only. --write copies exact numeric values
through staging only after independent returns and B impact checks pass.
"""
import argparse
import json
import statistics
import math
from pathlib import Path

import db_sources
import ingest_validate
from research.audit_rename_impact import run as audit_impact

BASE = Path(__file__).resolve().parent


def check_prices(cursor, old, current, through):
    cursor.execute("""select month,adj_close,raw_close,basis,source from series_selected
                      where symbol=%s and month<=%s order by month""", (current, through))
    rows = cursor.fetchall()
    if not rows or any(basis != 'total_return' or source != 'yahoo_relay' for m,a,r,basis,source in rows):
        raise RuntimeError('Canonical source is not the reviewed Yahoo input: %s' % current)
    cursor.execute("""select count(*) from series_selected s join prices_monthly p using(symbol,month,source)
                      where s.symbol=%s and s.month<=%s and p.quality is distinct from 'ok'""",(current,through))
    if cursor.fetchone()[0]:
        raise RuntimeError('Canonical source contains unapproved quality flags')
    bars = {month: (float(adj),float(close) if close is not None else None)
            for month,adj,close,basis,source in rows}
    if any(close is None or not all(math.isfinite(v) and v>0 for v in (adj,close)) for adj,close in bars.values()):
        raise RuntimeError('Canonical price is missing or invalid')
    ok, reason, cleaned = ingest_validate.check_payload(old,bars,'total_return',allow_stale=True,min_months=100)
    if not ok:
        raise RuntimeError('%s: %s' % (old,reason))
    cursor.execute("""select month,adj_close from prices_monthly where symbol=%s
                      and source='kaggle_cc0' and month<='2017-10' order by month""", (old,))
    independent = {month:float(adj) for month,adj in cursor.fetchall()}
    common = sorted(set(bars)&set(independent))
    deviations = [abs(bars[b][0]/bars[a][0]-independent[b]/independent[a]) for a,b in zip(common,common[1:])]
    if len(deviations)<24 or statistics.median(deviations)>.001 or max(deviations)>.01:
        raise RuntimeError('Independent return match failed: %s' % old)
    return {'old':old,'current':current,'through':through,'months':len(cleaned),
            'independent_return_pairs':len(deviations),'median_return_deviation':statistics.median(deviations),
            'max_return_deviation':max(deviations)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write',action='store_true')
    args=parser.parse_args()
    impact=audit_impact()
    if impact['changed_holdings_or_returns'] or impact['official_2026_changed']:
        raise SystemExit('B result changed; no staging writes allowed without review')
    aliases=impact['aliases']
    checks=[]
    with db_sources.db() as connection,connection.cursor() as cursor:
        cursor.execute("select basis from data_source where source_id='yahoo_relay'")
        if cursor.fetchone()!=('total_return',):
            raise RuntimeError('Reviewed provider is not registered')
        # Validate every mapping before making the first staging write.
        for entry in aliases:
            old,current=entry['old'],entry['current']
            cursor.execute('select exists(select 1 from locked_symbols where symbol=%s),exists(select 1 from series_quarantine where symbol=%s)',(old,old))
            if any(cursor.fetchone()):
                raise RuntimeError('Target is locked or quarantined: %s' % old)
            checks.append(check_prices(cursor,old,current,entry['effective_date'][:4]+'-12'))
        staged=0
        if args.write:
            for check in checks:
                cursor.execute("""insert into ingest_staging(symbol,month,adj_close,raw_close,source,quality,basis)
                    select %s,p.month,p.adj_close,p.raw_close,p.source,origin.quality,p.basis
                    from series_selected p join prices_monthly origin using(symbol,month,source)
                    where p.symbol=%s and p.source='yahoo_relay' and origin.quality='ok'
                      and p.basis='total_return' and p.month<=%s
                      and not exists(select 1 from prices_monthly x where x.symbol=%s
                                     and x.month=p.month and x.source=p.source)
                      and not exists(select 1 from ingest_staging x where x.symbol=%s
                                     and x.month=p.month and x.source=p.source)""",
                               (check['old'],check['current'],check['through'],check['old'],check['old']))
                staged+=cursor.rowcount
    result={'write':args.write,'staged':staged if args.write else 0,'checks':checks,
            'historical_B_unchanged':True,'official_B_2026_unchanged':True,
            'provenance':'static/verified_ticker_renames.json; exact numeric DB copies from reviewed canonical Yahoo rows'}
    if args.write:
        (BASE/'output/verified_rename_ingest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
