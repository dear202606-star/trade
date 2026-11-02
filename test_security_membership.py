import unittest
from unittest.mock import patch

import pandas as pd
import security_membership
import variant_b
import holding_paths
import nominal_prices
import audit_b


class SecurityMembershipTests(unittest.TestCase):
    alias={'old':'OLD','current':'NEW','effective_date':'2022-06-09'}

    def test_fingerprint_uses_actual_online_reference_and_alias_inputs(self):
        refs={'members':{'NEW':[('2022-06-09','2099-12-31')]},'sectors':{'NEW':'Tech'}}
        with patch.object(variant_b,'_REFERENCE_INPUT',refs), \
             patch.object(variant_b,'_db_sha',return_value=('db','selected')), \
             patch.object(nominal_prices,'load_all',return_value={('NEW','2022-12'):10}), \
             patch.object(holding_paths,'input_sha',return_value='events'), \
             patch.object(security_membership,'input_sha',return_value='aliases') as alias_sha:
            initial=variant_b._input_sha()
            refs['members']['NEW']=[('2022-06-10','2099-12-31')]
            self.assertNotEqual(initial,variant_b._input_sha())
            refs['members']['NEW']=[('2022-06-09','2099-12-31')]
            refs['sectors']['NEW']='Industrial'
            self.assertNotEqual(initial,variant_b._input_sha())
            refs['sectors']['NEW']='Tech';alias_sha.return_value='revoked-aliases'
            self.assertNotEqual(initial,variant_b._input_sha())

    def members(self,rows,year=2022,aliases=None):
        frame=pd.DataFrame(rows,columns=['ticker','start_date','end_date'])
        reviewed=[self.alias] if aliases is None else aliases
        with patch.object(security_membership,'load_aliases',return_value=reviewed):
            result=variant_b.members_full_year(frame,year)
        for ticker in set(frame.ticker)|{a[k] for a in reviewed for k in ('old','current')}:
            self.assertEqual(ticker in result,audit_b.full_year_member(frame,reviewed,ticker,year))
        return result

    def test_continuous_same_security_uses_only_year_end_ticker(self):
        rows=[('OLD','2000-01-01','2022-06-09'),('NEW','2022-06-09','2099-12-31')]
        self.assertEqual(self.members(rows),{'NEW'})

    def test_real_membership_gap_is_not_filled_by_alias(self):
        self.assertEqual(self.members([('OLD','2000-01-01','2022-06-07'),
                                       ('NEW','2022-06-09','2099-12-31')]),set())

    def test_midyear_entry_and_exit_still_fail_whole_year_requirement(self):
        for start,end in [('2022-03-01','2099-12-31'),('2000-01-01','2022-10-01')]:
            with self.subTest(start=start,end=end):
                self.assertEqual(self.members([('OLD',start,'2022-06-09'),
                                               ('NEW','2022-06-09',end)]),set())

    def test_unreviewed_tickers_are_never_joined(self):
        self.assertEqual(self.members([('X','2000-01-01','2022-06-09'),
                                       ('Y','2022-06-09','2099-12-31')]),set())

    def test_later_reuse_of_old_code_cannot_fill_pre_rename_gap(self):
        self.assertEqual(self.members([('OLD','2000-01-01','2022-06-09'),
                                       ('OLD','2022-06-10','2099-12-31'),
                                       ('NEW','2022-06-09','2099-12-31')]),{'NEW'})
        self.assertEqual(self.members([('OLD','2022-06-09','2099-12-31'),
                                       ('NEW','2022-06-09','2099-12-31')]),set())

    def test_no_future_alias_or_post_rename_reuse_is_joined(self):
        rows=[('OLD','2000-01-01','2022-06-09'),('NEW','2022-06-09','2099-12-31'),
              ('OLD','2023-01-01','2099-12-31')]
        self.assertEqual(self.members(rows,2021),{'OLD'})
        self.assertEqual(self.members(rows,2023),{'OLD','NEW'})

    def test_stale_old_code_cannot_hide_successor_leaving_index(self):
        self.assertEqual(self.members([('OLD','2000-01-01','2099-12-31'),
                                       ('NEW','2022-06-09','2022-10-01')]),set())

    def test_ambiguous_same_year_chain_requires_its_own_review(self):
        aliases=[self.alias,{'old':'NEW','current':'THIRD','effective_date':'2022-10-01'}]
        with self.assertRaisesRegex(RuntimeError,'require review'):
            self.members([],aliases=aliases)


if __name__=='__main__':unittest.main()
