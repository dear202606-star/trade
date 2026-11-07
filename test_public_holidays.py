# -*- coding: utf-8 -*-
"""免费公共假日接口的漂移核对回归（全部用 stub，不发真实网络请求）。"""
import datetime as dt
import json
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'research'))
import check_public_holidays as cph          # noqa: E402
import trading_window as tw                  # noqa: E402


def holiday(date, name, global_=True, counties=None):
    return {'date': date, 'name': name, 'localName': name,
            'countryCode': 'US', 'global': global_, 'counties': counties,
            'types': ['Public', 'Bank']}


class PublicHolidayCheckTests(unittest.TestCase):
    def setUp(self):
        self.cal = tw.load_calendar()

    def test_easter_algorithm_matches_the_reviewed_good_fridays(self):
        for year, expected in ((2026, '2026-04-03'), (2027, '2027-03-26'),
                               (2028, '2028-04-14')):
            self.assertEqual((cph.easter(year) - dt.timedelta(days=2)).isoformat(),
                             expected)
            self.assertIn(expected, self.cal['closed'])

    def test_federal_holidays_shared_with_nyse_are_reported(self):
        fed = [holiday('2026-11-26', 'Thanksgiving Day'),
               holiday('2026-12-25', 'Christmas Day')]
        r = cph.classify(2026, fed, self.cal)
        self.assertEqual(r['both_closed'], ['2026-11-26', '2026-12-25'])
        self.assertEqual(r['needs_review'], [])

    def test_good_friday_is_a_known_divergence_not_a_review_item(self):
        r = cph.classify(2026, [], self.cal)
        dates = [x['date'] for x in r['known_divergence']['good_friday_nyse_closed']]
        self.assertIn('2026-04-03', dates)
        self.assertNotIn('2026-04-03', [x['date'] for x in r['needs_review']])

    def test_columbus_and_veterans_day_are_known_trading_days(self):
        fed = [holiday('2026-10-12', 'Columbus Day'),
               holiday('2026-11-11', 'Veterans Day')]
        r = cph.classify(2026, fed, self.cal)
        names = [x['name'] for x in r['known_divergence']['federal_only_but_nyse_open']]
        self.assertIn('Columbus Day', names)
        self.assertIn('Veterans Day', names)
        self.assertEqual(r['needs_review'], [])

    def test_unknown_federal_holiday_needs_human_review(self):
        fed = [holiday('2026-08-18', 'Newly Enacted National Holiday')]
        r = cph.classify(2026, fed, self.cal)
        self.assertEqual(len(r['needs_review']), 1)
        report = cph.build_report([2026], self.cal,
                                  fetch=lambda y: fed if y == 2026 else [])
        self.assertEqual(report['status'], 'review')

    def test_weekend_federal_holidays_are_not_treated_as_divergence(self):
        fed = [holiday('2026-07-04', 'Independence Day')]   # 周六
        r = cph.classify(2026, fed, self.cal)
        self.assertNotIn('2026-07-04', [x['date'] for x in r['needs_review']])
        self.assertEqual(r['known_divergence']['federal_only_but_nyse_open'], [])

    def test_state_only_and_non_public_entries_are_ignored(self):
        raw = [holiday('2026-04-03', 'Good Friday', global_=False,
                       counties=['US-CT', 'US-NY']),
               {'date': '2026-02-12', 'name': "Lincoln's Birthday", 'global': False,
                'counties': ['US-CA'], 'types': ['Observance']},
               holiday('2026-11-26', 'Thanksgiving Day')]
        selected = cph.select_federal(raw, 2026)
        self.assertEqual([r['date'] for r in selected], ['2026-11-26'])

    def test_report_is_ok_with_real_reviewed_calendar(self):
        def fake_fetch(year):
            if year != 2026:
                return []
            # 模拟接口返回完整的 2026 联邦假日（与已复核日历一致，仅差 Good Friday）
            shared = [holiday(d, 'Shared Holiday') for d in self.cal['closed']
                      if d.startswith('2026') and d != '2026-04-03']
            return shared + [holiday('2026-10-12', 'Columbus Day'),
                             holiday('2026-11-11', 'Veterans Day')]
        report = cph.build_report([2026], self.cal, fetch=fake_fetch)
        self.assertEqual(report['status'], 'ok', report['needs_review'])
        self.assertEqual(report['needs_review'], [])
        self.assertEqual(len(report['nyse_calendar_sha256']), 64)
        self.assertFalse(report['coverage']['expired'])
        json.dumps(report, ensure_ascii=False)

    def test_expired_coverage_is_a_hard_failure(self):
        cal = dict(self.cal, coverage_to='2025-12-31')
        report = cph.build_report([2026], cal, fetch=lambda y: [])
        self.assertEqual(report['status'], 'coverage_expiring')
        self.assertTrue(report['coverage']['expired'])

    def test_api_failure_is_reported_separately(self):
        def broken(year):
            raise OSError('network down')
        report = cph.build_report([2026], self.cal, fetch=broken)
        self.assertEqual(report['status'], 'api_unavailable')
        self.assertEqual(len(report['fetch_errors']), 1)

    def test_early_close_dates_are_listed_for_manual_nyse_check(self):
        r = cph.classify(2026, [], self.cal)
        self.assertEqual(r['early_close_dates'], ['2026-11-27', '2026-12-24'])


if __name__ == '__main__':
    unittest.main()
