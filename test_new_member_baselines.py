import copy
import unittest
from research.stage_new_member_baselines import FACTS,SINA,review


class NewMemberBaselineTests(unittest.TestCase):
    def fixture(self):
        fact=FACTS[0]
        rows=[{'d':'2025-12-%02d'%day,'o':86,'c':86.89,'h':88,'l':85,'v':100} for day in range(1,25)]
        rows.append(dict(rows[0],d='2025-12-31'))
        return fact,{'endpoint':SINA+fact['symbol'],'rows':rows}

    def test_matching_exact_date_is_archived_without_approving_a_price_series(self):
        fact,record=self.fixture();result=review(fact,record)
        self.assertFalse(result['price_selection_accepted'])
        self.assertFalse(result['nominal_fact_accepted'])
        self.assertEqual(result['observed_daily_2025']['2025-12-31']['close'],86.89)

    def test_identity_wrong_date_duplicate_and_adjusted_price_are_rejected(self):
        fact,record=self.fixture()
        for mode in ('identity','date','duplicate','price'):
            bad=copy.deepcopy(record)
            if mode=='identity':bad['endpoint']=SINA+'OTHER'
            elif mode=='date':bad['rows'][-1]['d']='2025-12-30'
            elif mode=='duplicate':bad['rows'].append(bad['rows'][-1])
            else:bad['rows'][-1]['c']=43.445
            with self.subTest(mode=mode),self.assertRaises(ValueError):review(fact,bad)


if __name__=='__main__':unittest.main()
