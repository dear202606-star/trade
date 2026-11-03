import unittest
from research.audit_fund_native_units import FUND, parse_rows, unit_support, check_csv_date


class FundUnitTests(unittest.TestCase):
    def test_downloaded_date_not_request_parameter_controls_acceptance(self):
        good=b'iShares Core S&P 500 ETF\nFund Holdings as of,"Dec 31, 2025"\n'
        wrong=good.replace(b'Dec 31, 2025', b'Nov 02, 2026')
        self.assertEqual(check_csv_date(good,'2025-12-31'),'2025-12-31')
        with self.assertRaisesRegex(ValueError,'2026-11-02'):
            check_csv_date(wrong,'2025-12-31')
    def text(self, body):
        return 'Schedule of Investments\nDecember 31, 2025\n'+FUND+'\n'+body

    def test_one_line_and_two_line_numeric_columns_remain_separate(self):
        for body in ['Company, Class A(a). . .\n1,000\n135,720',
                     'Company, Class A(a). . .\n1,000 $ 135,720']:
            rows = parse_rows(self.text(body), 9)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['shares'], 1000)
            self.assertEqual(rows[0]['reported_usd_value'], 135720)
            self.assertEqual(rows[0]['name'], 'Company, Class A(a)')

    def test_wrong_date_or_fund_is_rejected(self):
        for text in [self.text('').replace('December 31, 2025','September 30, 2025'),
                     self.text('').replace(FUND,'Other Fund')]:
            with self.assertRaises(ValueError):
                parse_rows(text, 9)

    def test_unit_band_corroborates_native_price_and_rejects_split_units(self):
        row = {'shares':5529473,'reported_usd_value':750460076}
        self.assertIn('unit_interval_low', unit_support(row,'135.72'))
        for price in ['67.86', '271.44', 'NaN', '0']:
            with self.subTest(price=price),self.assertRaises(ValueError):
                unit_support(row,price)


if __name__ == '__main__':
    unittest.main()
