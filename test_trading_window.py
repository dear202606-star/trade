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

    def test_beijing_regular_close_refresh_time_varies_with_dst(self):
        for stamp, hour in (("2026-07-01T20:45:00+00:00", 4), ("2026-11-10T21:45:00+00:00", 5)):
            now = at(stamp)
            self.assertTrue(tw.closing_collection_gate(now, self.cal)["allow"])
            local = now.astimezone(dt.timezone(dt.timedelta(hours=8)))
            self.assertEqual((local.hour, local.minute), (hour, 45))

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

    def test_manual_dispatch_force_and_heartbeat_are_never_blocked(self):
        # 2026-11-10：push 不再无条件放行，与 schedule 同等按日历裁决。
        # 改代码只跑回归；休市日、盘前绝不误采集（SPY 前收字段会被改写）。
        holiday = at('2026-11-26T16:00:00+00:00')
        self.assertFalse(tw.gate('intraday', holiday, self.cal,
                                 event_name='push')['allow'])
        self.assertTrue(tw.gate('intraday', holiday, self.cal,
                                event_name='workflow_dispatch')['allow'])
        # 交易时段内的 push 仍按窗口正常放行
        self.assertTrue(tw.gate('intraday', at('2026-11-09T15:00:00+00:00'), self.cal,
                                event_name='push')['allow'])
        self.assertTrue(tw.gate('heartbeat', at('2026-11-08T05:00:00+00:00'), self.cal)['allow'])
        with patch.dict('os.environ', {'TRADE_FORCE_COLLECT': '1'}):
            self.assertTrue(tw.gate('intraday', holiday, self.cal)['allow'])

    def test_gate_result_contract(self):
        r = tw.gate('intraday', at('2026-11-09T15:00:00+00:00'), self.cal)
        for key in ('allow', 'reason', 'task', 'session', 'phase', 'calendar_sha256'):
            self.assertIn(key, r)
        self.assertEqual(len(r['calendar_sha256']), 64)
        json.dumps(r, ensure_ascii=False)   # 必须是可序列化的纯数据

    def test_publish_gate_never_skips_the_deploy_chain(self):
        """回归：calendar_gate 曾写 `if: github.event_name == 'schedule'`，
        push 时被跳过后沿 needs 链把 deploy 一起跳过 —— build 成功但 Pages 没部署。
        门禁 job 必须始终运行，由脚本对非 schedule 事件自行放行。
        """
        text = (BASE / '.github' / 'workflows' / 'ui-publish.yml').read_text(encoding='utf-8')
        lines = text.splitlines()
        start = next(i for i, line in enumerate(lines) if line.rstrip() == 'jobs:')
        jobs, current = {}, None
        for line in lines[start + 1:]:
            m = re.match(r'^  ([A-Za-z_][A-Za-z0-9_]*):\s*$', line)
            if m:
                current = m.group(1)
                jobs[current] = []
            elif current is not None:
                jobs[current].append(line)
        body = {name: '\n'.join(rows) for name, rows in jobs.items()}

        self.assertIn('calendar_gate', body)
        self.assertIn('deploy', body)
        # 1) 门禁 job 不能有 job 级 if：被跳过的 job 会连带跳过所有下游
        self.assertIsNone(re.search(r'^    if:', body['calendar_gate'], re.M),
                          'calendar_gate 必须始终运行，不能有 job 级 if')
        self.assertIn('EVENT_NAME: ${{ github.event_name }}', body['calendar_gate'])
        self.assertIn('--gate', body['calendar_gate'])
        self.assertIn('allow: ${{ steps.gate.outputs.allow }}', body['calendar_gate'])

        # 2) 遍历 deploy 的整条 needs 链，禁止「只在 schedule 运行」的 job 级 if
        chain, pending = set(), ['deploy']
        while pending:
            name = pending.pop()
            if name in chain or name not in body:
                continue
            chain.add(name)
            needs = re.search(r'^    needs:\s*(.+)$', body[name], re.M)
            if needs:
                raw = needs.group(1).strip().strip("'\"")
                if raw.startswith('['):
                    pending += [x.strip().strip("'\"")
                                for x in raw.strip('[]').split(',') if x.strip()]
                else:
                    pending.append(raw)
            job_if = re.search(r'^    if:\s*(.+)$', body[name], re.M)
            if job_if:
                cond = job_if.group(1)
                self.assertNotIn("github.event_name == 'schedule'", cond,
                                 '%s 的 job 级 if 会在 push 被跳过，导致 deploy 掉链' % name)
                self.assertIn('!cancelled()', cond,
                              '%s 位于 deploy 依赖链，if 必须带 !cancelled() 兜底' % name)
        self.assertEqual(chain, {'deploy', 'build', 'calendar_gate'})


if __name__ == '__main__':
    unittest.main()
