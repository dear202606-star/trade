"""Whole-year index membership for explicitly verified same-share renames."""
import datetime as dt
import hashlib
import json
import db_select

POLICY='verified_same_equity_continuity'
_ALIASES=None


def load_aliases():
    global _ALIASES
    if _ALIASES is not None:return _ALIASES
    connection=None
    try:
        connection=db_select.db()
        with connection.cursor() as cursor:
            cursor.execute('select old_symbol,current_symbol,effective_date,evidence_url from v_verified_ticker_renames order by old_symbol,effective_date')
            rows=cursor.fetchall()
        aliases=[{'old':a,'current':b,'effective_date':str(date),'evidence':url} for a,b,date,url in rows]
    except Exception:
        import db_rest
        rows=db_rest.get('v_verified_ticker_renames',{'select':'old_symbol,current_symbol,effective_date,evidence_url',
                                                    'order':'old_symbol,effective_date'})
        aliases=[{'old':r['old_symbol'],'current':r['current_symbol'],
                  'effective_date':r['effective_date'],'evidence':r['evidence_url']} for r in rows]
    finally:
        if connection is not None:connection.close()
    _ALIASES=aliases
    return aliases


def input_sha():
    return hashlib.sha256(json.dumps(load_aliases(),sort_keys=True,separators=(',',':')).encode()).hexdigest()


def members_full_year(frame,year):
    start=dt.date(year,1,1);end=dt.date(year,12,31)
    result=set(frame[(frame.start_date<=str(start))&(frame.end_date>=str(end))].ticker.unique())
    aliases=load_aliases()
    seen=set()
    for alias in aliases:
        date=dt.date.fromisoformat(alias['effective_date'])
        if date.year!=year:continue
        old,current=alias['old'],alias['current']
        if old==current or {old,current}&seen:
            raise RuntimeError('Conflicting same-year security aliases require review')
        seen.update((old,current))
        intervals=[]
        for row in frame[frame.ticker.isin((old,current))].itertuples():
            a=dt.date.fromisoformat(str(row.start_date)[:10])
            b=dt.date.fromisoformat(str(row.end_date)[:10])
            # Do not attach a later reuse of the old code to the old issuer.
            if row.ticker==old:b=min(b,date-dt.timedelta(days=1))
            else:a=max(a,date)
            a=max(a,start);b=min(b,end)
            if a<=b:intervals.append((a,b))
        result.difference_update((old,current))
        covered=start-dt.timedelta(days=1)
        for a,b in sorted(intervals):
            if a>covered+dt.timedelta(days=1):break
            covered=max(covered,b)
        if covered>=end:result.add(current)
    return result
