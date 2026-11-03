import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from research import verify_klac_nominal_fact as fact


class NominalFactTests(unittest.TestCase):
    def test_accepted_fact_skips_reprocessing_and_writes(self):
        connection=MagicMock()
        cursor=connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value=[('eastmoney_native',1215.08,'accepted evidence')]
        with patch.object(fact.db_sources,'db',return_value=connection), \
             patch.object(fact,'review') as review:
            fact.run(write=True)
        review.assert_not_called()
        cursor.execute.assert_called_once()
        self.assertTrue(cursor.execute.call_args.args[0].startswith('select'))

    def test_conflicting_accepted_fact_cannot_be_overwritten(self):
        connection=MagicMock()
        cursor=connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value=[('eastmoney_native',121.508,'accepted evidence')]
        with patch.object(fact.db_sources,'db',return_value=connection), \
             patch.object(fact,'review') as review,self.assertRaisesRegex(RuntimeError,'no overwrite'):
            fact.run(write=True)
        review.assert_not_called()
        cursor.execute.assert_called_once()

    def test_wrong_security_adjustment_or_close_rejected_before_selected_reads(self):
        valid={'secid':'105.KLAC','mode':'fqt=0',
               'source_url':'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=105.KLAC&klt=101&fqt=0',
               'payload':{'rc':0,'data':{'code':'KLAC','market':105,
               'klines':['2025-12-31,1243.650,1215.080,1250.930,1214.090,608043,742315824.000,2.96,-2.30,-28.570,0.46']}}}
        variants=[]
        wrong=copy.deepcopy(valid);wrong['mode']='fqt=1';variants.append(wrong)
        wrong=copy.deepcopy(valid);wrong['payload']['data']['code']='OTHER';variants.append(wrong)
        wrong=copy.deepcopy(valid);wrong['payload']['data']['klines'][0]=wrong['payload']['data']['klines'][0].replace('1215.080','121.508');variants.append(wrong)
        for record in variants:
            with self.subTest(record=record),tempfile.TemporaryDirectory() as directory:
                base=Path(directory);folder=base/'output/_liquidity_windows';folder.mkdir(parents=True)
                (folder/'KLAC-2025-01-01-2025-12-31.json').write_text(json.dumps(record))
                cursor=MagicMock()
                with patch.object(fact,'BASE',base),self.assertRaises(ValueError):
                    fact.review(cursor)
                cursor.execute.assert_not_called()


if __name__=='__main__':
    unittest.main()
