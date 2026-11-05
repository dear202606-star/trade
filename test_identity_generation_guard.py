"""Generation must stop on unknown security identity without replacing outputs."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
import db_ref
import strategy_history
import variant_b


class IdentityGenerationGuardTests(unittest.TestCase):
    def test_full_history_identity_failure_preserves_existing_artifact_before_prices(self):
        from research.install_historical_identity_reviews import FACTS
        frame=pd.DataFrame([{'ticker':'OTHER','start_date':'2008-01-01','end_date':'2099-12-31'}])
        with tempfile.TemporaryDirectory() as directory:
            old=Path(directory)/'strategy_history.json';old.write_text('old accepted artifact')
            with patch.object(strategy_history,'OUT',str(old)), \
                 patch.object(strategy_history,'locked_ok',return_value=(False,'review needed')), \
                 patch.object(strategy_history,'missing_new_year',return_value=None), \
                 patch.object(strategy_history.sys,'argv',['strategy_history.py']), \
                 patch.object(strategy_history.datafeed,'load_members',return_value=frame), \
                 patch.object(strategy_history.datafeed,'load_sector',return_value=({'OTHER':'Tech'},{})), \
                 patch.object(db_ref,'require_unquarantined_candidates'), \
                 patch.object(strategy_history.historical_lineage,'load',return_value=FACTS), \
                 patch.object(strategy_history.datafeed,'prefetch') as prices:
                with self.assertRaisesRegex(RuntimeError,'unresolved equity identities'):
                    strategy_history.main()
            prices.assert_not_called()
            self.assertEqual(old.read_text(),'old accepted artifact')

    def test_online_identity_guard_rejects_quarantine_and_unavailable_database(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [('LSI','Different issuer')]
        with patch.object(db_ref,'db',return_value=connection):
            with self.assertRaisesRegex(RuntimeError,'LSI'):
                db_ref.require_unquarantined_candidates(['LSI','ABC'])
        connection.close.assert_called_once()
        cursor.fetchall.return_value = []
        with patch.object(db_ref,'db',return_value=connection):
            db_ref.require_unquarantined_candidates(['ABC'])
        with patch.object(db_ref,'db',side_effect=RuntimeError('offline')) as read:
            db_ref.require_unquarantined_candidates([])
            read.assert_not_called()
            with self.assertRaisesRegex(RuntimeError,'身份隔离检查失败'):
                db_ref.require_unquarantined_candidates(['ABC'])

    def test_history_rebuild_stops_before_prices_and_preserves_old_output(self):
        frame = pd.DataFrame([{'ticker':'LSI','start_date':'2008-01-01','end_date':'2014-05-06'}])
        with tempfile.TemporaryDirectory() as directory:
            old = Path(directory)/'strategy_history.json'; old.write_text('old accepted artifact')
            with patch.object(strategy_history,'OUT',str(old)), \
                 patch.object(strategy_history,'locked_ok',return_value=(False,'review needed')), \
                 patch.object(strategy_history,'missing_new_year',return_value=None), \
                 patch.object(strategy_history.sys,'argv',['strategy_history.py']), \
                 patch.object(strategy_history.datafeed,'load_members',return_value=frame), \
                 patch.object(strategy_history.datafeed,'load_sector',return_value=({'LSI':'Tech'},{})), \
                 patch.object(db_ref,'require_unquarantined_candidates',side_effect=RuntimeError('LSI identity')) as guard, \
                 patch.object(strategy_history.datafeed,'prefetch') as prices:
                with self.assertRaisesRegex(RuntimeError,'LSI identity'):
                    strategy_history.main()
            self.assertIn('LSI',guard.call_args.args[0])
            prices.assert_not_called()
            self.assertEqual(old.read_text(),'old accepted artifact')

    def test_b_rebuild_stops_before_writing_any_strategy_files(self):
        with tempfile.TemporaryDirectory() as directory:
            old = Path(directory)/'strategy_history.json'; old.write_text('old accepted artifact')
            with patch.object(variant_b,'OUT',directory), \
                 patch.object(db_ref,'load_members',return_value={'LSI':[('2008-01-01','2014-05-06')]}), \
                 patch.object(db_ref,'load_sectors',return_value={'LSI':'Tech'}), \
                 patch.object(variant_b.db_prices,'load_all',return_value={}), \
                 patch.object(variant_b,'members_full_year',return_value={'LSI'}), \
                 patch.object(db_ref,'require_unquarantined_candidates',side_effect=RuntimeError('LSI identity')) as guard, \
                 patch.object(variant_b,'frozen_bars') as prices, \
                 patch.object(variant_b,'_REFERENCE_INPUT',None,create=True):
                with self.assertRaisesRegex(RuntimeError,'LSI identity'):
                    variant_b.main()
            self.assertIn('LSI',guard.call_args.args[0])
            prices.assert_not_called()
            self.assertEqual(old.read_text(),'old accepted artifact')
            self.assertEqual([p.name for p in Path(directory).iterdir()],['strategy_history.json'])



if __name__ == "__main__":
    unittest.main()
