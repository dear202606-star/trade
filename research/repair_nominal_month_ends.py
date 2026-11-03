"""Accept five individually corroborated month ends; preserve all old observations."""
import argparse
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import db_sources
from import_verified_closing_facts import verify_exact_close
from research.audit_nominal_month_ends import daily_month_end

FACTS = (
    ('BKNG', '2018-02-28', 2034.04, 105, '2018-02-26', 1905.64),
    ('MMM', '2017-11-30', 243.14, 106, '2017-11-28', 235.63),
    ('MMM', '2017-12-29', 235.37, 106, '2017-12-05', 238.26),
    ('TRV', '2010-08-31', 48.99, 106, '2010-08-27', 49.87),
    ('TRV', '2011-08-31', 50.46, 106, '2011-08-25', 47.99),
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     default=str).encode()).hexdigest()


def review_fact(fact, kaggle, selected):
    symbol, date, price, market, old_date, old_price = fact
    month = date[:7]
    path = BASE/'output/_eastmoney_probe/monthly'/(symbol+'-month-end-review.json')
    raw = path.read_bytes(); record = json.loads(raw)
    url = urlparse(record['source_url']); params = parse_qs(url.query)
    if (url.scheme != 'https' or url.netloc != 'push2his.eastmoney.com'
            or params.get('secid') != [str(market)+'.'+symbol]
            or params.get('fqt') != ['0'] or params.get('klt') != ['103']
            or record.get('secid') != str(market)+'.'+symbol
            or record.get('mode') != 'fqt=0' or record['payload'].get('rc') != 0):
        raise ValueError('Unreviewed native request identity or adjustment')
    verify_exact_close(record['payload']['data'], {'symbol':symbol,'date':date,'close':price}, market)
    stock = daily_month_end(BASE/'output/_nominal_daily'/(symbol+'.json'), symbol, month)
    calendar = daily_month_end(BASE/'output/_nominal_daily/SPY.json', 'SPY', month)
    if stock['date'] != date or calendar['date'] != date or stock['close'] != price:
        raise ValueError('Independent daily close or benchmark session differs')
    if (kaggle is None or str(kaggle[1]) != date or abs(float(kaggle[0])-price) > .001
            or not kaggle[2] or not kaggle[3]):
        raise ValueError('Reviewed native-share unit evidence differs')
    # Reuse the existing raw-share unit proof, without interpreting adjusted
    # return prices as actual historical dollars.
    daily = json.loads((BASE/'output/_nominal_daily'/(symbol+'.json')).read_text())
    monthly = {}
    for row in sorted(daily['rows'], key=lambda r:r['d']):
        value = float(row['c'])
        if not math.isfinite(value) or value <= 0:
            raise ValueError('Invalid independent history')
        monthly[row['d'][:7]] = value
    common = sorted(set(monthly)&set(selected)); deviations = []
    for a,b in zip(common, common[1:]):
        if (int(b[:4])-int(a[:4]))*12+int(b[5:])-int(a[5:]) != 1:
            continue
        deviations.append(abs(math.log((monthly[b]/monthly[a])/(selected[b]/selected[a]))))
    if len(deviations) < 24:
        raise ValueError('Insufficient independent return overlap')
    median = statistics.median(deviations)
    p90 = sorted(deviations)[int((len(deviations)-1)*.9)]
    if median > .005 or p90 > .05:
        raise ValueError('Selected-security returns disagree')
    return {'symbol':symbol,'month':month,'date':date,'close':price,
            'old_date':old_date,'old_close':old_price,'old_source':'quandl_wiki',
            'five_dollar_eligibility_changed':(old_price >= 5) != (price >= 5),
            'primary_url':record['source_url'],'primary_cache_sha256':hashlib.sha256(raw).hexdigest(),
            'independent_daily':stock,'benchmark_calendar':calendar,
            'native_unit_source':'kaggle_yfinance2022','native_unit_close':float(kaggle[0]),
            'native_unit_evidence':kaggle[2],'native_unit_sha256':kaggle[3],
            'return_pairs':len(deviations),'median_log_deviation':median,'p90_log_deviation':p90,
            'identity_crosscheck_period':[common[0],common[-1]],
            'identity_crosscheck_is_not_a_strategy_ranking_input':True,
            'scope':'Only this month-end screening fact; no return-price correction or other date approved'}


def run(write=False, rollback=False):
    connection = db_sources.db()
    try:
        connection.set_session(isolation_level='REPEATABLE READ')
        with connection.cursor() as cursor:
            cursor.execute("set local statement_timeout='45s'")
            cursor.execute("set local lock_timeout='5s'")
            pending = []
            for fact in FACTS:
                symbol,date,price,market,old_date,old_price = fact
                cursor.execute('select pg_advisory_xact_lock(hashtext(%s)::bigint)',
                               ('nominal_fact:'+symbol+':'+date[:7],))
                cursor.execute("""select n.close,n.trade_date,n.evidence_sha256,e.metrics
                    from sys_event e join nominal_monthly n on n.symbol=%s and n.month=%s
                    and n.source='eastmoney_native' and n.evidence_sha256=e.metrics->>'evidence_sha256'
                    where e.kind='validation' and e.status='pass'
                    and e.source in ('verified_closing_fact','fund_native_closing_fact')
                    and e.subject=%s""", (symbol,date[:7],symbol+':'+date))
                accepted = cursor.fetchall()
                if accepted:
                    if any(float(p)!=price or str(d)!=date or sha!=digest(metrics['proof'])
                           for p,d,sha,metrics in accepted):
                        raise RuntimeError('Accepted fact conflicts; explicit version review required')
                    continue
                cursor.execute('select source,close,trade_date from v_nominal_monthly where symbol=%s and month=%s', (symbol,date[:7]))
                old = cursor.fetchone()
                if old is None or (old[0],float(old[1]),str(old[2])) != ('quandl_wiki',old_price,old_date):
                    raise RuntimeError('Reviewed old selection changed')
                cursor.execute("select 1 from nominal_monthly where symbol=%s and month=%s and source='eastmoney_native'", (symbol,date[:7]))
                if cursor.fetchone():
                    raise RuntimeError('Target source already exists; no overwrite')
                pending.append(fact)
            if not pending:
                connection.rollback()
                print('All five facts already accepted; no cache processing or writes')
                return
            symbols = sorted({f[0] for f in pending})
            cursor.execute('select symbol from series_pick where symbol=any(%s) order by symbol for share',
                           (symbols,))
            if [row[0] for row in cursor.fetchall()] != symbols:
                raise RuntimeError('Selected security decision missing')
            cursor.execute('select symbol from nominal_monthly where symbol=any(%s) for share',
                           (symbols,))
            cursor.fetchall()
            snapshots = {}
            for name,query in (
                ('returns','select * from series_selected where symbol=any(%s) order by symbol,month'),
                ('picks','select * from series_pick where symbol=any(%s) order by symbol'),
                ('locks','select * from data_lock where symbol=any(%s) order by id'),
                ('observations',"select * from nominal_monthly where symbol=any(%s) and source<>'eastmoney_native' order by symbol,month,source")):
                cursor.execute(query,(symbols,)); snapshots[name] = (query,digest(cursor.fetchall()))
            proofs = []
            for fact in pending:
                symbol,date = fact[:2]
                cursor.execute('select month,adj_close from series_selected where symbol=%s order by month', (symbol,))
                selected = {m:float(p) for m,p in cursor.fetchall()}
                cursor.execute("select close,trade_date,unit_evidence,evidence_sha256 from nominal_monthly where symbol=%s and month=%s and source='kaggle_yfinance2022'",(symbol,date[:7]))
                proofs.append(review_fact(fact,cursor.fetchone(),selected))
            changed = []
            if write or rollback:
                cursor.execute('create temporary table before_nominal on commit drop as select * from v_nominal_monthly')
                for proof in proofs:
                    sha = digest(proof)
                    cursor.execute("""insert into nominal_monthly
                        (symbol,month,close,trade_date,source,source_url,fetched_at,evidence_sha256,
                         crosscheck_pairs,median_log_deviation,p90_log_deviation,unit_evidence)
                        values (%s,%s,%s,%s,'eastmoney_native',%s,now(),%s,%s,%s,%s,%s)""",
                        (proof['symbol'],proof['month'],proof['close'],proof['date'],proof['primary_url'],sha,
                         proof['return_pairs'],proof['median_log_deviation'],proof['p90_log_deviation'],
                         'Exact raw month-end close; independent daily/calendar and native-unit archive proof '+sha))
                    cursor.execute("""insert into sys_event(kind,source,subject,status,metrics,finished_at)
                        values ('validation','verified_closing_fact',%s,'pass',%s,now())""",
                        (proof['symbol']+':'+proof['date'],json.dumps({'proof':proof,'evidence_sha256':sha})))
                    cursor.execute('select source,close,trade_date,evidence_sha256 from v_nominal_monthly where symbol=%s and month=%s',(proof['symbol'],proof['month']))
                    src,p,d,h = cursor.fetchone()
                    if (src,float(p),str(d),h) != ('eastmoney_native',proof['close'],proof['date'],sha):
                        raise RuntimeError('Selected result differs; rollback')
                cursor.execute('''select symbol,month from (
                    (select * from before_nominal except select * from v_nominal_monthly)
                    union (select * from v_nominal_monthly except select * from before_nominal)
                    ) changes group by symbol,month order by symbol,month''')
                changed = cursor.fetchall()
                if changed != sorted((p['symbol'],p['month']) for p in proofs):
                    raise RuntimeError('Unexpected nominal view changes; rollback')
            for name,(query,sha) in snapshots.items():
                cursor.execute(query,(symbols,))
                if digest(cursor.fetchall()) != sha:
                    raise RuntimeError(name+' changed; rollback')
            result = {'mode':'write' if write else 'rollback' if rollback else 'review',
                      'proofs':proofs,'selected_keys_changed':changed,
                      'preserved_snapshot_sha256':{n:v[1] for n,v in snapshots.items()},
                      'return_prices_picks_locks_original_observations_changed':False}
        if write:
            connection.commit()
        else:
            connection.rollback()
        (BASE/'output'/('nominal_month_end_'+result['mode']+'.json')).write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'mode':result['mode'],'reviewed':len(proofs),'changed_keys':changed}))
    finally:
        connection.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--write',action='store_true')
    modes.add_argument('--rollback',action='store_true')
    args = parser.parse_args()
    run(args.write,args.rollback)
