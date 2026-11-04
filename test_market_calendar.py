import copy
import datetime as dt
import unittest

from market_calendar import Calendar,fingerprint
from research.install_exchange_calendar import CALENDAR


class ExchangeCalendarTests(unittest.TestCase):
    def setUp(self):
        self.calendar=Calendar(CALENDAR,fingerprint(CALENDAR))

    def at(self,value):
        return self.calendar.state(dt.datetime.fromisoformat(value))

    def test_quote_deadlines_cross_weekends_holidays_early_close_and_dst(self):
        cases=[('2026-11-06T21:00:00+00:00','2026-11-09T14:50:00+00:00'),
               ('2026-11-25T21:00:00+00:00','2026-11-27T14:50:00+00:00'),
               ('2026-11-27T18:00:00+00:00','2026-11-30T14:50:00+00:00'),
               ('2026-03-06T21:00:00+00:00','2026-03-09T13:50:00+00:00'),
               ('2026-11-03T15:00:00+00:00','2026-11-03T15:20:00+00:00')]
        for source,expected in cases:
            self.assertEqual(self.calendar.source_deadline(dt.datetime.fromisoformat(source)).isoformat(),expected)
        with self.assertRaisesRegex(RuntimeError,'coverage missing'):
            self.calendar.source_deadline(dt.datetime(2028,12,29,21,tzinfo=dt.timezone.utc))

    def test_thanksgiving_is_closed_and_friday_settles_at_one(self):
        holiday=self.at('2026-11-26T16:00:00+00:00')
        self.assertFalse(holiday['refresh_due'])
        self.assertEqual(str(holiday['session']),'2026-11-25')
        self.assertTrue(self.at('2026-11-27T17:00:00+00:00')['intraday'])
        early=self.at('2026-11-27T19:00:00+00:00')
        self.assertFalse(early['intraday'])
        self.assertFalse(early['refresh_due'])
        self.assertEqual(early['minimum_source_time'].isoformat(),'2026-11-27T18:00:00+00:00')
        self.assertEqual(str(early['previous_session']),'2026-11-25')

    def test_dst_changes_utc_open_without_changing_nine_thirty_et(self):
        for day,expected in [('2026-03-06',14),('2026-03-09',13),('2026-11-02',14)]:
            opening,closing=self.calendar.bounds(dt.date.fromisoformat(day))
            self.assertEqual((opening.hour,opening.minute),(expected,30))

    def test_christmas_and_year_boundary_are_exchange_not_federal_observance(self):
        self.assertEqual(str(self.at('2026-01-01T16:00:00+00:00')['session']),'2025-12-31')
        self.assertEqual(str(self.at('2026-12-28T12:00:00+00:00')['session']),'2026-12-24')
        self.assertIsNotNone(self.calendar.bounds(dt.date(2027,12,31)))
        self.assertEqual(str(self.at('2028-01-03T12:00:00+00:00')['session']),'2027-12-31')

    def test_opening_delay_budget_and_settlement_grace_have_bounds(self):
        early=self.at('2026-11-03T14:40:00+00:00')
        self.assertEqual(early['minimum_source_time'].isoformat(),'2026-11-02T21:00:00+00:00')
        settled=self.at('2026-11-03T21:21:00+00:00')
        self.assertEqual(settled['minimum_source_time'].isoformat(),'2026-11-03T21:00:00+00:00')

    def test_unknown_coverage_and_tampered_or_conflicting_evidence_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError,'coverage missing'):
            self.at('2029-01-02T17:00:00+00:00')
        bad=copy.deepcopy(CALENDAR);bad['closed'].append('2026-11-27')
        with self.assertRaises(ValueError):Calendar(bad,fingerprint(bad))
        with self.assertRaises(ValueError):Calendar(CALENDAR,'0'*64)


if __name__=='__main__':
    unittest.main()
