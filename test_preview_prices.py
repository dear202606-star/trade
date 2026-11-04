import datetime as dt
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import preview


def timestamp(date):
    return int(dt.datetime.fromisoformat(date).replace(tzinfo=dt.timezone.utc).timestamp())


class PreviewPriceTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(preview.holding_paths,'load_all',return_value={})
        guard.start();self.addCleanup(guard.stop)

    def test_unaccepted_distribution_cannot_be_valued_as_a_split_only_quote(self):
        with patch.object(preview.holding_paths,'load_all',return_value={('HON',2026):None}), \
             patch.object(preview.holding_paths,'_EVENT_MONTHS',{('HON',2026):'2026-06'}), \
             patch.object(preview.nominal_prices,'price') as price:
            with self.assertRaisesRegex(RuntimeError,'企业行动收益尚未核验'):
                preview.price_ratio('HON',100,'2025-12','2026-11-03')
        price.assert_not_called()

    def test_split_shares_use_actual_historical_cost_not_adjusted_close(self):
        cases=[('BKNG',5355.33,148.13,25,'2026-04-06'),
               ('CRWD',468.76,250.43,4,'2026-07-02'),
               ('KLAC',1215.08,157.91,10,'2026-06-12')]
        for symbol,cost,current,multiplier,date in cases:
            with self.subTest(symbol=symbol), \
                 patch.object(preview.nominal_prices,'price',return_value=cost), \
                 patch.object(preview.datafeed,'_splits',return_value={symbol:[[timestamp(date),multiplier]]}):
                # One original share costs cost and becomes multiplier shares.
                expected=multiplier*current/cost
                actual=preview.price_ratio(symbol,current,'2025-12','2026-10-30')
                self.assertAlmostEqual(actual,expected)
                self.assertLess(actual,8)

    def test_future_and_base_month_splits_do_not_change_current_share_count(self):
        events=[[timestamp('2025-12-15'),2],[timestamp('2026-04-06'),25],
                [timestamp('2026-12-01'),3]]
        with patch.object(preview.datafeed,'_splits',return_value={'X':events}):
            self.assertEqual(preview.split_factor_after('X','2025-12','2026-04-05'),1)
            self.assertEqual(preview.split_factor_after('X','2025-12','2026-04-06'),25)

    def test_missing_native_evidence_has_no_adjusted_price_fallback(self):
        with patch.object(preview.nominal_prices,'price',side_effect=RuntimeError('unverified')):
            with self.assertRaisesRegex(RuntimeError,'unverified'):
                preview.price_ratio('X',100,'2025-12','2026-10-30')

    def test_missing_native_price_preserves_previous_preview_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'preview.json';output.write_bytes(b'previous accepted preview')
            with patch.object(preview,'OUT',str(output)), \
                 patch.object(preview.live_quotes,'load_snapshot',return_value=
                     {'quotes':{'X':{'p':100}},'session':'2026-10-30'}), \
                 patch.object(preview.db_ref,'require_unquarantined_candidates'), \
                 patch.object(preview.db_ref,'load_market_reference',return_value=({'X'},{'X':'Tech'},'reference-version')), \
                 patch.object(preview.datafeed,'prefetch',side_effect=AssertionError('Historical collection forbidden')), \
                 patch.object(preview.datafeed,'load_bars',side_effect=AssertionError('Local history forbidden')), \
                 patch.object(preview.nominal_prices,'price',side_effect=RuntimeError('unverified')):
                with self.assertRaisesRegex(RuntimeError,'unverified'):
                    preview.main()
            self.assertEqual(output.read_bytes(),b'previous accepted preview')

    def test_invalid_prices_or_split_factors_are_rejected(self):
        for cost,current,split in [(0,100,2),(100,float('nan'),2),(100,100,0),
                                    (100,100,float('inf'))]:
            with self.subTest(cost=cost,current=current,split=split), \
                 patch.object(preview.nominal_prices,'price',return_value=cost), \
                 patch.object(preview.datafeed,'_splits',return_value={'X':[[timestamp('2026-04-06'),split]]}):
                with self.assertRaises(ValueError):
                    preview.price_ratio('X',current,'2025-12','2026-10-30')


if __name__=='__main__':
    unittest.main()
