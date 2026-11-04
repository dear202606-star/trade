import unittest
from research.audit_twtr_cash_merger import retained_values


class TwitterCashPathTests(unittest.TestCase):
    def bars(self):
        result={'2021-12':(43.22,43.22)}
        result.update({'2022-%02d'%m:(40+m,40+m) for m in range(1,10)})
        result['2022-10']=(53.70,53.70)
        return result

    def test_exit_month_and_remaining_months_hold_actual_cash_without_interest(self):
        values=retained_values(self.bars(),53.70,54.20)
        self.assertEqual(len(values),12)
        self.assertAlmostEqual(values['2022-09'],49/43.22)
        for month in ('2022-10','2022-11','2022-12'):
            self.assertAlmostEqual(values[month],54.20/43.22)
            self.assertNotAlmostEqual(values[month],53.70/43.22)

    def test_incomplete_pre_exit_history_cannot_be_filled_by_carrying_a_quote(self):
        bars=self.bars();del bars['2022-05']
        with self.assertRaises(KeyError):retained_values(bars,53.70,54.20)


if __name__=='__main__':unittest.main()
