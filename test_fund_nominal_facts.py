import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from research import import_fund_nominal_facts as facts


class FundFactTests(unittest.TestCase):
    def test_unresolved_security_is_reported_without_discarding_reviewed_points(self):
        connection=MagicMock();cursor=connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value=[]
        cursor.fetchone.return_value=('yahoo_relay','total_return',False)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'output').mkdir()
            with patch.object(facts,'BASE',root), \
                 patch.object(facts.db_sources,'db',return_value=connection), \
                 patch.object(facts.fund,'REVIEWED_NAMES',{'ABNB':'Airbnb','CRWD':'Crowdstrike'}), \
                 patch.object(facts.fund,'run',return_value={'reviewed_unit_proofs':[{'symbol':'ABNB'},{'symbol':'CRWD'}]}), \
                 patch.object(facts,'review_symbol',side_effect=[{'valid':True},ValueError('identity unresolved')]):
                with self.assertRaisesRegex(RuntimeError,'CRWD'):
                    facts.run()
            result=json.loads((root/'output/fund_nominal_fact_review.json').read_text())
        self.assertEqual(result['proofs'],[{'valid':True}])
        self.assertEqual(result['unresolved'],[{'symbol':'CRWD','reason':'identity unresolved'}])
        self.assertTrue(all(call.args[0].lstrip().startswith('select') for call in cursor.execute.call_args_list))

    def test_changed_fitb_issuer_document_is_rejected_before_pdf_processing(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'output/_fund_reports').mkdir(parents=True)
            (root/'output/_fund_reports/FITB_2026_proxy.pdf').write_bytes(b'changed document')
            with patch.object(facts,'BASE',root),self.assertRaisesRegex(ValueError,'issuer closing fact differs'):
                facts.fitb_primary(46.81)

    def test_changed_review_batch_cannot_write_closing_facts(self):
        connection=MagicMock();cursor=connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.side_effect=[[], [], []]
        cursor.fetchone.return_value=('yahoo_relay','total_return',False)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'output').mkdir()
            (root/'output/fund_nominal_fact_review.json').write_text(json.dumps({'write':False,'proofs':[{'old':True}]}))
            with patch.object(facts,'BASE',root), \
                 patch.object(facts.db_sources,'db',return_value=connection), \
                 patch.object(facts.fund,'REVIEWED_NAMES',{'ABNB':'Airbnb'}), \
                 patch.object(facts.fund,'run',return_value={'reviewed_unit_proofs':[{}]}), \
                 patch.object(facts,'review_symbol',return_value={'new':True}):
                with self.assertRaisesRegex(RuntimeError,'differs from prior'):
                    facts.run(write=True)
        self.assertTrue(all(call.args[0].lstrip().startswith('select') for call in cursor.execute.call_args_list))

    def test_accepted_batch_skips_pdf_processing_and_all_writes(self):
        connection=MagicMock();cursor=connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.side_effect=[ [('eastmoney_native',100,'accepted')]
                                     for _ in facts.fund.REVIEWED_NAMES ]
        with patch.object(facts.db_sources,'db',return_value=connection), \
             patch.object(facts.fund,'run') as review:
            facts.run(write=True)
        review.assert_not_called()
        self.assertEqual(cursor.execute.call_count,len(facts.fund.REVIEWED_NAMES))
        self.assertTrue(all(call.args[0].startswith('select') for call in cursor.execute.call_args_list))

    def test_wrong_date_or_company_class_rejected_without_prices(self):
        source={'source_url':facts.fund.SOURCE,'source_sha256':facts.fund.PDF_SHA,'report_date':'2025-12-31'}
        unit={'symbol':'ABNB','date':'2025-12-31','independent_close':'135.72',
              'fund_row':{'name':facts.fund.REVIEWED_NAMES['ABNB'],'shares':1000,'reported_usd_value':135720}}
        for change in [{'date':'2026-11-02'},{'symbol':'OTHER'},
                       {'fund_row':dict(unit['fund_row'],name='Airbnb, Inc., Class B')}]:
            with self.subTest(change=change),self.assertRaises(ValueError):
                facts.review_symbol(MagicMock(),dict(unit,**change),source)

    def test_adjusted_wrong_security_or_wrong_date_quotes_are_rejected(self):
        source={'source_url':facts.fund.SOURCE,'source_sha256':facts.fund.PDF_SHA,'report_date':'2025-12-31'}
        unit={'symbol':'ABNB','date':'2025-12-31','independent_close':'135.72',
              'fund_row':{'name':facts.fund.REVIEWED_NAMES['ABNB'],'shares':1000,'reported_usd_value':135720}}
        valid={'source_url':'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=105.ABNB&klt=101&fqt=0',
               'secid':'105.ABNB','mode':'fqt=0','payload':{'rc':0,'data':{'code':'ABNB','market':105,
               'klines':['2025-12-31,136.62,135.72,137.58,135.56,2188109,297434000,1,1,1,1']}}}
        variants=[]
        row=copy.deepcopy(valid);row['mode']='fqt=1';variants.append(row)
        row=copy.deepcopy(valid);row['payload']['data']['code']='OTHER';variants.append(row)
        row=copy.deepcopy(valid);row['payload']['data']['klines'][0]=row['payload']['data']['klines'][0].replace('2025-12-31','2025-12-30');variants.append(row)
        for record in variants:
            with self.subTest(record=record),tempfile.TemporaryDirectory() as directory:
                folder=Path(directory)/'output/_liquidity_windows';folder.mkdir(parents=True)
                (folder/'ABNB-2025-01-01-2025-12-31.json').write_text(json.dumps(record))
                cursor=MagicMock()
                with patch.object(facts,'BASE',Path(directory)),self.assertRaises(ValueError):
                    facts.review_symbol(cursor,unit,source)
                cursor.execute.assert_not_called()


if __name__=='__main__':
    unittest.main()
