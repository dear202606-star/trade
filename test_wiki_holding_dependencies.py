import copy
import hashlib
import json
import unittest

from research.audit_wiki_holding_dependencies import check_cvc


class WikiHoldingDependencyTests(unittest.TestCase):
    def fixture(self):
        bars={'2015-12':{'close':31.90,'adj_close':31.90}}
        bars.update({'2016-%02d'%m:{'close':31.90+m*.2,'adj_close':31.90+m*.2} for m in range(1,6)})
        archive={'archive_sha256':'pinned','monthly':{'CVC':bars},'events':{'CVC':[]}}
        evidence={'hold_year':2016,'native_base':31.90,'cash_per_old_share':34.90,'archive_sha256':'pinned',
                  'monthly_evidence_sha256':hashlib.sha256(json.dumps(bars,sort_keys=True).encode()).hexdigest()}
        values=[bars['2016-%02d'%m]['close']/31.90 if m<6 else 34.90/31.90 for m in range(1,13)]
        return evidence,values,archive

    def test_no_dividend_period_has_twelve_verified_values_and_uses_actual_cash_exit(self):
        args=self.fixture();proof=check_cvc(*args)
        self.assertEqual(proof['verified_months'],12)
        self.assertEqual(proof['cash_conversion_from_month'],'2016-06')
        bad=copy.deepcopy(args);bad[1][5]=34.87/31.90
        with self.assertRaises(ValueError):check_cvc(*bad)

    def test_unexplained_actions_and_changed_cached_prices_cannot_pass_scope_review(self):
        args=self.fixture();args[2]['events']['CVC']=[{'date':'2016-03-01','dividend':.15,'split_ratio':1}]
        with self.assertRaises(ValueError):check_cvc(*args)
        args=self.fixture();args[2]['monthly']['CVC']['2015-12']['close']=30
        with self.assertRaisesRegex(ValueError,'accepted version'):check_cvc(*args)


if __name__=='__main__':unittest.main()
