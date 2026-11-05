import unittest
from unittest.mock import MagicMock, patch

import historical_lineage as lineage
from research.install_historical_identity_reviews import FACTS


class HistoricalLineageTests(unittest.TestCase):
    def test_legacy_full_generation_cannot_reuse_old_accepted_looking_outputs(self):
        with patch.object(lineage,'load',return_value=FACTS),self.assertRaisesRegex(RuntimeError,'unresolved equity identities'):
            lineage.require_complete_history()

    def test_guard_code_changes_invalidate_identity_input_fingerprint(self):
        with patch.object(lineage,'load',return_value=FACTS),patch.object(lineage.Path,'read_bytes',return_value=b'guard version 1') as code:
            old=lineage.input_sha();code.return_value=b'guard version 2'
            self.assertNotEqual(old,lineage.input_sha())

    def test_rank_and_holding_identity_boundaries_do_not_merge_distinct_equities(self):
        with patch.object(lineage,'load',return_value=FACTS):
            for symbol, before, after in [('IR','2020-02','2020-03'),('JCI','2016-08','2016-09')]:
                with self.subTest(symbol=symbol):
                    with self.assertRaisesRegex(RuntimeError,'differs'):
                        lineage.require_rank(symbol,before)
                    with self.assertRaisesRegex(RuntimeError,'requires independent'):
                        lineage.require_holding(symbol,before)
                    lineage.require_rank(symbol,after)
                    lineage.require_holding(symbol,after)
            lineage.require_rank('OTHER','2000-01')

    def test_transition_year_cannot_claim_new_ir_was_a_member_all_year(self):
        with patch.object(lineage,'load',return_value=FACTS):
            with self.assertRaisesRegex(RuntimeError,'two index securities'):
                lineage.require_membership({'IR','TT'},2020)
            lineage.require_membership({'IR','TT'},2021)
            lineage.require_membership({'OTHER'},2020)

    def test_crossing_transition_cannot_use_parent_adjusted_returns(self):
        with patch.object(lineage,'load',return_value=FACTS):
            for symbol,base,end in [('IR','2019-12','2020-12'),('TT','2019-12','2020-12'),
                                    ('JCI','2015-12','2016-12')]:
                with self.subTest(symbol=symbol),self.assertRaisesRegex(RuntimeError,'ownership'):
                    lineage.require_rank(symbol,end,base)
            with self.assertRaisesRegex(RuntimeError,'holding path'):
                lineage.require_holding('TT','2019-12','2020-12')
            lineage.require_rank('IR','2025-12','2024-12')

    def test_tampered_or_changed_source_reviews_are_not_loaded(self):
        for tampered,source in [(True,'yahoo_relay'),(False,'other')]:
            connection=MagicMock();cursor=connection.cursor.return_value.__enter__.return_value
            rows=[(s+':lineage-v1',{'proof':p,'evidence_sha256':'bad' if tampered else lineage.digest(p)})
                  for s,p in sorted(FACTS.items())]
            cursor.fetchall.side_effect=[rows,[(s,source) for s in sorted(FACTS)]]
            with patch.object(lineage,'_REVIEWS',None),patch.object(lineage.db_select,'db',return_value=connection):
                with self.assertRaisesRegex(RuntimeError,'conflicting or stale'):
                    lineage.load()


if __name__ == '__main__':
    unittest.main()
