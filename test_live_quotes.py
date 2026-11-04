import datetime as dt
import os
import unittest
from unittest.mock import patch

import live_quotes as live


def fields(date='Nov 02 04:00PM EST',year='2026'):
    result = ['name','110','10','unused','10','0','0','0','0','0','10']+['']*19
    result[25] = date; result[26] = '100'; result[29] = year
    return result


class LiveQuoteTests(unittest.TestCase):
    now = dt.datetime(2026,11,3,12,tzinfo=dt.timezone.utc)

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
        members = {str(i):[('2000-01-01','2099-12-31')] for i in range(500)}
        members['OLD']=[('2000-01-01','2014-01-01')]
        with patch.object(live.db_ref,'load_members',return_value=members):
            actual = live.universe(self.now)
        self.assertNotIn('OLD',actual)
        self.assertIn('SPY',actual)
        with patch.object(live.db_ref,'load_members',side_effect=RuntimeError('offline')):
            with self.assertRaisesRegex(RuntimeError,'offline'):
                live.universe(self.now)


if __name__=='__main__':
    unittest.main()
