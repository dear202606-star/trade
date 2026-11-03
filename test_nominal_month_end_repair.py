import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import import_wiki_nominal_prices as wiki
from research import repair_nominal_month_ends as repair


class MonthEndRepairTests(unittest.TestCase):
    def test_three_feed_proof_rejects_wrong_adjustment_units_and_date(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root/'output/_eastmoney_probe/monthly'
            cache.mkdir(parents=True)
            daily = root/'output/_nominal_daily'
            daily.mkdir()
            rows = [{'d':f'{year}-{month:02d}-28','c':50}
                    for year in (2009,2010,2011) for month in range(1,13)]
            rows[31] = {'d':'2011-08-31','c':50.46}
            selected = {r['d'][:7]:r['c'] for r in rows}
            for symbol in ('SPY','TRV'):
                (daily/(symbol+'.json')).write_text(json.dumps({'endpoint':
                    'https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/'
                    'x/US_MinKService.getDailyK?symbol='+symbol,'rows':rows}))
            primary = {'source_url':'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=106.TRV&klt=103&fqt=0',
                       'secid':'106.TRV','mode':'fqt=0','payload':{'rc':0,'data':
                       {'code':'TRV','market':106,'klines':
                        ['2011-08-31,50,50.460,51,49,100,0,1,1,1,1']}}}
            path = cache/'TRV-month-end-review.json'
            path.write_text(json.dumps(primary))
            kaggle = (50.46000542616247,'2011-08-31','reviewed raw-share units','a'*64)
            with patch.object(repair,'BASE',root):
                proof = repair.review_fact(repair.FACTS[-1],kaggle,selected)
                self.assertFalse(proof['five_dollar_eligibility_changed'])
                self.assertEqual(proof['return_pairs'],35)
                for bad in ((5.046,kaggle[1],kaggle[2],kaggle[3]),
                            (kaggle[0],'2011-08-25',kaggle[2],kaggle[3]),
                            (kaggle[0],kaggle[1],None,kaggle[3])):
                    with self.subTest(kaggle=bad), self.assertRaises(ValueError):
                        repair.review_fact(repair.FACTS[-1],bad,selected)
                primary['source_url'] = primary['source_url'].replace('fqt=0','fqt=1')
                path.write_text(json.dumps(primary))
                with self.assertRaises(ValueError):
                    repair.review_fact(repair.FACTS[-1],kaggle,selected)

    def test_wiki_import_excludes_partial_month_preserving_valid_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); output = root/'output'; output.mkdir()
            daily = output/'_nominal_daily'; daily.mkdir()
            bars = {'2017-11':{'date':'2017-11-30','close':243.14},
                    '2017-12':{'date':'2017-12-05','close':238.26}}
            archive = {'archive_sha256':'a'*64,'monthly':{'MMM':bars},
                       'raw_unit_split_checks':[{'symbol':'AAPL','date':'2014-06-09',
                         'split_ratio':7,'raw_before':645,'raw_after':92,
                         'raw_ratio_times_split':1,'adjusted_ratio':1}]}
            review = {'archive_sha256':'a'*64,'snapshot_stamp':[1], 'source_url':'archive',
                      'results':{'MMM':{'status':'crosscheck_pass','archive_symbol':'MMM',
                        'months':bars,'overlap_returns':30,'median_log_deviation':0,
                        'p90_log_deviation':0}}}
            (output/'wiki_monthly_archive.json').write_text(json.dumps(archive))
            (output/'wiki_nominal_review.json').write_text(json.dumps(review))
            (daily/'SPY.json').write_text(json.dumps({'endpoint':
                'https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/'
                'x/US_MinKService.getDailyK?symbol=SPY','rows':
                [{'d':'2017-11-30','c':200},{'d':'2017-12-29','c':200}]}))
            with patch.object(wiki,'BASE',root), patch.object(wiki.db_prices,'_db_stamp',return_value=(1,)):
                wiki.run()
            receipt = json.loads((output/'wiki_nominal_ingest.json').read_text())
            self.assertEqual(receipt['prepared_observations'],1)
            self.assertEqual(receipt['rejected_incomplete_months'],[{
                'symbol':'MMM','month':'2017-12','archive_date':'2017-12-05',
                'benchmark_date':'2017-12-29'}])
            self.assertFalse(receipt['return_prices_locks_and_published_baselines_changed'])


if __name__ == '__main__':
    unittest.main()
