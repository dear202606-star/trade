"""Audit/apply only BK -> BNY membership continuity; never transfer prices."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import db_select
import db_ref
import security_membership


def run(write=False):
    baseline=(BASE/'output/baseline_b.json').read_bytes()
    alias=next(r for r in json.loads((BASE/'static/verified_ticker_renames.json').read_text())['aliases']
               if r['old']=='BK' and r['current']=='BNY')
    if alias['effective_date']!='2026-05-21':raise RuntimeError('Unexpected reviewed effective date')
    before=security_membership.load_aliases()
    pending=[r for r in before if r['old']!='BK' and r['current']!='BNY']+[alias]
    if len(pending)!=len(before)+1:raise RuntimeError('Rename already exists or conflicts; inspect before reapplying')
    frame=pd.DataFrame([{'ticker':s,'start_date':a,'end_date':b}
                       for s,periods in db_ref.load_members().items() for a,b in periods])
    changes={}
    for year in range(2010,2027):
        with patch.object(security_membership,'load_aliases',return_value=before):
            old=security_membership.members_full_year(frame,year)
        with patch.object(security_membership,'load_aliases',return_value=pending):
            new=security_membership.members_full_year(frame,year)
        if old!=new:changes[str(year)]={'added':sorted(new-old),'removed':sorted(old-new)}
    if changes!={'2026':{'added':['BNY'],'removed':[]}}:
        raise RuntimeError('Unexpected membership effect: '+str(changes))
    digest=hashlib.sha256(json.dumps(alias,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if write:
        connection=db_select.db()
        try:
            with connection,connection.cursor() as cursor:
                cursor.execute('''insert into verified_ticker_renames
                 (old_symbol,current_symbol,effective_date,evidence_url,evidence_sha256,verified)
                 values (%s,%s,%s,%s,%s,true)''',
                 ('BK','BNY',alias['effective_date'],alias['evidence'],digest))
                cursor.execute('select evidence_sha256,verified from verified_ticker_renames where old_symbol=%s and current_symbol=%s',('BK','BNY'))
                if cursor.fetchall()!=[(digest,True)]:raise RuntimeError('Inserted reference differs')
        finally:connection.close()
        security_membership._ALIASES=None
        if 'BNY' not in security_membership.members_full_year(frame,2026):raise RuntimeError('Live runtime did not use rename')
    if baseline!=(BASE/'output/baseline_b.json').read_bytes():raise RuntimeError('Published baseline changed')
    result={'write':write,'alias':alias,'evidence_record_sha256':digest,
            'other_primary_sources':[
              'https://www.bny.com/corporate/global/en/about-us/newsroom/press-release/bny-announces-planned-change-of-stock-ticker-symbol-to-bny-130465.html',
              'https://infomemo.theocc.com/infomemos?number=58945'],
            'membership_year_changes':changes,'selection_years_2010_through_2026_unchanged':True,
            'first_affected_selection_year':2027,'prices_source_picks_and_published_outputs_changed':False,
            'baseline_b_sha256':hashlib.sha256(baseline).hexdigest()}
    (BASE/'output/bny_membership_verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--write',action='store_true')
    run(parser.parse_args().write)
