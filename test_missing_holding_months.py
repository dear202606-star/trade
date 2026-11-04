"""A missing month cannot become an unchanged holding or a reweighted survivor."""
import copy
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import strategy_history
import variant_b


class MissingHoldingMonthTests(unittest.TestCase):
    def test_history_producer_stops_on_missing_year_without_republishing_old_files(self):
        with tempfile.TemporaryDirectory() as directory,ExitStack() as stack:
            base=Path(directory);(base/'output').mkdir();(base/'docs').mkdir()
            old=base/'output/strategy_history.json';old.write_text('old accepted snapshot')
            published=base/'docs/strategy_history.json';published.write_text('existing public snapshot')
            frame=pd.DataFrame([{'ticker':'BCR','start_date':'2008-01-01','end_date':'2017-12-29'}])
            for target,value in [('BASE',directory),('OUT',str(published)),('CURVE',str(base/'docs/curve.json')),
                                 ('range',[2017])]:
                if target=='range':stack.enter_context(patch.object(strategy_history,target,return_value=value,create=True))
                else:stack.enter_context(patch.object(strategy_history,target,value))
            stack.enter_context(patch.object(strategy_history.sys,'argv',['strategy_history.py','--refresh']))
            stack.enter_context(patch.object(strategy_history.datafeed,'load_members',return_value=frame))
            stack.enter_context(patch.object(strategy_history.datafeed,'load_sector',return_value=({'BCR':'Health'},{})))
            stack.enter_context(patch.object(strategy_history.db_ref,'require_unquarantined_candidates'))
            stack.enter_context(patch.object(strategy_history.datafeed,'prefetch'))
            stack.enter_context(patch.object(strategy_history,'load_bars',return_value={}))
            stack.enter_context(patch.object(strategy_history,'pick_for_year',return_value=[{'ticker':'BCR','weight':1,'ret12':.1,'sector':'Health'}]))
            stack.enter_context(patch.object(strategy_history,'year_perf',return_value=(None,None,None,None)))
            with self.assertRaisesRegex(RuntimeError,'2017'):
                strategy_history.main()
            self.assertEqual(old.read_text(),'old accepted snapshot')
            self.assertEqual(published.read_text(),'existing public snapshot')
            self.assertFalse((base/'docs/curve.json').exists())

    def bars(self,missing=None):
        result={'2016-12':(100,100)}
        result.update({'2017-%02d'%m:(110,110) for m in range(1,13)})
        if missing:result.pop(missing)
        return result

    def paths(self,stack):
        # Engineering has reviewed event paths; the release branch still uses
        # direct bars. This test covers the same no-event contract in both.
        if hasattr(strategy_history,'holding_paths'):
            stack.enter_context(patch.object(strategy_history.holding_paths,'load_all',return_value={}))

    def test_interior_or_terminal_gap_cannot_be_forward_filled(self):
        for missing in ('2017-04','2017-12'):
            with self.subTest(missing=missing),ExitStack() as stack:
                self.paths(stack)
                bars=self.bars(missing)
                stack.enter_context(patch.object(strategy_history,'load_bars',return_value=bars))
                stack.enter_context(patch.object(variant_b,'load_bars',return_value=bars))
                picks=[{'ticker':'BCR','weight':1}]
                self.assertEqual(strategy_history.year_perf(2017,copy.deepcopy(picks)),(None,None,None,None))
                with self.assertRaises(RuntimeError):variant_b.year_perf(picks,2017)

    def test_year_return_cannot_drop_missing_holding_and_renormalize_survivor(self):
        with ExitStack() as stack:
            self.paths(stack)
            stack.enter_context(patch.object(variant_b,'load_bars',side_effect=lambda s:self.bars('2017-12') if s=='BCR' else self.bars()))
            with self.assertRaises(RuntimeError):
                variant_b.year_return([{'ticker':'BCR','weight':.5},{'ticker':'OTHER','weight':.5}],2017)

    def test_complete_holding_keeps_observed_return(self):
        with ExitStack() as stack:
            self.paths(stack)
            stack.enter_context(patch.object(strategy_history,'load_bars',return_value=self.bars()))
            stack.enter_context(patch.object(variant_b,'load_bars',return_value=self.bars()))
            picks=[{'ticker':'OTHER','weight':1}]
            self.assertAlmostEqual(strategy_history.year_perf(2017,copy.deepcopy(picks))[0],.1)
            self.assertAlmostEqual(variant_b.year_return(picks,2017),.1)


if __name__=='__main__':
    unittest.main()
