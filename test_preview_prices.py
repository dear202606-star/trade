import datetime as dt
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import pandas as pd

import preview


def timestamp(date):
    return int(dt.datetime.fromisoformat(date).replace(tzinfo=dt.timezone.utc).timestamp())


class PreviewPriceTests(unittest.TestCase):
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
        members=pd.DataFrame([{'ticker':'X','start_date':'2020-01-01','end_date':'2099-12-31'}])
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'preview.json';output.write_bytes(b'previous accepted preview')
            with patch.object(preview,'OUT',str(output)), \
                 patch.object(preview.live_quotes,'load_snapshot',return_value=
                     {'quotes':{'X':{'p':100}},'session':'2026-10-30'}), \
                 patch.object(preview.db_ref,'require_unquarantined_candidates'), \
                 patch.object(preview.datafeed,'load_members',return_value=members), \
                 patch.object(preview.datafeed,'load_sector',return_value=({'X':'Tech'},{})), \
                 patch.object(preview.datafeed,'prefetch'), \
                 patch.object(preview.datafeed,'load_bars',return_value={'2025-12':(1,1)}), \
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
