"""Keep portable audit receipts; bulk provider observations stay in the DB/cache."""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]


def run():
    for name in ('nominal_price_review','wiki_nominal_review','yfinance_actions2022_review'):
        report = json.loads((BASE/'output'/(name+'.json')).read_text())
        summary = {k:v for k,v in report.items() if k!='results'}
        summary['results'] = {symbol:{k:v for k,v in row.items() if k!='months'}
                              for symbol,row in report['results'].items()}
        summary['price_units_proved_by_this_return_check_alone'] = False
        (BASE/'output'/(name+'_summary.json')).write_text(json.dumps(summary,indent=2)+'\n')
    archive = json.loads((BASE/'output/wiki_monthly_archive.json').read_text())
    receipt = {k:v for k,v in archive.items() if k not in ('monthly','events')}
    receipt['corporate_action_rows'] = sum(len(v) for v in archive['events'].values())
    receipt['raw_unit_evidence'] = 'https://investor.apple.com/investor-relations/faq/default.aspx'
    (BASE/'output/wiki_archive_receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')


if __name__=='__main__':
    run()
