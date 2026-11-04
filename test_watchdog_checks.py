import unittest
import datetime as dt
import json
import hashlib

from watchdog_checks import quality_problems,quote_problems,history_problems
from market_calendar import Calendar,fingerprint
from research.install_exchange_calendar import CALENDAR


class QualityReportTests(unittest.TestCase):
    def report(self, statuses=('pass',), degraded=False):
        return {'summary': {'fail': statuses.count('fail')},
                'degraded': degraded,
                'checks': [{'status': status} for status in statuses]}

    def test_valid_report(self):
        self.assertEqual(quality_problems(self.report(('pass', 'warn'))), [])

    def test_published_failure_and_degradation(self):
        self.assertEqual(len(quality_problems(self.report(('pass', 'fail'), True))), 2)

    def test_missing_or_malformed_evidence(self):
        for report in (None, [], {}, {'summary': {}},
                       dict(self.report(), degraded='false'),
                       dict(self.report(), checks=[]),
                       dict(self.report(), checks=[{'status': 'unknown'}]),
                       dict(self.report(), summary={'fail': True}),
                       dict(self.report(), summary={'fail': -1}),
                       dict(self.report(), summary={'fail': 1})):
            with self.subTest(report=report), self.assertRaises(ValueError):
                quality_problems(report)


class OnlineQuoteMonitorTests(unittest.TestCase):
    def snapshot(self,source,session,previous,worker_time):
        calendar=Calendar(CALENDAR,fingerprint(CALENDAR))
        payload={'ok':True,'ts':source,'session':session,'previous_session':previous,
                 'calendar_sha256':calendar.sha,'count':1,'universe':1,'quotes':{'SPY':{'p':100}}}
        sha=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        payload['version']=sha
        row={'source_as_of':source,'evidence_sha256':sha,'payload':payload}
        heartbeat={'finished_at':worker_time,'metrics':{'stored_evidence_sha256':sha}}
        return row,heartbeat,calendar

    def test_weekend_and_holiday_close_do_not_require_history_or_new_price_writes(self):
        args=self.snapshot('2026-11-27T18:00:00Z','2026-11-27','2026-11-25','2026-11-27T18:05:00Z')
        self.assertEqual(quote_problems(*args,dt.datetime(2026,11,29,18,tzinfo=dt.timezone.utc)),[])
        args=self.snapshot('2026-11-25T21:00:00Z','2026-11-25','2026-11-24','2026-11-25T21:05:00Z')
        self.assertEqual(quote_problems(*args,dt.datetime(2026,11,26,18,tzinfo=dt.timezone.utc)),[])

    def test_stale_source_and_stale_worker_are_independent_failures(self):
        now=dt.datetime(2026,11,3,16,tzinfo=dt.timezone.utc)
        args=self.snapshot('2026-11-02T21:00:00Z','2026-11-02','2026-10-30','2026-11-03T15:59:00Z')
        self.assertIn('报价未达到交易所应有时点',quote_problems(*args,now))
        args=self.snapshot('2026-11-03T15:50:00Z','2026-11-03','2026-11-02','2026-11-03T15:00:00Z')
        self.assertIn('报价刷新任务超过 15 分钟未成功',quote_problems(*args,now))

    def test_tampered_payload_and_missing_receipt_cannot_pass(self):
        row,event,calendar=self.snapshot('2026-11-03T21:00:00Z','2026-11-03','2026-11-02','2026-11-03T21:05:00Z')
        now=dt.datetime(2026,11,4,2,tzinfo=dt.timezone.utc)
        self.assertIn('缺少实际报价刷新回执',quote_problems(row,None,calendar,now))
        row['payload']['quotes']['SPY']['p']=200
        with self.assertRaises(ValueError):quote_problems(row,event,calendar,now)

    def test_frozen_history_is_not_stale_but_new_year_or_missing_year_still_fails(self):
        now=dt.datetime(2026,11,4,tzinfo=dt.timezone.utc)
        old={'generated_at':'2026-01-01T00:00:00Z','years':[{'year':2024},{'year':2025}]}
        self.assertEqual(history_problems(old,now),[])
        self.assertTrue(history_problems(old,now.replace(year=2027)))
        self.assertTrue(history_problems({'years':[{'year':2023},{'year':2025}]},now))


if __name__ == '__main__':
    unittest.main()
