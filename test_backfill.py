"""延迟补采与自愈闭环回归（2026-11-10）。

背景：GitHub scheduled 事件把 21:50 UTC 的收盘任务拖到次日 03:49 UTC，
trading_window 按「实际执行时刻」判超窗跳过；看门狗虽派发了自愈补跑，
但 workflow_dispatch 没有 --critical，被 egress freeze 再次跳过，页面数据
连续 4 天停更。本文件锁死修复后的四段链路，防止任一环回退。
"""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import os
import unittest
from unittest.mock import patch

import live_quotes
import selfheal
import trading_window as tw
import watchdog_site_check

UTC = dt.timezone.utc
CAL = tw.load_calendar()


def at(text):
    return dt.datetime.fromisoformat(text)


class TradingWindowBackfillTests(unittest.TestCase):
    def test_opening_task_uses_reviewed_calendar_open_time(self):
        # EST 开盘 14:30 UTC；+30min = 15:00 UTC
        r = tw.gate("opening", at("2026-11-09T15:00:00+00:00"), CAL)
        self.assertTrue(r["allow"])
        self.assertEqual(r["phase"], "opening")
        # 盘前 / 收盘后都不做开盘快照
        self.assertFalse(tw.gate("opening", at("2026-11-09T13:00:00+00:00"), CAL)["allow"])
        self.assertFalse(tw.gate("opening", at("2026-11-09T22:00:00+00:00"), CAL)["allow"])

    def test_opening_delayed_but_still_intraday_is_allowed(self):
        r = tw.gate("opening", at("2026-11-09T18:00:00+00:00"), CAL)
        self.assertTrue(r["allow"])
        self.assertEqual(r["phase"], "late_opening")

    def test_opening_on_exchange_holiday_is_skipped(self):
        self.assertFalse(tw.gate("opening", at("2026-11-26T15:00:00+00:00"), CAL)["allow"])

    def test_closing_late_backfill_requires_explicit_late_ok(self):
        late = at("2026-11-10T03:49:00+00:00")   # 收盘后 409 分钟
        self.assertFalse(tw.gate("closing", late, CAL)["allow"])
        r = tw.gate("closing", late, CAL, late_ok=True)
        self.assertTrue(r["allow"])
        self.assertEqual(r["phase"], "late_backfill")
        self.assertEqual(r["session"], "2026-11-09")
        self.assertEqual(r["as_of"], "2026-11-09T21:17:00+00:00")

    def test_closing_beyond_twelve_hours_is_not_backfilled(self):
        self.assertFalse(tw.gate(
            "closing", at("2026-11-10T15:00:00+00:00"), CAL, late_ok=True)["allow"])

    def test_latest_closed_session_skips_weekend_and_holidays(self):
        self.assertEqual(tw.latest_closed_session(at("2026-11-08T12:00:00+00:00"), CAL)["session"],
                         "2026-11-06")   # 周六 → 最近收盘是周五
        self.assertEqual(tw.latest_closed_session(at("2026-11-26T22:00:00+00:00"), CAL)["session"],
                         "2026-11-25")   # 感恩节休市 → 前一日

    def test_closing_as_of_points_at_target_session(self):
        self.assertEqual(tw.closing_as_of(at("2026-11-10T13:00:00+00:00"), CAL),
                         "2026-11-09T21:17:00+00:00")

    def test_gate_output_contains_task_and_as_of(self):
        out = os.path.join(os.path.dirname(__file__), "_gate_output.tmp")
        try:
            with patch.dict(os.environ, {"EVENT_NAME": "schedule", "GITHUB_OUTPUT": out}):
                with patch.object(tw.sys, "argv", ["trading_window.py", "--task", "closing",
                                                   "--late-ok", "--at",
                                                   "2026-11-10T03:49:00+00:00", "--gate"]):
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(tw.main(), 0)
            body = open(out, encoding="utf-8").read()
            self.assertIn("allow=true", body)
            self.assertIn("task=closing", body)
            self.assertIn("as_of=2026-11-09T21:17:00+00:00", body)
            self.assertIn("critical=true", body)
        finally:
            if os.path.exists(out):
                os.remove(out)


class LogicalTimeTests(unittest.TestCase):
    NOW = at("2026-11-10T13:20:00+00:00")

    def test_default_is_real_time(self):
        self.assertEqual(live_quotes.logical_time(None, self.NOW), self.NOW)

    def test_valid_backfill(self):
        self.assertEqual(live_quotes.logical_time("2026-11-09T21:17:00+00:00", self.NOW),
                         at("2026-11-09T21:17:00+00:00"))

    def test_rejects_future_too_old_preopen_and_naive(self):
        for spec in ("2026-11-11T21:17:00+00:00", "2026-11-01T21:17:00+00:00",
                     "2026-11-10T13:00:00+00:00", "2026-11-09T21:17:00"):
            with self.subTest(spec=spec):
                with self.assertRaises(ValueError):
                    live_quotes.logical_time(spec, self.NOW)

    def test_rejects_a_holiday_session(self):
        with self.assertRaises(ValueError):
            live_quotes.logical_time("2026-11-26T21:17:00+00:00",
                                    at("2026-11-27T13:00:00+00:00"))

    def test_backfill_window_is_capped_at_20_hours(self):
        # 在线挂牌只保留最近一个场次；更旧的场次字段已被改写，补不回来
        self.assertTrue(live_quotes.logical_time(
            "2026-11-09T21:17:00+00:00", at("2026-11-10T13:20:00+00:00")))
        with self.assertRaises(ValueError):
            live_quotes.logical_time("2026-11-09T21:17:00+00:00",
                                    at("2026-11-10T18:00:00+00:00"))


class SelfHealDispatchTests(unittest.TestCase):
    def test_after_close_dispatch_carries_critical_and_as_of(self):
        # 11-10 22:00 UTC = 17:00 EST，当日收盘后 60 分钟
        args = selfheal.dispatch_args("quotes.yml", at("2026-11-10T22:00:00+00:00"))
        self.assertIn("critical=true", args)
        self.assertTrue(any(a.startswith("as_of=2026-11-10T21:17") for a in args),
                        "收盘后补跑必须带当日收盘逻辑时间: %r" % args)

    def test_intraday_dispatch_has_no_as_of(self):
        # 盘中若仍带上一场次的 as_of，逻辑时间被固定到昨日，而在线报价
        # 已是今日时间戳 → source_time 判 Future source quote，必然失败。
        args = selfheal.dispatch_args("quotes.yml", at("2026-11-10T15:00:00+00:00"))
        self.assertEqual(args, ["-f", "critical=true"])

    def test_preopen_does_not_dispatch(self):
        # 盘前 SPY 前收价被写成现价、涨跌 0，放行会把假报价写进生产
        self.assertIsNone(selfheal.dispatch_args(
            "quotes.yml", at("2026-11-10T13:20:00+00:00")))

    def test_other_workflows_get_no_extra_args(self):
        self.assertEqual(selfheal.dispatch_args("ui-publish.yml", self_heal_now()), [])

    def test_expired_session_skips_useless_backfill_dispatch(self):
        # 周六 18:00 UTC：最近已收盘场次是周五，已超 20 小时窗口
        self.assertIsNone(selfheal.dispatch_args(
            "quotes.yml", at("2026-11-14T18:00:00+00:00")))

    def test_fresh_session_still_dispatches(self):
        self.assertIsNotNone(selfheal.dispatch_args(
            "quotes.yml", at("2026-11-10T15:00:00+00:00")))

    def test_cooldown_only_counts_dispatch_runs(self):
        with patch.object(selfheal, "gh", return_value="[]") as gh:
            self.assertFalse(selfheal.recently_active("quotes.yml"))
            argv = gh.call_args[0]
            self.assertIn("--event", argv)
            self.assertIn("workflow_dispatch", argv)

    def test_dry_run_reports_args_without_dispatching(self):
        path = os.path.join(os.path.dirname(__file__), "_problems.tmp")
        stub = ["-f", "critical=true", "-f", "as_of=2026-11-10T21:17:00+00:00"]
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("行情停更 84.6 小时（阈值 72）\n")
            with patch.object(selfheal, "gh", return_value="[]"), \
                 patch.object(selfheal, "dispatch_args", return_value=stub), \
                 patch.object(selfheal.sys, "argv",
                              ["selfheal.py", "--problems", path, "--dry-run"]), \
                 contextlib.redirect_stdout(io.StringIO()) as captured:
                selfheal.main()
            report = json.loads(captured.getvalue())
            self.assertIn("critical=true", report["decisions"][0]["args"])
            self.assertTrue(any(a.startswith("as_of=")
                                for a in report["decisions"][0]["args"]))
        finally:
            if os.path.exists(path):
                os.remove(path)


def self_heal_now():
    return dt.datetime.now(UTC)


class WatchdogEarlyDetectionTests(unittest.TestCase):
    def _doc(self, session):
        q = {"ts": "2026-11-06T21:00:00Z", "session": session, "ok": True,
             "quotes": {"SPY": {"as_of": "2026-11-06T21:00:00+00:00"}}}
        datasets = {k: {"ok": True} for k in watchdog_site_check.REQUIRED}
        datasets["quotes"] = q
        return {"schema": 1, "source": "turso-http",
                "generated_at": "2026-11-10T12:00:00+00:00",
                "datasets": datasets, "meta": {}}

    def test_missing_last_close_is_reported_within_90_minutes(self):
        now = at("2026-11-10T13:00:00+00:00")
        problems = watchdog_site_check.snapshot_problems(self._doc("2026-11-06"), now)
        self.assertTrue(any("2026-11-09 收盘数据缺失" in p for p in problems),
                        "未及早发现停更: %r" % problems)
        self.assertTrue(any("行情停更" in p for p in problems),
                        "自愈关键词未命中: %r" % problems)

    def test_matching_session_is_not_reported(self):
        now = at("2026-11-10T13:00:00+00:00")
        problems = watchdog_site_check.snapshot_problems(self._doc("2026-11-09"), now)
        self.assertFalse(any("收盘数据缺失" in p for p in problems),
                         "数据已到位仍误报: %r" % problems)

    def test_missing_intraday_snapshot_is_detected_early(self):
        # GitHub 15:00 UTC 的 opening cron 实测整批不触发；若页面仍停在昨日，
        # 看门狗必须在盘中就发现并让 selfheal 派发实时补采。
        problems = watchdog_site_check.snapshot_problems(
            self._doc("2026-11-06"), at("2026-11-10T15:10:00+00:00"))
        self.assertTrue(any("盘中快照缺失" in p and "行情停更" in p
                            for p in problems),
                        "盘中停更未早发现: %r" % problems)

    def test_current_intraday_snapshot_is_not_flagged(self):
        problems = watchdog_site_check.snapshot_problems(
            self._doc("2026-11-10"), at("2026-11-10T15:10:00+00:00"))
        self.assertFalse(any("盘中快照缺失" in p for p in problems),
                         "当日快照已到位仍误报: %r" % problems)

    def test_opening_grace_period_is_not_flagged(self):
        # 开盘后 35 分钟宽限，避免采集排队延迟被误判成故障
        problems = watchdog_site_check.snapshot_problems(
            self._doc("2026-11-06"), at("2026-11-10T14:35:00+00:00"))
        self.assertFalse(any("盘中快照缺失" in p for p in problems),
                         "开盘宽限期内误报: %r" % problems)

    def _dependency_doc(self, intraday):
        q = {"ts": "2026-11-10T14:33:00Z", "session": "2026-11-10",
             "previous_session": "2026-11-09", "version": "v-test",
             "ok": True, "intraday": intraday,
             "quotes": {"SPY": {"as_of": "2026-11-10T14:33:00+00:00"}}}
        datasets = {k: {"ok": True} for k in watchdog_site_check.REQUIRED}
        datasets.update({"quotes": q, "equity": {}, "b/equity": {},
                         "preview": {}, "b/preview": {}})
        meta = {k: {"updated_at": "2026-11-10T14:33:00+00:00"}
                for k in ("equity", "b/equity", "preview", "b/preview")}
        return {"schema": 1, "source": "turso-http",
                "generated_at": "2026-11-10T14:33:00+00:00",
                "datasets": datasets, "meta": meta}

    def test_intraday_snapshot_does_not_flag_equity_version_gap(self):
        # 盘中 run-guard 明确跳过净值/预览，版本落后是设计而非故障
        problems = watchdog_site_check.snapshot_problems(
            self._dependency_doc(True), at("2026-11-10T15:00:00+00:00"))
        self.assertFalse(any("收益基准落后" in p or "次年预览未跟随" in p
                             for p in problems),
                         "盘中误判净值停更会触发告警+无效自愈空转: %r" % problems)

    def test_closed_snapshot_still_flags_equity_version_gap(self):
        # 收盘后净值没跟上就是真故障，必须保留告警与自愈能力
        problems = watchdog_site_check.snapshot_problems(
            self._dependency_doc(False), at("2026-11-10T22:00:00+00:00"))
        self.assertTrue(any("收益基准落后" in p for p in problems),
                        "收盘后漏报净值未更新: %r" % problems)
        self.assertTrue(any("次年预览未跟随" in p for p in problems),
                        "收盘后漏报预览未更新: %r" % problems)


class WorkflowContractTests(unittest.TestCase):
    """防止工作流层面的三处断点回退（纯文本解析，不依赖 PyYAML）。"""

    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        root = Path(__file__).resolve().parent / ".github" / "workflows"
        cls.quotes = (root / "quotes.yml").read_text(encoding="utf-8")
        cls.sync = (root / "db-sync.yml").read_text(encoding="utf-8")

    def test_dispatch_egress_is_not_blocked_by_freeze(self):
        self.assertIn("TRADE_FORCE_BUDGET", self.quotes)
        self.assertIn("--critical", self.quotes)
        self.assertIn("steps.gate.outputs.critical", self.quotes)

    def test_quotes_can_pass_as_of_to_collector(self):
        self.assertIn("--as-of", self.quotes)
        self.assertIn("steps.gate.outputs.as_of || github.event.inputs.as_of", self.quotes)

    def test_quotes_dispatches_sync_after_closing_collection(self):
        self.assertIn("gh workflow run db-sync.yml", self.quotes)
        self.assertIn("actions: write", self.quotes)

    def test_opening_collection_also_pushes_turso_immediately(self):
        # 访客读的是 Turso：开盘那次不能只在主库落地等下一次定时同步
        self.assertIn("steps.gate.outputs.task == 'opening'", self.quotes)
        self.assertIn("db-sync.yml -f targets=turso || true", self.quotes)
        self.assertIn("db-sync.yml -f targets=turso,neon || true", self.quotes)

    def test_selfheal_backfill_also_pushes_turso_without_waking_neon(self):
        # 自愈补采成功后若不同步，闭环断在「主库已更新、Turso 仍旧」，
        # 页面继续告警；但盘中补采不能误唤醒 Neon（每日一次额度）。
        self.assertIn("(github.event_name == 'workflow_dispatch' ||", self.quotes)
        self.assertIn('EVENT: ${{ github.event_name }}', self.quotes)
        self.assertIn('[ "$TASK" = "closing" ] && [ "$EVENT" = "schedule" ]',
                      self.quotes)

    def test_manual_dispatch_without_as_of_is_calendar_gated(self):
        # 带 as_of 的补采放行；不带 as_of 的实时验证必须借日历裁决，
        # 否则盘前 p=pc / 涨跌 0 的假报价会被写进生产。
        self.assertIn("AS_OF_INPUT: ${{ github.event.inputs.as_of }}", self.quotes)
        self.assertIn('if [ -n "$AS_OF_INPUT" ]', self.quotes)
        self.assertIn('GATE_EVENT="schedule"', self.quotes)
        self.assertIn('EVENT_NAME="$GATE_EVENT" python3 trading_window.py', self.quotes)

    def test_opening_and_closing_are_explicit_critical_crons(self):
        for cron in ('"0 14 * * 1-5"', '"0 15 * * 1-5"',
                     '"17 21 * * 1-5"', '"50 21 * * 1-5"'):
            self.assertIn(cron, self.quotes)

    def test_push_does_not_bypass_the_trading_window(self):
        # push 只跑回归；盘前无条件采集会因 SPY 前收字段被改写而整轮失败
        self.assertIn('if [ "$EVENT_NAME" = "workflow_dispatch" ]', self.quotes)
        self.assertNotIn('[ "$EVENT_NAME" = "push" ]', self.quotes)

    def test_collection_has_enough_timeout_and_one_retry(self):
        # 4 分钟 job 超时实测把 500+ 标的采集腰斩，两次补采都被 canceled
        self.assertIn("timeout-minutes: 20", self.quotes)
        self.assertIn("timeout-minutes: 14", self.quotes)
        self.assertIn("for attempt in 1 2", self.quotes)

    def test_sync_close_cron_runs_after_collection(self):
        # 21:55 UTC > 21:17 / 21:50 两次收盘采集，否则同步的永远是旧数据
        self.assertIn('"55 21 * * 1-5"', self.sync)
        self.assertNotIn('"10 21 * * 1-5"', self.sync)


if __name__ == "__main__":
    unittest.main()
