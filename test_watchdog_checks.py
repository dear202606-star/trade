import unittest
import datetime as dt
import json
import hashlib

import e2e_check
from watchdog_checks import (quality_problems, quote_problems, history_problems,
                             published_dependency_problems, settlement_gap)
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
    def test_rehashed_snapshot_cannot_hide_invalid_or_stale_components(self):
        cases=[({'p':0,'as_of':'2026-11-03T15:55:00Z'},'价格无效'),
               ({'p':True,'as_of':'2026-11-03T15:55:00Z'},'价格无效'),
               ({'p':100},'时间无效'),
               ({'p':100,'as_of':'2026-11-02T21:00:00Z'},'场次不一致'),
               ({'p':100,'as_of':'2026-11-03T15:00:00Z'},'未达到应有时点'),
               ({'p':100,'as_of':'2026-11-03T17:00:00Z'},'时间来自未来')]
        now=dt.datetime(2026,11,3,16,tzinfo=dt.timezone.utc)
        for quote,expected in cases:
            row,event,calendar=self.snapshot('2026-11-03T15:55:00Z','2026-11-03','2026-11-02','2026-11-03T15:59:00Z')
            payload=row['payload'];payload.pop('version')
            payload['quotes']['A']=quote;payload['count']=payload['universe']=2
            sha=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            payload['version']=row['evidence_sha256']=event['metrics']['stored_evidence_sha256']=sha
            with self.subTest(quote=quote):
                self.assertTrue(any(expected in p for p in quote_problems(row,event,calendar,now)))

    def snapshot(self,source,session,previous,worker_time):
        calendar=Calendar(CALENDAR,fingerprint(CALENDAR))
        payload={'ok':True,'ts':source,'session':session,'previous_session':previous,
                 'fresh_until':calendar.source_deadline(dt.datetime.fromisoformat(source.replace('Z','+00:00'))).isoformat(),
                 'calendar_sha256':calendar.sha,'count':1,'universe':1,'quotes':{'SPY':{'p':100,'as_of':source}}}
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


class PublishedDependencyTests(unittest.TestCase):
    def test_valid_previous_calculation_expires_without_a_new_quote_version(self):
        now=dt.datetime(2026,11,4,16,tzinfo=dt.timezone.utc)
        quotes={'version':'new','session':'2026-11-04','previous_session':'2026-11-03','ts':'2026-11-04T16:00:00Z'}
        equity={'last_session':'20261103','quote_version':'old','source_as_of':'2026-11-04T15:55:00Z','fresh_until':'2026-11-04T16:15:00Z'}
        preview={'quote_version':'old','session':quotes['session'],'reference_version':'ref','top':[{}, {}, {}],
                 'as_of_ts':equity['source_as_of'],'fresh_until':equity['fresh_until']}
        self.assertEqual(published_dependency_problems(equity,preview,quotes,now),[])
        self.assertEqual(len(published_dependency_problems(equity,preview,quotes,now+dt.timedelta(minutes=15))),2)

    def test_quote_success_does_not_hide_stalled_calculations(self):
        quotes={'version':'new','session':'2026-11-04','previous_session':'2026-11-03'}
        preview={'quote_version':'new','session':quotes['session'],'reference_version':'reviewed','top':[{}, {}, {}]}
        self.assertEqual(published_dependency_problems({'last_session':'20261103','quote_version':'new'},preview,quotes),[])
        self.assertEqual(len(published_dependency_problems({'last_session':'20261102','quote_version':'new'},dict(preview,quote_version='old'),quotes)),2)
        self.assertTrue(published_dependency_problems(None,None,quotes))
        with self.assertRaises(ValueError):published_dependency_problems({}, {}, {})

    # 盘中：净值与次年预览是收盘产物，本场还没收盘，本就不该重算。
    # 2026-11-21 事故——预览连着两场没产出（停在 11-18，行情已到 11-20），
    # 而调用方因为「盘中按版本判必然误报」把整条依赖校验丢掉，看门狗全绿。
    INTRADAY_QUOTES={'version':'live','session':'2026-11-04','previous_session':'2026-11-03',
                     'ts':'2026-11-04T20:00:00Z','intraday':True}
    MID_SESSION=dt.datetime(2026,11,4,20,tzinfo=dt.timezone.utc)

    def closed_products(self, session):
        """上一收盘场（或更旧）的净值与预览：版本落后、时效已过，盘中都是常态。"""
        dashed=session.replace('-','')
        stale={'quote_version':'old','fresh_until':'2026-11-04T14:30:00Z'}
        as_of='2026-11-%sT02:00:00Z'%session[-2:]
        return (dict(stale,last_session=dashed,source_as_of=as_of),
                dict(stale,session=session,as_of_ts=as_of,reference_version='ref',top=[{}, {}, {}]))

    def test_intraday_judges_close_products_by_session_not_by_version(self):
        equity,preview=self.closed_products('2026-11-03')
        self.assertEqual(published_dependency_problems(equity,preview,self.INTRADAY_QUOTES,self.MID_SESSION),[])

    def test_intraday_cannot_hide_a_close_that_never_produced_anything(self):
        equity,preview=self.closed_products('2026-11-02')
        self.assertEqual(published_dependency_problems(equity,preview,self.INTRADAY_QUOTES,self.MID_SESSION),
                         ['收益基准落后于可衔接报价场次','次年预览未跟随当前报价版本完成核验'])

    def test_intraday_is_not_a_free_pass_for_broken_structure(self):
        equity,preview=self.closed_products('2026-11-03')
        self.assertEqual(published_dependency_problems(equity,dict(preview,top=[{}]),
                                                       self.INTRADAY_QUOTES,self.MID_SESSION),
                         ['次年预览未跟随当前报价版本完成核验'])

    def test_intraday_without_a_previous_session_cannot_be_vacated(self):
        with self.assertRaises(ValueError):
            published_dependency_problems(*self.closed_products('2026-11-03'),
                dict(self.INTRADAY_QUOTES,previous_session=None),self.MID_SESSION)


class SettlementGapTests(unittest.TestCase):
    """新鲜度按「日历要求的收盘场」判，不按墙钟。两种误报都是现场踩出来的。"""

    def test_an_old_but_matching_close_is_not_stale(self):
        # 周五 20:40 的收盘快照挂到周日已经 37 小时：e2e 原来的 age<=26
        # 在周末必红，而恒红的门禁和恒绿一样没有信息量。
        self.assertIsNone(settlement_gap("行情停更", "2026-11-20", "2026-11-20"))

    def test_a_live_session_row_is_not_a_missing_previous_close(self):
        # 2026-11-20T19:57 UTC 看门狗现场：行情已是当日盘中版，旧判据只看
        # intraday is True，于是喊「11-19 收盘数据缺失」并白派一次收盘补采。
        self.assertIsNone(settlement_gap("行情停更", "2026-11-20", "2026-11-19", intraday=True))

    def test_the_due_session_must_have_landed_as_a_close(self):
        self.assertEqual(
            settlement_gap("行情停更", "2026-11-20", "2026-11-20", intraday=True),
            "行情停更：2026-11-20 收盘快照未落地（仍是盘中版）")

    def test_a_product_older_than_the_due_session_is_stale(self):
        # 预览停在 11-18、应收盘场已到 11-20：文案必须带「预览停更」，
        # selfheal 靠它把重算派发到 live-strategy.yml。
        message = settlement_gap("预览停更", "2026-11-18", "2026-11-20")
        self.assertIn("预览停更", message)
        self.assertIn("缺 2026-11-20 收盘场", message)

    def test_without_a_due_session_the_rule_stays_silent(self):
        # 日历不可用时返回 None 不等于放行：调用方必须自己退回墙钟兜底
        # 并把判据打印出来（见 DueSessionTests）。
        self.assertIsNone(settlement_gap("行情停更", "2026-11-20", ""))


class DueSessionTests(unittest.TestCase):
    """用仓库里那份已复核日历跑真日期，判据边界不靠假数据支撑。"""

    def test_weekend_reports_the_last_real_session(self):
        self.assertEqual(e2e_check.due_session(dt.datetime(2026, 11, 22, 9, tzinfo=dt.timezone.utc)),
                         ("2026-11-20", ""))

    def test_a_session_inside_the_settlement_grace_is_not_yet_due(self):
        # 2026-11-20 收盘 21:00 UTC，宽限到 22:30；22:00 时收盘补采还在路上，
        # 这时判「停更」就是拿门禁去追一个还没来得及到的产物。
        due, note = e2e_check.due_session(dt.datetime(2026, 11, 20, 22, tzinfo=dt.timezone.utc))
        self.assertEqual(due, "")
        self.assertIn("结算宽限", note)

    def test_midweek_afternoon_still_demands_yesterday(self):
        self.assertEqual(e2e_check.due_session(dt.datetime(2026, 11, 20, 15, tzinfo=dt.timezone.utc)),
                         ("2026-11-19", ""))

    def test_an_unusable_calendar_says_so_instead_of_passing(self):
        due, note = e2e_check.due_session(dt.datetime(2031, 6, 4, 15, tzinfo=dt.timezone.utc))
        self.assertEqual(due, "")
        self.assertTrue(note)


if __name__ == '__main__':
    unittest.main()
