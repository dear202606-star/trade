"""Plan liquidity collection from live point-in-time database inputs only."""
import collections
import datetime as dt
import json
import sys
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import db_select
import liquidity_prices


def run():
    now=dt.datetime.now(dt.timezone.utc).date()
    latest=(now.replace(day=1)-dt.timedelta(days=1)).strftime('%Y-%m')
    official=latest if latest.endswith('-12') else '%d-12'%(int(latest[:4])-1)
    months=sorted({latest,official});rows=[]
    connection=db_select.db();connection.autocommit=False
    try:
        with connection.cursor() as cursor:
            cursor.execute('set transaction isolation level repeatable read read only')
            for month in months:
                base='%d-%s'%(int(month[:4])-1,month[5:])
                cursor.execute('''with eligible as (
                    select p.symbol,min(p.basis) as basis from series_selected p
                    where p.month between %s and %s and exists (
                     select 1 from members m where m.symbol=p.symbol
                     and m.start_date<=(to_date(%s||'-01','YYYY-MM-DD')+interval '1 month - 1 day')::date
                     and m.end_date>=(to_date(%s||'-01','YYYY-MM-DD')+interval '1 month - 1 day')::date)
                    group by p.symbol having count(distinct p.month)=13)
                    select e.symbol,e.basis,n.close,l.verified,l.calculator_sha256
                    from eligible e left join v_nominal_monthly n on n.symbol=e.symbol and n.month=%s
                    left join v_liquidity_windows l on l.symbol=e.symbol and l.base_month=%s and l.rank_month=%s
                    order by e.symbol''',(base,month,month,month,month,base,month))
                for symbol,basis,price,verified,calculator in cursor.fetchall():
                    current=verified is True and calculator==liquidity_prices.calculator_sha()
                    status=('below_known_price_floor' if price is not None and price<5 else
                            'verified_liquidity' if current else
                            'accepted_version_review_required' if verified is True else 'missing_liquidity')
                    rows.append({'symbol':symbol,'base_month':base,'rank_month':month,'price_basis':basis,
                                 'nominal_close':float(price) if price is not None else None,'status':status})
    finally:connection.close()
    targets=collections.defaultdict(list)
    for row in rows:
        if row['status']=='missing_liquidity':targets[row['rank_month']].append(row['symbol'])
    result={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
            'definition':'point-in-time index member, 13 selected months; confirmed sub-$5 excluded; unknown native prices retained',
            'scope':'collection plan only; no ranking, source selection, or final data acceptance',
            'status_counts':dict(collections.Counter(r['status'] for r in rows)),
            'price_basis_counts':dict(collections.Counter(r['price_basis'] for r in rows)),
            'targets_by_month':dict(targets),'rows':rows}
    (BASE/'output/liquidity_inventory.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('rows','targets_by_month')},indent=2))
    return result


if __name__=='__main__':run()
