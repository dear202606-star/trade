"""Read-only CVC cash-consideration evidence; no price or baseline updates."""
import hashlib
import json
import sys
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from holding_consideration import distribution_value, POLICY

TERMS='https://www.sec.gov/Archives/edgar/data/784681/000094787116001286/ss1506472_8k.htm'


def run():
    path=BASE/'output/wiki_monthly_archive.json'
    archive=json.loads(path.read_text())
    receipt=json.loads((BASE/'output/wiki_archive_receipt.json').read_text())
    if archive['archive_sha256']!=receipt['archive_sha256']:
        raise RuntimeError('WIKI archive receipt differs')
    bars=archive['monthly']['CVC']
    before,base=bars['2016-06'],bars['2015-12']
    if before['date']!='2016-06-20' or before['close']!=34.87 or base['date']!='2015-12-31':
        raise RuntimeError('Unexpected pre-merger native-price dates/units')
    cash_value=distribution_value(before['adj_close']/base['adj_close'],before['close'],34.90,[])
    values={'2016-%02d'%month:(bars['2016-%02d'%month]['adj_close']/base['adj_close']
                              if month<6 else cash_value) for month in range(1,13)}
    result={'scope':'cash-only consideration evidence; no quoted-price or baseline writes',
        'producer':'cvc_cash_merger','symbol':'CVC','hold_year':2016,'event_date':'2016-06-21',
        'holding_event_policy':POLICY,'issuer_terms':TERMS,'effective_date':'2016-06-21',
        'cash_per_old_share':34.90,'successor_shares':[],
        'archive_sha256':archive['archive_sha256'],
        'monthly_evidence_sha256':hashlib.sha256(json.dumps(bars,sort_keys=True).encode()).hexdigest(),
        'base_date':base['date'],'native_base':base['close'],
        'last_trading_date':before['date'],'native_before':before['close'],
        'retained_monthly_values':values,'return_2016':cash_value-1,
        'last_quote_carry_counterfactual_return':before['adj_close']/base['adj_close']-1,
        'month_of_merger_uses_cash_even_if_a_partial_month_quote_exists':True,
        'cash_held_without_interest_as_in_existing_cash_convention':True,
        'prices_locks_and_baselines_changed':False}
    (BASE/'output/cvc_cash_merger_review.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    return result


if __name__=='__main__':
    run()
