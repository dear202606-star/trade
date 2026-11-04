import unittest
from research.audit_atvi_cash_merger import retained_values


class ActivisionCashPathTests(unittest.TestCase):
    def bars(self):
        result={'2022-12':(75.734,76.55)}
        result.update({'2023-%02d'%m:(80.,81.) for m in range(1,10)})
        result['2023-10']=(94.42,94.42)
        return result

    def test_dividend_stays_in_adjusted_growth_without_an_extra_cash_addition(self):
        values=retained_values(self.bars())
        for month in ('2023-10','2023-11','2023-12'):
            self.assertAlmostEqual(values[month],95/75.734)
            self.assertNotAlmostEqual(values[month],(95+.99)/75.734)
            self.assertNotAlmostEqual(values[month],95/76.55)

    def test_missing_pre_exit_month_cannot_be_filled_with_last_quote(self):
        bars=self.bars();del bars['2023-07']
        with self.assertRaises(KeyError):retained_values(bars)


if __name__=='__main__':unittest.main()
