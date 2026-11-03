"""Collect the live database's missing windows, preserving per-month receipts."""
import json
import hashlib
import shutil
import sys
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from research.audit_liquidity_inventory import run as inventory
from collect_liquidity_windows import run as collect


def run():
    receipt=BASE/'output/liquidity_window_ingest.json'
    seed=BASE/'output/liquidity_window_seed_ingest.json'
    if receipt.exists() and not seed.exists():shutil.copyfile(receipt,seed)
    plan=inventory()
    if plan.get('status_counts',{}).get('accepted_version_review_required',0):
        raise RuntimeError('Accepted historical calculator version requires review; automatic recollection forbidden')
    results=[]
    progress=BASE/'output/liquidity_plan_progress.json'
    progress.write_text('[]\n')
    for month,symbols in sorted(plan['targets_by_month'].items()):
        print('Collecting %d missing windows for %s'%(len(symbols),month),flush=True)
        collect(symbols,[month],write=True)
        record=json.loads(receipt.read_text())
        if any(r.get('rank_month',month)!=month for r in record['results']):
            raise RuntimeError('Collector receipt belongs to another month')
        seen=[r['symbol'] for r in record['results']]
        if len(seen)!=len(set(seen)) or set(seen)-set(symbols):
            raise RuntimeError('Collector receipt has duplicate/unrequested symbols')
        if any(r['status'] not in ('stored','review_required','network_failure','no_selected_month')
               for r in record['results']):
            raise RuntimeError('Collector receipt has an unknown status')
        target=BASE/'output'/('liquidity_ingest_'+month+'.json')
        if target.exists():
            previous=target.read_bytes()
            archive=target.with_name(target.stem+'_'+hashlib.sha256(previous).hexdigest()[:16]+'.json')
            if archive.exists() and archive.read_bytes()!=previous:
                raise RuntimeError('Receipt archive content conflict')
            archive.write_bytes(previous)
        target.write_text(json.dumps(record,indent=2)+'\n')
        failed=sum(r['status']=='network_failure' for r in record['results'])
        results.append({'month':month,'requested':len(symbols),
                        'attempted':len(seen),'not_attempted':len(symbols)-len(seen),
                        'network_failures':failed,
                        'stored':sum(r['status']=='stored' for r in record['results']),
                        'review_required':sum(r['status']=='review_required' for r in record['results'])})
        progress.write_text(json.dumps(results,indent=2)+'\n')
        if failed:
            raise RuntimeError('Network/source failure: stopped without beginning another month')
        if set(seen)!=set(symbols):
            raise RuntimeError('Collector omitted requested symbols')


if __name__=='__main__':run()
