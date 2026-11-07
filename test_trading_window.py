# -*- coding: utf-8 -*-
"""交易时段门禁回归：口径必须与策略用的已复核 NYSE 日历逐点一致。"""
import datetime as dt
import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch

import trading_window as tw
from market_calendar import Calendar, fingerprint
from research.install_exchange_calendar import CALENDAR

BASE = Path(__file__).resolve().parent


def at(value):
    return dt.datetime.fromisoformat(value)


class TradingWindowTests(unittest.TestCase):
    def setUp(self):
        self.cal = tw.load_calendar()
        self.reviewed = Calendar(CALENDAR, fingerprint(CALENDAR))

    def test_pure_json_matches_the_reviewed_evidence(self):
        """门禁与数据库证据必须来自同一份日历，不许出现第二套口径。"""
        self.assertEqual(tw.fingerprint(self.cal), fingerprint(CALENDAR))
        self.assertEqual(self.cal, CALENDAR)
        self.assertEqual(self.cal['market'], 'XNYS')

    def test_weekends_and_nyse_holidays_are_skipped(self):
        cases = [
            '2026-11-08T15:00:00+00:00',   # 周日
            '2026-11-26T16:00:00+00:00',   # 感恩节
            '2026-12-25T16:00:00+00:00',   # 圣诞
            '2026-01-01T16:00:00+00:00',   # 元旦
            '2026-07-03T15:00:00+00:00',   # 独立日顺延观测
        ]
        for stamp in cases:
            for task in ('intraday', 'closing', 'publish'):
                r = tw.gate(task, at(stamp), self.cal)
                self.assertFalse(r['allow'], '%s %s 不应采集' % (stamp, task))
                self.assertEqual(r['phase'], 'closed_day')

    def test_good_friday_closed_even_though_not_a_federal_holiday(self):
        """免费联邦假日接口会漏掉这天，生产判定必须靠 NYSE 日历。"""
        r = tw.gate('intraday', at('2026-04-03T15:00:00+00:00'), self.cal)
        self.assertFalse(r['allow'])
        r27 = tw.gate('intraday', at('2027-03-26T15:00:00+00:00'), self.cal)
        self.assertFalse(r27['allow'])

    def test_columbus_and_veterans_day_still_trade(self):
        """联邦放假但 NYSE 开市；若误用联邦日历会漏采两个完整交易日。"""
        for stamp in ('2026-10-12T15:00:00+00:00', '2026-11-11T15:00:00+00:00',
                      '2027-11-11T15:00:00+00:00'):
            self.assertTrue(tw.gate('intraday', at(stamp), self.cal)['allow'], stamp)

    def test_beijing_0600_to_2000_is_not_a_collection_window(self):
        """北京 6-20 点 = UTC 22:00~12:00，落在美股收盘后到次日开盘前。"""
        # 2026-11-10 北京 10:00 -> 2026-11-10 02:00 UTC -> 美东 11-09 21:00
        stamp = '2026-11-10T02:00:00+00:00'
        for task in ('intraday', 'closing', 'publish'):
            r = tw.gate(task, at(stamp), self.cal)
            self.assertFalse(r['allow'], '%s 在北京白天应停' % task)

    def test_early_close_moves_the_window_to_1300_et(self):
        """感恩节后一天 13:00 ET 收盘，不能按 16:00 继续采到 21:00 UTC。"""
        self.assertTrue(tw.gate('intraday', at('2026-11-27T17:00:00+00:00'), self.cal)['allow'])
        late = tw.gate('intraday', at('2026-11-27T18:30:00+00:00'), self.cal)
        self.assertFalse(late['allow'])
        self.assertTrue(tw.gate('closing', at('2026-11-27T18:30:00+00:00'), self.cal)['allow'])
        self.assertTrue(tw.gate('intraday', at('2026-12-24T17:00:00+00:00'), self.cal)['allow'])

    def test_dst_is_handled_in_eastern_time_not_utc(self):
        self.assertEqual(self.reviewed.bounds(dt.date(2026, 3, 9))[0].hour, 13)
        self.assertEqual(self.reviewed.bounds(dt.date(2026, 11, 3))[0].hour, 14)
        # 夏令时 21:17 UTC = 17:17 ET，收盘后 77 分钟，收盘关键任务仍应放行
        self.assertTrue(tw.gate('closing', at('2026-07-01T21:17:00+00:00'), self.cal)['allow'])
        self.assertFalse(tw.gate('intraday', at('2026-07-01T21:17:00+00:00'), self.cal)['allow'])

    def test_gate_matches_strategy_calendar_refresh_due_point_by_point(self):
        stamps = [
            '2026-11-09T14:00:00+00:00',   # 冬令时开盘前
            '2026-11-09T14:40:00+00:00',   # 冬令时盘中
            '2026-11-09T21:10:00+00:00',   # 收盘后 10 分钟
            '2026-11-09T21:40:00+00:00',   # 收盘后 40 分钟
            '2026-11-09T02:00:00+00:00',   # 周末/夜间
            '2026-11-26T16:00:00+00:00',   # 感恩节
            '2026-11-27T18:00:00+00:00',   # 半日收盘
            '2026-11-27T19:00:00+00:00',   # 半日收盘后
            '2026-07-01T13:40:00+00:00',   # 夏令时开盘后
            '2026-07-01T20:10:00+00:00',   # 夏令时收盘后
        ]
        for stamp in stamps:
            now = at(stamp)
            self.assertEqual(tw.gate('intraday', now, self.cal)['allow'],
                             self.reviewed.state(now)['refresh_due'], stamp)

    def test_every_collection_cron_stays_outside_beijing_daytime(self):
        """真实调度时刻必须避开北京 6-20 点，而不只是 gate 函数返回 False。"""
        for name in ('quotes.yml', 'db-sync.yml'):
            text = (BASE / '.github' / 'workflows' / name).read_text(encoding='utf-8')
            crons = re.findall(r'cron:\s*"([^"]+)"', text)
            self.assertTrue(crons, name + ' 缺少 schedule')
            for cron in crons:
                hour_field = cron.split()[1]
                hours = []
                for part in hour_field.split(','):
                    if '-' in part:
                        lo, hi = part.split('-')
                        hours += list(range(int(lo), int(hi) + 1))
                    elif part.isdigit():
                        hours.append(int(part))
                    elif part.startswith('*/'):
                        continue          # 分钟位，小时位在下一字段
                for h in hours:
                    beijing = (h + 8) % 24
                    self.assertFalse(6 <= beijing < 20,
                                     '%s cron %s 落在北京 %d 点' % (name, cron, beijing))

    def test_fail_open_on_unavailable_or_tampered_calendar(self):
        """日历坏了只能多采一次，绝不能造成行情缺口。"""
        beyond = tw.gate('intraday', at('2029-01-02T15:00:00+00:00'), self.cal)
        self.assertTrue(beyond['allow'])
        self.assertEqual(beyond['phase'], 'calendar_unavailable')
        broken = tw.gate('intraday', at('2026-11-09T15:00:00+00:00'),
                         {'market': 'NOT_XNYS'})
        self.assertTrue(broken['allow'])
        with patch.object(tw, 'load_calendar', side_effect=OSError('missing')):
            self.assertTrue(tw.gate('intraday', at('2026-11-09T15:00:00+00:00'))['allow'])

    def test_manual_force_and_heartbeat_are_never_blocked(self):
        self.assertTrue(tw.gate('intraday', at('2026-11-26T16:00:00+00:00'), self.cal,
                               event_name='push')['allow'])
        self.assertTrue(tw.gate('intraday', at('2026-11-26T16:00:00+00:00'), self.cal,
                               event_name='workflow_dispatch')['allow'])
        self.assertTrue(tw.gate('heartbeat', at('2026-11-08T05:00:00+00:00'), self.cal)['allow'])
        with patch.dict('os.environ', {'TRADE_FORCE_COLLECT': '1'}):
            self.assertTrue(tw.gate('intraday', at('2026-11-26T16:00:00+00:00'), self.cal)['allow'])

    def test_gate_result_contract(self):
        r = tw.gate('intraday', at('2026-11-09T15:00:00+00:00'), self.cal)
        for key in ('allow', 'reason', 'task', 'session', 'phase', 'calendar_sha256'):
            self.assertIn(key, r)
        self.assertEqual(len(r['calendar_sha256']), 64)
        json.dumps(r, ensure_ascii=False)   # 必须是可序列化的纯数据


if __name__ == '__main__':
    unittest.main()
