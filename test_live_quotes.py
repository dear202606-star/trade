import datetime as dt
import os
import unittest
from unittest.mock import patch

import live_quotes as live
import e2e_check
from market_calendar import Calendar, fingerprint
from research.install_exchange_calendar import CALENDAR


def fields(date='Nov 02 04:00PM EST',year='2026'):
    result = ['name','110','10','unused','10','0','0','0','0','0','10']+['']*19
    result[25] = date; result[26] = '100'; result[29] = year
    return result


class LiveQuoteTests(unittest.TestCase):
    now = dt.datetime(2026,11,3,12,tzinfo=dt.timezone.utc)

    def setUp(self):
        loader=patch.object(live.market_calendar,'load',return_value=Calendar(CALENDAR,fingerprint(CALENDAR)))
        loader.start();self.addCleanup(loader.stop)

    def test_online_probe_checks_database_quotes_instead_of_deployment_files(self):
        cfg = '{"url":"https://example.supabase.co","key":"public-test-key"}'
        payload = {'ok':True,'quotes':{'SPY':{'p':100}}}
        from unittest.mock import MagicMock
        response = MagicMock();response.json.return_value=[{'payload':payload}]
        with patch.object(e2e_check,'get',return_value=(200,cfg)) as static, \
             patch.object(e2e_check.requests,'get',return_value=response) as online:
            self.assertEqual(e2e_check.data('quotes.json'),payload)
        static.assert_called_once_with('data/cloud.json')
        self.assertIn('/rest/v1/live_quote_snapshot',online.call_args.args[0])

    def test_online_probe_cannot_pass_when_database_is_unavailable(self):
        cfg = '{"url":"https://example.supabase.co","key":"public-test-key"}'
        with patch.object(e2e_check,'get',return_value=(200,cfg)) as static, \
             patch.object(e2e_check.requests,'get',side_effect=RuntimeError('offline')), \
             patch.object(e2e_check,'fails',[]) as failures:
            self.assertIsNone(e2e_check.data('quotes.json'))
            self.assertEqual(len(failures),1)
        static.assert_called_once_with('data/cloud.json')

    def test_automated_publication_uses_the_existing_secret_without_a_local_password_file(self):
        expected = object()
        with patch.dict(os.environ,{'SUPABASE_DB_PASSWORD':'test-environment-secret'}), \
             patch.object(live.db_sources.io,'open',side_effect=AssertionError('No local credential file in Actions')), \
             patch.object(live.db_sources.psycopg2,'connect',return_value=expected) as connect:
            self.assertIs(live.db_sources.db(),expected)
        self.assertEqual(connect.call_args.kwargs['password'],'test-environment-secret')

    def test_source_time_controls_snapshot_not_fetch_time(self):
        payload,sha = live.build_snapshot(['SPY','A'],{'gb_spy':fields(),'gb_a':fields()},7,self.now)
        self.assertEqual(payload['ts'],'2026-11-02T21:00:00Z')
        self.assertEqual(payload['ts_local'],'2026-11-03 05:00')
        self.assertEqual(payload['session'],'2026-11-02')
        self.assertFalse(payload['intraday'])
        self.assertEqual(payload['version'],sha)

    def test_rejects_wrong_dst_future_and_missing_source_time(self):
        for row in (fields('Nov 02 04:00PM EDT'),fields('Nov 03 04:00PM EST'),
                    fields(''),fields(year='2025')):
            with self.subTest(row=row[25]),self.assertRaises(ValueError):
                live.build_snapshot(['SPY'],{'gb_spy':row},7,self.now)

    def test_partial_cross_session_and_missing_fx_cannot_publish(self):
        for raw,fx in (({'gb_spy':fields()},7),
                       ({'gb_spy':fields(),'gb_a':fields('Oct 30 04:00PM EDT')},7),
                       ({'gb_spy':fields(),'gb_a':fields()},None)):
            with self.subTest(fx=fx),self.assertRaises(ValueError):
                live.build_snapshot(['SPY','A'],raw,fx,self.now)

    def test_intraday_benchmark_must_be_current_and_within_delay_limit(self):
        now = self.now.replace(hour=16)
        for row in (fields(),fields('Nov 03 10:00AM EST')):
            with self.assertRaises(ValueError):
                live.build_snapshot(['SPY'],{'gb_spy':row},7,now)
        payload,sha = live.build_snapshot(['SPY'],{'gb_spy':fields('Nov 03 10:50AM EST')},7,now)
        self.assertTrue(payload['intraday'])

    def test_online_universe_does_not_read_local_files_or_use_inactive_members(self):
        members = {str(i) for i in range(500)}
        with patch.object(live.db_ref,'load_market_reference',return_value=(members,{},'verified-reference')) as reader:
            actual,reference = live.universe(self.now)
        reader.assert_called_once_with('2026-11-03')
        self.assertEqual(reference,{'as_of':'2026-11-03','version':'verified-reference'})
        self.assertNotIn('OLD',actual)
        self.assertIn('SPY',actual)
        with patch.object(live.db_ref,'load_market_reference',side_effect=RuntimeError('offline')):
            with self.assertRaisesRegex(RuntimeError,'offline'):
                live.universe(self.now)


if __name__=='__main__':
    unittest.main()
