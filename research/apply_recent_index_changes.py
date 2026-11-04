"""Apply reviewed S&P 500 changes only; default is an actual transaction rollback."""
import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import db_sources

SOURCE='reviewed_index_change'
SEPT='https://press.spglobal.com/2026-09-04-Bloom-Energy,-Illumina,-and-Everpure-Set-to-Join-S-P-500-Others-to-Join-S-P-100,-S-P-MidCap-400,-and-S-P-SmallCap-600'
OCT='https://press.spglobal.com/2026-10-01-Vylor-Added-to-the-S-P-500-Twilio-Set-to-Join-S-P-500-Others-to-Join-S-P-MidCap-400-and-S-P-SmallCap-600'
NOV='https://press.spglobal.com/2026-10-30-Okta-Set-to-Join-S-P-500-Others-to-Join-S-P-MidCap-400-and-S-P-SmallCap-600'
SKYD='https://ir.paramount.com/news-releases/news-release-details/paramount-completes-acquisition-warner-bros-discovery-creating'
CHANGES=[
    {'symbol':s,'action':a,'effective_on':day,'sector':sector,
     'sector_from':sector_from or day,'url':url}
    for s,a,day,sector,sector_from,url in [
        ('BE','add','2026-09-21','Industrials',None,SEPT),
        ('P','add','2026-09-21','Information Technology',None,SEPT),
        ('ILMN','add','2026-09-21','Health Care',None,SEPT),
        ('TAP','remove','2026-09-21',None,None,SEPT),
        ('TTD','remove','2026-09-21',None,None,SEPT),
        ('BLDR','remove','2026-09-21',None,None,SEPT),
        ('VYLR','add','2026-10-01','Consumer Staples','2026-10-06',OCT),
        ('CTVA','remove','2026-10-06',None,None,OCT),
        ('TWLO','add','2026-10-06','Information Technology',None,OCT),
        ('WBD','remove','2026-10-06',None,None,OCT),
        ('PSKY','remove','2026-10-06',None,None,SKYD),
        ('SKYD','add','2026-10-06','Communication Services',None,SKYD),
        ('OKTA','add','2026-11-04','Information Technology',None,NOV),
        ('KVUE','remove','2026-11-04',None,None,NOV),
    ]]
IDENTITY={'symbol':'P','issuer':'Everpure, Inc.','cik':'1474432',
    'prior_ticker':'PSTG','rename_effective':'2026-04-17',
    'rename_url':'https://www.everpuredata.com/company/newsroom/press-releases/everpure-to-change-ticker-symbol.html',
    'rename_confirmation_url':'https://www.sec.gov/Archives/edgar/data/1474432/000147443226000061/pstg-20260503.htm',
    'prior_reused_issuer':'Pandora Media, Inc.',
    'prior_reuse_url':'https://investor.siriusxm.com/sec-filings/all-sec-filings/content/0001193125-19-020898/d674763dex991.htm',
    'price_history_accepted':False}
P_REASON='P now denotes Everpure (CIK 1474432), formerly PSTG, effective 2026-04-17; older P denoted Pandora. Index admission is not approval of ticker-labelled price history. Verify issuer and same-equity price lineage before selection.'
NAMES={'BE':'Bloom Energy Corporation','P':'Everpure, Inc.','ILMN':'Illumina, Inc.',
       'VYLR':'Vylor Inc.','TWLO':'Twilio Inc.','SKYD':'Skydance Corporation','OKTA':'Okta, Inc.'}
TERMS=[
    {'symbol':'KVUE','event_date':'2026-11-02','ceased_trading_on':'2026-11-02',
     'cash_usd_per_share':'3.50','successor_symbol':'KMB','successor_units_per_share':'0.14625',
     'url':'https://www.investor.kimberly-clark.com/news-releases/news-release-details/kimberly-clark-completes-acquisition-kenvue-creating-preeminent'},
    {'symbol':'WBD','event_date':'2026-10-06','ceased_trading_on':'2026-10-06',
     'cash_usd_per_share':'31.01666668','successor_symbol':None,'successor_units_per_share':'0','url':SKYD},
    {'symbol':'PSKY','event_date':'2026-10-06','old_ticker_ceased_on':'2026-10-06',
     'successor_symbol':'SKYD','same_equity_ticker_change':True,'url':SKYD,
     'additional_asset':{'kind':'warrant','record_date':'2026-10-05','issued_on':'2026-10-13',
        'units_per_common_share':'1','trading_symbol':'SKYD WS','exercise_price_usd':'12.00',
        'url':'https://www.sec.gov/Archives/edgar/data/2041610/000110465926115294/tm2626659d12_8k.htm'}},
]


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()


def subject(change):
    return 'S&P500:'+change['effective_on']+':'+change['action']+':'+change['symbol']


def clipped(rows,through):
    """Compare historical membership semantics without open-ended future dates."""
    return [(s,str(a),min(str(b),through),source) for s,a,b,source in rows if str(a)<=through]


def apply_change(cursor,change):
    s=change['symbol'];day=dt.date.fromisoformat(change['effective_on'])
    cursor.execute('select start_date,end_date from members where symbol=%s and end_date>=%s order by start_date',(s,day))
    existing=cursor.fetchall()
    if change['action']=='add':
        if existing:raise RuntimeError('Admission overlaps existing interval: '+s)
        cursor.execute("insert into members(symbol,start_date,end_date,source) values(%s,%s,'2099-12-31',%s)",(s,day,SOURCE))
    elif change['action']=='remove':
        if len(existing)!=1 or existing[0][0]>=day or str(existing[0][1])!='2099-12-31':
            raise RuntimeError('Removal does not match exactly one open interval: '+s)
        cursor.execute('update members set end_date=%s where symbol=%s and start_date=%s',
                       (day-dt.timedelta(days=1),s,existing[0][0]))
        if cursor.rowcount!=1:raise RuntimeError('Concurrent membership change')
    else:raise ValueError('Unknown index action')


def run(write=False):
    symbols=sorted(c['symbol'] for c in CHANGES)
    subjects=[subject(c) for c in CHANGES]
    connection=db_sources.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute("set local statement_timeout='45s'")
            cursor.execute("set local lock_timeout='5s'")
            cursor.execute('lock table symbols,members,sectors,series_quarantine,prices_monthly,nominal_monthly,series_pick,data_lock in share row exclusive mode')
            cursor.execute((BASE/'sql/113_reviewed_index_boundaries.sql').read_text())
            cursor.execute("select subject,metrics from sys_event where kind='validation' and source=%s and status='pass' and subject=any(%s)",(SOURCE,subjects))
            receipts=cursor.fetchall()
            cursor.execute("select subject,metrics from sys_event where kind='validation' and source='verified_corporate_terms' and status='pass' and subject=any(%s)",
                           ([t['symbol']+':'+t['event_date'] for t in TERMS],))
            term_receipts=cursor.fetchall()
            if receipts:
                if len(receipts)!=len(CHANGES) or set(s for s,m in receipts)!=set(subjects):
                    raise RuntimeError('Partial prior review; no automatic repair')
                for change in CHANGES:
                    payload=next(m for s,m in receipts if s==subject(change))
                    if payload.get('evidence_sha256')!=digest(payload.get('proof')) or payload['proof'].get('change')!=change:
                        raise RuntimeError('Existing review version conflicts')
                    if change['symbol']=='P' and payload['proof'].get('identity_review')!=IDENTITY:
                        raise RuntimeError('Existing issuer review version conflicts')
                    if change['action']=='add':
                        cursor.execute('select end_date from members where symbol=%s and start_date=%s',(change['symbol'],change['effective_on']))
                        if cursor.fetchone()!=(dt.date(2099,12,31),):raise RuntimeError('Admission is no longer effective')
                    else:
                        cursor.execute('select max(end_date) from members where symbol=%s',(change['symbol'],))
                        if cursor.fetchone()[0]!=dt.date.fromisoformat(change['effective_on'])-dt.timedelta(days=1):raise RuntimeError('Removal boundary changed')
                cursor.execute("select reason from series_quarantine where symbol='P'")
                if cursor.fetchone()!=(P_REASON,):raise RuntimeError('Unresolved P identity guard changed')
                if len(term_receipts)!=len(TERMS):raise RuntimeError('Corporate terms evidence incomplete')
                for term in TERMS:
                    payload=next(m for s,m in term_receipts if s==term['symbol']+':'+term['event_date'])
                    if payload.get('evidence_sha256')!=digest(payload.get('proof')) or payload['proof'].get('terms')!=term:
                        raise RuntimeError('Corporate terms evidence differs')
                connection.rollback();print('Reviewed index changes already installed; no reprocessing');return
            if term_receipts:raise RuntimeError('Partial prior corporate review; no automatic overwrite')
            cursor.execute('select symbol,start_date,end_date,source from members order by symbol,start_date')
            before=cursor.fetchall()
            cursor.execute('select * from symbols order by symbol')
            masters=cursor.fetchall();master_symbols={r[0] for r in masters}
            new_symbols=sorted(set(NAMES)-master_symbols)
            preserved={}
            for table,order in [('sectors','symbol'),('prices_monthly','symbol,month,source'),
                                ('nominal_monthly','symbol,month,source'),('series_pick','symbol'),('data_lock','id')]:
                where='' if table=='sectors' else ' where symbol=any(%s)'
                cursor.execute('select * from '+table+where+' order by '+order,None if not where else (symbols,))
                rows=cursor.fetchall();preserved[table]={'count':len(rows),'sha256':digest(rows)}
            cursor.execute("select count(*) from series_selected where symbol='P'")
            if cursor.fetchone()[0]:raise RuntimeError('Existing P selection requires separate issuer-impact review')
            cursor.execute("select symbol from locked_symbols where symbol='P' union select symbol from series_pick where symbol='P' and frozen")
            if cursor.fetchall():raise RuntimeError('Frozen P input requires explicit review')
            cursor.execute("select id from data_lock where symbol='P' and status<>'voided'")
            if cursor.fetchall():raise RuntimeError('Accepted P history requires explicit review')
            cursor.execute("select subject from sys_event where kind='validation' and status='pass' and source in ('verified_closing_fact','fund_native_closing_fact') and split_part(subject,':',1)='P'")
            if cursor.fetchall():raise RuntimeError('Accepted P nominal fact requires explicit review')
            cursor.execute("select reason from series_quarantine where symbol='P'")
            if cursor.fetchone():raise RuntimeError('Existing P review differs')
            baseline=hashlib.sha256((BASE/'output/baseline_b.json').read_bytes()).hexdigest()
            for symbol in new_symbols:
                cursor.execute('insert into symbols(symbol,name,note) values(%s,%s,%s)',
                    (symbol,NAMES[symbol],'Reviewed current index identity only; price history not accepted'))
            for change in CHANGES:apply_change(cursor,change)
            cursor.execute('insert into series_quarantine(symbol,reason) values(%s,%s)',('P',P_REASON))
            cursor.execute('select symbol,start_date,end_date,source from members order by symbol,start_date')
            after=cursor.fetchall()
            if clipped(before,'2026-09-20')!=clipped(after,'2026-09-20'):
                raise RuntimeError('Pre-change membership changed')
            if [r for r in before if r[0] not in symbols]!=[r for r in after if r[0] not in symbols]:
                raise RuntimeError('Unrelated membership changed')
            for table,order in [('sectors','symbol'),('prices_monthly','symbol,month,source'),
                                ('nominal_monthly','symbol,month,source'),('series_pick','symbol'),('data_lock','id')]:
                where='' if table=='sectors' else ' where symbol=any(%s)'
                cursor.execute('select * from '+table+where+' order by '+order,None if not where else (symbols,))
                rows=cursor.fetchall()
                if preserved[table]!={'count':len(rows),'sha256':digest(rows)}:raise RuntimeError('Preserved inputs changed: '+table)
            if hashlib.sha256((BASE/'output/baseline_b.json').read_bytes()).hexdigest()!=baseline:raise RuntimeError('Baseline changed')
            cursor.execute('select * from symbols where not(symbol=any(%s)) order by symbol',(new_symbols,))
            if cursor.fetchall()!=masters:raise RuntimeError('Existing security master changed')
            deltas=[]
            for day in ['2026-09-20','2026-09-21','2026-10-01','2026-10-05','2026-10-06','2026-11-03','2026-11-04']:
                prior={s for s,a,b,_ in before if str(a)<=day<=str(b)}
                actual={s for s,a,b,_ in after if str(a)<=day<=str(b)}
                additions={c['symbol'] for c in CHANGES if c['action']=='add' and c['effective_on']<=day}
                removals={c['symbol'] for c in CHANGES if c['action']=='remove' and c['effective_on']<=day}
                if actual!=(prior|additions)-removals:raise RuntimeError('Unexpected effective-day pool')
                deltas.append({'date':day,'before_count':len(prior),'after_count':len(actual),'added':sorted(actual-prior),'removed':sorted(prior-actual)})
            result={'write':write,'changes':CHANGES,'corporate_terms':TERMS,'new_symbol_masters':new_symbols,
                    'existing_symbol_master_sha256':digest(masters),'P_identity_pending':IDENTITY,'membership_before_sha256':digest(before),'membership_after_sha256':digest(after),
                    'pre_2026_09_21_membership_unchanged':True,'unrelated_membership_unchanged':True,
                    'preserved_inputs':preserved,'baseline_b_sha256':baseline,'effective_day_deltas':deltas,
                    'historical_price_writes':0,'global_sector_writes':0,'financial_acceptance':False}
            if write:
                reviewed=json.loads((BASE/'output/recent_index_changes_rollback.json').read_text())
                if {k:v for k,v in reviewed.items() if k!='write'}!={k:v for k,v in result.items() if k!='write'}:
                    raise RuntimeError('Rollback evidence differs from current state')
            for change in CHANGES:
                proof={'change':change,'membership_after_sha256':result['membership_after_sha256'],
                       'index':'S&P 500','price_history_accepted':False}
                if change['action']=='add':proof['interval_start']=change['effective_on']
                else:
                    proof['interval_start']=next(str(a) for s,a,b,source in before
                        if s==change['symbol'] and str(b)=='2099-12-31')
                if change['symbol']=='P':proof['identity_review']=IDENTITY
                cursor.execute("""insert into sys_event(kind,source,subject,status,metrics,finished_at)
                    values('validation',%s,%s,'pass',%s,now())""",
                    (SOURCE,subject(change),json.dumps({'proof':proof,'evidence_sha256':digest(proof)})))
            for term in TERMS:
                proof={'terms':term,'scope':'Verified corporate terms only; no replacement market quote or accepted return path',
                       'holding_valuation_accepted':False}
                cursor.execute("""insert into sys_event(kind,source,subject,status,metrics,finished_at)
                    values('validation','verified_corporate_terms',%s,'pass',%s,now())""",
                    (term['symbol']+':'+term['event_date'],json.dumps({'proof':proof,'evidence_sha256':digest(proof)})))
            cursor.execute('set constraints reviewed_member_boundary,reviewed_member_proof immediate')
        if write:connection.commit()
        else:connection.rollback()
        name='recent_index_changes_applied.json' if write else 'recent_index_changes_rollback.json'
        (BASE/'output'/name).write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'write':write,'changes':len(CHANGES),'effective_day_deltas':deltas,'historical_price_writes':0}))
    finally:connection.rollback();connection.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--write',action='store_true')
    run(parser.parse_args().write)
