import json
import tempfile
import unittest
from pathlib import Path

from research.audit_nominal_month_ends import daily_month_end


class MonthEndEvidenceTests(unittest.TestCase):
    def check_record(self, rows, endpoint_symbol='SPY'):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'daily.json'
            path.write_text(json.dumps({'endpoint':
                'https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/'
                'x/US_MinKService.getDailyK?symbol='+endpoint_symbol, 'rows': rows}))
            return daily_month_end(path, 'SPY', '2017-12')

    def test_last_observation_without_assuming_calendar_month_last_day(self):
        result = self.check_record([{'d': '2017-12-29', 'c': 235.37},
                                    {'d': '2017-12-05', 'c': 238.26}])
        self.assertEqual(result['date'], '2017-12-29')
        self.assertEqual(result['close'], 235.37)

    def test_wrong_security_duplicate_missing_and_invalid_price_rejected(self):
        for rows in ([], [{'d': '2017-12-29', 'c': 0}],
                     [{'d': '2017-12-29', 'c': 'NaN'}],
                     [{'d': '2017-12-29', 'c': 1}]*2):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.check_record(rows)
        with self.assertRaises(ValueError):
            self.check_record([{'d': '2017-12-29', 'c': 1}], 'MMM')


if __name__ == '__main__':
    unittest.main()
