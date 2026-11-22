import io
import json
import unittest
import urllib.error
from unittest.mock import patch

import fetch_stockanalysis as source


class StockanalysisHistoryTests(unittest.TestCase):
    def test_explicit_adjusted_and_close_values_keep_their_distinct_units(self):
        self.assertEqual(source.parse_rows([{'t':'2025-12-31','a':100,'c':80}]),{'2025-12':(100.,80.)})
        self.assertEqual(source.parse_rows([]),{})

    def test_missing_adjustment_cannot_be_relabelled_as_total_return(self):
        for row in ({'t':'2025-12-31','c':80},{'t':'2025-12-31','a':100},
                    {'t':'2025-12-31','a':None,'c':80}):
            with self.subTest(row=row),self.assertRaises(ValueError):source.parse_rows([row])

    def test_bad_month_duplicate_nonfinite_boolean_or_negative_price_rejects_whole_payload(self):
        good={'t':'2025-12-31','a':100,'c':80}
        bad=[dict(good,t='2025-13-01'),dict(good,a=True),dict(good,c=0),dict(good,a=-1),
             dict(good,a='NaN'),dict(good,c=float('inf'))]
        for row in bad:
            if row['t']==good['t']:row=dict(row,t='2025-11-28')
            with self.subTest(row=row),self.assertRaises(ValueError):source.parse_rows([good,row])
        with self.assertRaises(ValueError):source.parse_rows([good,dict(good,t='2025-12-01')])

    def test_schema_failure_does_not_retry_or_return_partial_history(self):
        reply=io.BytesIO(json.dumps({'data':{'data':[{'t':'2025-12-31','c':80}]}}).encode())
        with patch.object(source.urllib.request,'urlopen',return_value=reply) as request, \
             patch.object(source.time,'sleep') as sleep:
            with self.assertRaises(ValueError):source.fetch('TEST')
        request.assert_called_once();sleep.assert_not_called()


class FetchRetryPolicyTests(unittest.TestCase):
    def test_not_found_is_terminal_but_rate_limiting_still_backs_off(self):
        for code,calls,sleeps in ((400,1,0),(404,1,0),(429,3,2)):
            error=urllib.error.HTTPError('https://stockanalysis.com/api/x',code,'nope',{},None)
            with self.subTest(code=code), \
                 patch.object(source.urllib.request,'urlopen',side_effect=error) as request, \
                 patch.object(source.time,'sleep') as sleep:
                with self.assertRaises(urllib.error.HTTPError):source.fetch('GONE')
            self.assertEqual(request.call_count,calls);self.assertEqual(sleep.call_count,sleeps)


if __name__=='__main__':unittest.main()
