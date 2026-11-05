"""Review SCANA evidence; accept one issuer price, never its incomplete returns."""
import argparse
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import db_sources
import db_prices
from historical_lineage import digest

FACT={'symbol':'SCG','date':'2013-12-31','close':46.93,'issuer':'SCANA Corporation',
      'cik':'754737','url':'https://www.sec.gov/Archives/edgar/data/754737/000119312514112009/d640625ddef14a.htm',
      'location':'Outstanding Equity Awards at Fiscal Year-End, footnote (2), page 40'}
DIVIDENDS='https://www.sec.gov/Archives/edgar/data/754737/000075473718000115/a12312017-10k.htm'
ARCHIVE_SHA='36c667bbecf42c43e5b9e8e4e5d9a1268522705bc40a4bac9671d62c1a20cbae'


def review(archive,daily,benchmark,comparison):
    if archive['archive_sha256']!=ARCHIVE_SHA:raise ValueError('Unreviewed WIKI archive')
    endpoint='https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/x/US_MinKService.getDailyK?symbol=SCG'
    if daily['endpoint']!=endpoint:raise ValueError('Independent issuer route differs')
    months=archive['monthly']['SCG'];point=months.get('2013-12',{})
    if point.get('date')!=FACT['date'] or point.get('close')!=FACT['close']:
        raise ValueError('WIKI differs from the exact issuer closing fact')
    observations={r['d']:r for r in daily['rows']}
    if (len(observations)!=len(daily['rows']) or FACT['date'] not in observations
            or float(observations[FACT['date']]['c'])!=FACT['close']):
        raise ValueError('Independent daily differs from the exact issuer closing fact')
    calendar={}
    for row in sorted(benchmark['rows'],key=lambda r:r['d']):calendar[row['d'][:7]]=row['d']
    if calendar.get('2013-12')!=FACT['date']:raise ValueError('Issuer date is not the benchmark month end')
    full_months={m:r for m,r in months.items() if m<'2018-03'}
    conflicts=[m for m,r in full_months.items() if calendar.get(m)!=r['date']]
    if conflicts:raise ValueError('Unreviewed monthly-date conflicts: '+','.join(conflicts))
    independent={}
    for row in sorted(daily['rows'],key=lambda r:r['d']):
        if '2008-01-01'<=row['d']<'2015-01-01':independent[row['d'][:7]]=row
    common_native=sorted(set(independent)&set(full_months))
    if len(common_native)<24 or any(independent[m]['d']!=months[m]['date'] or
        abs(float(independent[m]['c'])-months[m]['close'])>.005 for m in common_native):
        raise ValueError('Original SCANA native-price comparison failed')
    # The comparison archive ended on 2017-11-10; its November row is partial.
    common=sorted(m for m in set(comparison)&set(months) if m<'2017-11')
    deviations=[];details=[]
    for a,b in zip(common,common[1:]):
        if (int(b[:4])-int(a[:4]))*12+int(b[5:])-int(a[5:])!=1:continue
        values=[comparison[a],comparison[b],months[a]['adj_close'],months[b]['adj_close']]
        if any(type(v) not in (float,int) or not math.isfinite(v) or v<=0 for v in values):
            raise ValueError('Invalid comparison return price')
        gap=abs(math.log((values[1]/values[0])/(values[3]/values[2])))
        deviations.append(gap)
        if gap>.001:details.append({'from':a,'to':b,'log_deviation':gap})
    if len(deviations)<24:raise ValueError('Insufficient original-security overlap')
    median=statistics.median(deviations);p90=sorted(deviations)[int(.9*(len(deviations)-1))]
    actions=[r for r in archive['events']['SCG'] if r['date'].startswith('2017') and r['dividend']]
    return {'issuer_fact':FACT,'symbol':'SCG','date':FACT['date'],'nominal_source':'issuer_native',
        'independent_native_months':len(common_native),'raw_month_end_dates_verified':len(full_months),
        'partial_archive_months':[m for m in months if m not in full_months],
        'return_pairs':len(deviations),'median_log_deviation':median,'p90_log_deviation':p90,
        'coarse_identity_threshold_pass':median<=.005 and p90<=.02,
        'individual_return_disagreements':details,
        'dividend_review':{'issuer_url':DIVIDENDS,'issuer_2017_quarterly_rate':.6125,
            'issuer_2017_declared_quarters':4,'archive_2017_actions':actions,
            'archive_2017_action_count':len(actions)},
        'return_history_accepted':False,'quarantine_release_accepted':False,
        'scope':'Only original SCANA common equity exact 2013-12-31 native close; no return history or source promotion'}


def preserved(cursor):
    result={'selected':list(db_prices.selected_stamp(cursor))}
    for table,order in [('prices_monthly','month,source'),('series_pick','symbol'),
                        ('data_lock','id'),('members','start_date'),('series_quarantine','symbol')]:
        cursor.execute('select * from '+table+' where symbol=%s order by '+order,('SCG',))
        result[table]=hashlib.sha256(json.dumps(cursor.fetchall(),default=str).encode()).hexdigest()
    result['baseline_b']=hashlib.sha256((BASE/'output/baseline_b.json').read_bytes()).hexdigest()
    return result


def run(write=False):
    connection=db_sources.db()
    try:
        connection.set_session(isolation_level='REPEATABLE READ')
        with connection.cursor() as cursor:
            cursor.execute("set local lock_timeout='5s'")
            cursor.execute("select pg_advisory_xact_lock(hashtext('nominal_fact:SCG:2013-12')::bigint)")
            cursor.execute("select metrics from sys_event where source='verified_closing_fact' and subject='SCG:2013-12-31' and status='pass'")
            existing=cursor.fetchall()
            if existing:
                if len(existing)!=1:raise RuntimeError('Conflicting SCANA acceptance receipts')
                metrics=existing[0][0];proof=metrics['proof']
                cursor.execute("select close,trade_date,evidence_sha256 from nominal_monthly where symbol='SCG' and month='2013-12' and source='issuer_native'")
                row=cursor.fetchone()
                if (digest(proof)!=metrics['evidence_sha256'] or proof['issuer_fact']!=FACT
                    or not row or (float(row[0]),str(row[1]),row[2])!=(46.93,'2013-12-31',metrics['evidence_sha256'])):
                    raise RuntimeError('Stored SCANA fact or evidence changed')
                connection.rollback();print('SCANA exact fact already accepted; no cache processing or writes');return
            cursor.execute("select source,basis,frozen from series_pick where symbol='SCG' for share")
            if cursor.fetchone()!=('sina_repaired','price_only',False):raise RuntimeError('SCANA selected source changed')
            cursor.execute("select reason from series_quarantine where symbol='SCG' for share")
            if cursor.fetchone() is None:raise RuntimeError('SCANA quarantine must remain')
            before=preserved(cursor)
            paths={name:BASE/'output'/path for name,path in (
                ('wiki','wiki_monthly_archive.json'),('daily','_nominal_daily/SCG.json'),
                ('benchmark','_nominal_daily/SPY.json'))}
            raw={name:path.read_bytes() for name,path in paths.items()}
            cursor.execute("select month,adj_close from prices_monthly where symbol='SCG' and source='kaggle_cc0' order by month")
            comparison={m:float(p) for m,p in cursor.fetchall()}
            proof=review(*(json.loads(raw[n]) for n in ('wiki','daily','benchmark')),comparison)
            if not proof['coarse_identity_threshold_pass']:raise RuntimeError('Independent identity comparison failed')
            proof['cache_sha256']={n:hashlib.sha256(v).hexdigest() for n,v in raw.items()}
            proof['comparison_rows_sha256']=digest(comparison)
            sha=digest(proof)
            if write:
                prior=json.loads((BASE/'output/scg_original_fact_rollback.json').read_text())
                if prior['proof']!=proof or prior['preserved_sha256']!=before:
                    raise RuntimeError('SCANA evidence differs from rollback review')
            cursor.execute("""insert into nominal_monthly(symbol,month,close,trade_date,source,source_url,
                fetched_at,evidence_sha256,crosscheck_pairs,median_log_deviation,p90_log_deviation,unit_evidence)
                values ('SCG','2013-12',46.93,'2013-12-31','issuer_native',%s,now(),%s,%s,%s,%s,%s)""",
                (FACT['url'],sha,proof['return_pairs'],proof['median_log_deviation'],proof['p90_log_deviation'],
                 'Original SCANA common equity exact issuer closing fact; return history remains quarantined; proof '+sha))
            cursor.execute("""insert into sys_event(kind,source,subject,status,metrics,finished_at)
                values ('validation','verified_closing_fact','SCG:2013-12-31','pass',%s,now())""",
                (json.dumps({'proof':proof,'evidence_sha256':sha}),))
            blocked=[]
            for name,sql in [('price_update',"update nominal_monthly set close=close+1 where symbol='SCG' and source='issuer_native'"),
                ('price_delete',"delete from nominal_monthly where symbol='SCG' and source='issuer_native'"),
                ('proof_update',"update sys_event set metrics=metrics||'{\"probe\":true}'::jsonb where source='verified_closing_fact' and subject='SCG:2013-12-31'"),
                ('proof_delete',"delete from sys_event where source='verified_closing_fact' and subject='SCG:2013-12-31'")]:
                cursor.execute('savepoint scg_probe')
                try:cursor.execute(sql)
                except db_sources.psycopg2.Error as error:
                    if not any(s in str(error) for s in ('immutable','append-only')):raise
                    blocked.append(name)
                else:raise RuntimeError('Accepted SCANA mutation was not blocked: '+name)
                finally:cursor.execute('rollback to savepoint scg_probe')
            if preserved(cursor)!=before:raise RuntimeError('SCANA source, history, quarantine, locks or baseline changed')
            cursor.execute("select count(*) from v_nominal_monthly where symbol='SCG'")
            if cursor.fetchone()[0]!=0:raise RuntimeError('Unreviewed SCANA history became eligible')
        if write:connection.commit()
        else:connection.rollback()
        result={'write':write,'proof':proof,'preserved_sha256':before,'blocked_mutations':blocked,
                'evidence_sha256':sha,'quarantine_preserved':True,'full_history_accepted':False}
        name='scg_original_fact_applied.json' if write else 'scg_original_fact_rollback.json'
        (BASE/'output'/name).write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'write':write,'accepted_exact_points':1,'quarantine_preserved':True,
                          'return_disagreements':len(proof['individual_return_disagreements']),'blocked_mutations':len(blocked)}))
    finally:connection.rollback();connection.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--write',action='store_true')
    run(parser.parse_args().write)
