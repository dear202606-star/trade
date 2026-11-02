"""Actual WDC + one-third SNDK holding path for calendar 2025.

Reuses the already reserved AV monthly response; no further AV calls. The
post-distribution February anchor has no intervening cash distribution. Later
parent cash dividends use its same-vintage adjusted-return ratios.
"""
import hashlib
import json
import math
import sys
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from collect_eastmoney_nominal import parse
from holding_consideration import distribution_value,POLICY

TERMS='https://www.sec.gov/Archives/edgar/data/106040/000010604025000038/wdc-20250627.htm'
CHILD_POLICY='https://investor.sandisk.com/ir-resources/investor-faqs'


def run():
    cache=BASE/'output/_holding_evidence'
    files={name:cache/(name+'.json') for name in
           ('WDC_eastmoney_2025','SNDK_eastmoney_2025','WDC_pre_event','SNDK_qfq_2025')}
    payloads={name:json.loads(path.read_text()) for name,path in files.items()}
    parent=parse('WDC',105,payloads['WDC_eastmoney_2025'])
    child=parse('SNDK',105,payloads['SNDK_eastmoney_2025'])
    child_adjusted=parse('SNDK',105,payloads['SNDK_qfq_2025'])
    expected={'2025-%02d'%m for m in range(2,13)}
    if set(child)!=expected or set(child_adjusted)!=expected:
        raise RuntimeError('Incomplete post-IPO child months')
    if any(child[m]!=child_adjusted[m] for m in expected):
        raise RuntimeError('Child adjustment factors require separate corporate-action review')
    daily=json.loads((BASE/'output/_nominal_daily/SNDK.json').read_text())
    independent={}
    for row in sorted(daily['rows'],key=lambda r:r['d']):
        if '2025-02-24'<=row['d']<='2025-12-31':independent[row['d'][:7]]=row
    if set(independent)!=expected or any(independent[m]['d']!=child[m]['date'] or
        abs(float(independent[m]['c'])-child[m]['close'])>.005 for m in expected):
        raise RuntimeError('Independent child monthly closes disagree')
    av_path=BASE/'output/_av_monthly_native/WDC.json'
    av_record=json.loads(av_path.read_text());av=av_record['payload']
    if av.get('Meta Data',{}).get('2. Symbol')!='WDC':
        raise RuntimeError('Parent primary monthly identity differs')
    series=av.get('Monthly Adjusted Time Series') or {}
    monthly={day[:7]:(day,row) for day,row in series.items() if '2024-12'<=day[:7]<='2025-12'}
    if set(monthly)!={'2024-12'}|{'2025-%02d'%m for m in range(1,13)}:
        raise RuntimeError('Parent monthly history is incomplete')
    for m,(date,row) in monthly.items():
        if date!=parent[m]['date'] or abs(float(row['4. close'])-parent[m]['close'])>.005:
            raise RuntimeError('Parent native units/date disagreement')
    if any(float(monthly[m][1]['7. dividend amount'])!=0 for m in ('2024-12','2025-01','2025-02')):
        raise RuntimeError('Pre-event or anchor-gap cash distributions require explicit treatment')
    before=payloads['WDC_pre_event'].get('data') or {}
    if before.get('code')!='WDC' or before.get('market')!=105 or len(before.get('klines') or [])!=1:
        raise RuntimeError('Missing pre-event native quote')
    pre=before['klines'][0].split(',')
    if pre[0]!='2025-02-21':raise RuntimeError('Pre-event quote date differs')
    native_before=float(pre[2]);native_base=parent['2024-12']['close']
    adj=lambda month:float(monthly[month][1]['5. adjusted close'])
    pre_growth=(adj('2025-01')/adj('2024-12'))*(native_before/parent['2025-01']['close'])
    if not all(math.isfinite(v) and v>0 for v in (pre_growth,native_before,native_base)):
        raise RuntimeError('Invalid pre-event values')
    if abs((adj('2025-01')/adj('2024-12'))/(parent['2025-01']['close']/native_base)-1)>1e-5:
        raise RuntimeError('Unexpected pre-event share/cash adjustment')
    values={'2025-01':adj('2025-01')/adj('2024-12')}
    for month in sorted(expected):
        values[month]=distribution_value(pre_growth,native_before,0,[
            {'shares':1,'native_entry':parent['2025-02']['close'],'growth':adj(month)/adj('2025-02')},
            {'shares':1/3,'native_entry':child['2025-02']['close'],'growth':child[month]['close']/child['2025-02']['close']}])
    result={'producer':'wdc_distribution','symbol':'WDC','hold_year':2025,'event_date':'2025-02-24',
        'holding_event_policy':POLICY,'issuer_terms':TERMS,'child_dividend_policy_source':CHILD_POLICY,
        'consideration':{'cash_per_old_share':0,'successor_shares':{'WDC':1,'SNDK':1/3}},
        'post_event_valuation_anchor':'2025-02-28','pre_event_date':'2025-02-21',
        'native_before':native_before,'native_base':native_base,'pre_event_growth':pre_growth,
        'parent_primary_monthly_response_sha256':hashlib.sha256(json.dumps(av,sort_keys=True).encode()).hexdigest(),
        'parent_primary_response_received_at':av_record['received_at'],
        'raw_evidence_sha256':{name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in files.items()},
        'child_independent_daily_sha256':hashlib.sha256(json.dumps(daily['rows'],sort_keys=True).encode()).hexdigest(),
        'child_independent_months':len(expected),'child_adjusted_equals_native_holding_months':True,
        'pre_2025_reused_SNDK_history_excluded':True,'retained_monthly_values':values,
        'retained_2025_return':values['2025-12']-1,
        'parent_provider_adjusted_2025_return':adj('2025-12')/adj('2024-12')-1,
        'quoted_prices_locks_and_baselines_changed':False}
    (BASE/'output/wdc_distribution_review.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2));return result


if __name__=='__main__':run()
