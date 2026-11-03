import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research import collect_planned_liquidity as planner


class PlannedLiquidityTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base=Path(self.directory.name)
        (self.base/'output').mkdir()
        self.receipt=self.base/'output/liquidity_window_ingest.json'

    def execute(self,records,targets=None):
        if targets is None:targets={'2025-12':['AAA','BBB'],'2026-10':['CCC']}
        def collect(symbols,months,write):
            self.assertTrue(write)
            self.receipt.write_text(json.dumps({'results':records[months[0]]}))
        with patch.object(planner,'BASE',self.base), \
             patch.object(planner,'inventory',return_value={'targets_by_month':targets}), \
             patch.object(planner,'collect',side_effect=collect) as calls:
            planner.run()
        return calls

    def test_complete_batches_use_planned_symbols_and_preserve_seed(self):
        self.receipt.write_text('original seed')
        calls=self.execute({'2025-12':[{'symbol':'AAA','status':'stored'},
                                     {'symbol':'BBB','status':'review_required'}],
                            '2026-10':[{'symbol':'CCC','status':'stored'}]})
        self.assertEqual(calls.call_count,2)
        self.assertEqual((self.base/'output/liquidity_window_seed_ingest.json').read_text(),'original seed')
        progress=json.loads((self.base/'output/liquidity_plan_progress.json').read_text())
        self.assertEqual(progress[0]['stored'],1)
        self.assertEqual(progress[0]['review_required'],1)
        self.assertEqual(progress[0]['not_attempted'],0)

    def test_network_failure_has_nonzero_failure_and_never_starts_next_month(self):
        def failed(*args,**kwargs):
            self.receipt.write_text(json.dumps({'results':[{'symbol':'AAA','status':'network_failure'}]}))
        with patch.object(planner,'BASE',self.base), \
             patch.object(planner,'inventory',return_value={'targets_by_month':{'2025-12':['AAA','BBB'],'2026-10':['CCC']}}), \
             patch.object(planner,'collect',side_effect=failed) as calls:
            with self.assertRaisesRegex(RuntimeError,'Network/source failure'):planner.run()
        self.assertEqual(calls.call_count,1)
        progress=json.loads((self.base/'output/liquidity_plan_progress.json').read_text())[0]
        self.assertEqual(progress['network_failures'],1)
        self.assertEqual(progress['not_attempted'],1)
        self.assertTrue((self.base/'output/liquidity_ingest_2025-12.json').exists())

    def test_rejects_incomplete_receipt_without_network_error(self):
        with self.assertRaisesRegex(RuntimeError,'omitted requested'):
            self.execute({'2025-12':[{'symbol':'AAA','status':'stored'}]})

    def test_rejects_wrong_month_and_unrequested_or_duplicate_symbols(self):
        for rows,error in [([{'symbol':'AAA','rank_month':'2026-10','status':'stored'}],'another month'),
                           ([{'symbol':'OTHER','status':'stored'}],'duplicate/unrequested'),
                           ([{'symbol':'AAA','status':'stored'}]*2,'duplicate/unrequested')]:
            with self.subTest(rows=rows),self.assertRaisesRegex(RuntimeError,error):
                self.execute({'2025-12':rows})

    def test_preserves_previous_month_receipt_when_resuming(self):
        previous=b'{"results": [{"symbol": "OLD", "status": "stored"}]}\n'
        target=self.base/'output/liquidity_ingest_2025-12.json'
        target.write_bytes(previous)
        self.execute({'2025-12':[{'symbol':'AAA','status':'stored'}]},targets={'2025-12':['AAA']})
        archive=target.with_name(target.stem+'_'+hashlib.sha256(previous).hexdigest()[:16]+'.json')
        self.assertEqual(archive.read_bytes(),previous)
        self.assertEqual(json.loads(target.read_text())['results'][0]['symbol'],'AAA')

    def test_empty_inventory_never_calls_source(self):
        self.assertEqual(self.execute({},targets={}).call_count,0)
        self.assertEqual(json.loads((self.base/'output/liquidity_plan_progress.json').read_text()),[])

    def test_unknown_status_is_not_a_success(self):
        with self.assertRaisesRegex(RuntimeError,'unknown status'):
            self.execute({'2025-12':[{'symbol':'AAA','status':'unexpected'}]})

    def test_accepted_old_calculator_never_becomes_a_collection_target(self):
        from research import audit_liquidity_inventory as inventory
        with patch.object(inventory,'BASE',self.base),patch.object(inventory.db_select,'db') as database:
            cursor=database.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchall.return_value=[('AAA','total_return',10,True,'older-accepted-version')]
            result=inventory.run()
        self.assertEqual(result['targets_by_month'],{})
        self.assertTrue(all(r['status']=='accepted_version_review_required' for r in result['rows']))

    def test_version_review_stops_before_any_external_collection(self):
        with patch.object(planner,'BASE',self.base), \
             patch.object(planner,'inventory',return_value={
                 'status_counts':{'accepted_version_review_required':1},'targets_by_month':{'2025-12':['AAA']}}), \
             patch.object(planner,'collect') as source:
            with self.assertRaisesRegex(RuntimeError,'automatic recollection forbidden'):planner.run()
            source.assert_not_called()


if __name__=='__main__':unittest.main()
