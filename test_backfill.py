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
        self.assertTrue(any(a.startswith("as_of=2026-11-10T21:45") for a in args),
                        "收盘后补跑必须带当日收盘逻辑时间: %r" % args)

    def test_no_dispatch_during_market_hours(self):
        args = selfheal.dispatch_args("quotes.yml", at("2026-11-10T15:00:00+00:00"))
        self.assertIsNone(args)

    def test_preopen_repair_is_bound_to_yesterdays_close(self):
        args = selfheal.dispatch_args("quotes.yml", at("2026-11-10T13:20:00+00:00"))
        self.assertIn("as_of=2026-11-09T21:45:00+00:00", args)

    def test_other_workflows_get_no_extra_args(self):
        self.assertEqual(selfheal.dispatch_args("ui-publish.yml", self_heal_now()), [])

    def test_expired_session_skips_useless_backfill_dispatch(self):
        # 周六 18:00 UTC：最近已收盘场次是周五，已超 20 小时窗口
        self.assertIsNone(selfheal.dispatch_args(
            "quotes.yml", at("2026-11-14T18:00:00+00:00")))

    def test_fresh_session_still_dispatches(self):
        self.assertIsNotNone(selfheal.dispatch_args(
            "quotes.yml", at("2026-11-10T22:00:00+00:00")))

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
    def _doc(self, session, ts="2026-11-06T21:00:00Z"):
        q = {"ts": ts, "session": session, "ok": True,
             "quotes": {"SPY": {"as_of": "2026-11-06T21:00:00+00:00"}}}
        datasets = {k: {"ok": True} for k in watchdog_site_check.REQUIRED}
        datasets["quotes"] = q
        return {"schema": 1, "source": "turso-http",
                "generated_at": "2026-11-10T12:00:00+00:00",
                "datasets": datasets, "meta": {}}

    def test_missing_last_close_is_reported_within_90_minutes(self):
        now = at("2026-11-10T13:00:00+00:00")
        problems = watchdog_site_check.snapshot_problems(self._doc("2026-11-06"), now)
        self.assertTrue(any("缺 2026-11-09 收盘场" in p for p in problems),
                        "未及早发现停更: %r" % problems)
        self.assertTrue(any("行情停更" in p for p in problems),
                        "自愈关键词未命中: %r" % problems)

    def test_matching_session_is_not_reported(self):
        now = at("2026-11-10T13:00:00+00:00")
        problems = watchdog_site_check.snapshot_problems(
            self._doc("2026-11-09", "2026-11-09T21:00:00Z"), now)
        self.assertFalse(any("行情停更" in p for p in problems),
                         "数据已到位仍误报: %r" % problems)

    def test_no_intraday_gap_alert_for_latest_settled_close(self):
        problems = watchdog_site_check.snapshot_problems(
            self._doc("2026-11-09", "2026-11-09T21:00:00Z"), at("2026-11-10T15:10:00+00:00"))
        self.assertFalse(any("行情停更" in p for p in problems),
                         "上一收盘场已结算仍误报: %r" % problems)

    def test_current_intraday_snapshot_is_not_flagged(self):
        # 2026-11-20 19:57 UTC 实测：行情已是当日盘中版，旧判据却喊
        # 「11-19 收盘数据缺失」并白派一次收盘补采。
        problems = watchdog_site_check.snapshot_problems(
            self._doc("2026-11-10", "2026-11-10T14:33:00Z"), at("2026-11-10T15:10:00+00:00"))
        self.assertFalse(any("行情停更" in p for p in problems),
                         "当日快照已到位仍误报: %r" % problems)

    def test_opening_grace_period_is_not_flagged(self):
        # 开盘后 35 分钟宽限，避免采集排队延迟被误判成故障
        problems = watchdog_site_check.snapshot_problems(
            self._doc("2026-11-10", "2026-11-10T14:33:00Z"), at("2026-11-10T14:35:00+00:00"))
        self.assertFalse(any("行情停更" in p for p in problems),
                         "开盘宽限期内误报: %r" % problems)

    def _dependency_doc(self, intraday, last_closed=True):
        q = {"ts": "2026-11-10T14:33:00Z", "session": "2026-11-10",
             "previous_session": "2026-11-09", "version": "v-test",
             "ok": True, "intraday": intraday,
             "quotes": {"SPY": {"as_of": "2026-11-10T14:33:00+00:00"}}}
        datasets = {k: {"ok": True} for k in watchdog_site_check.REQUIRED}
        # 收盘产物的真实形状：上一已收盘场次的结果，版本落后于盘中报价、
        # fresh_until 按下一开盘设定。原先这里图省事填 {}（＝根本没有数据），
        # 于是「盘中不报版本差」被写成了「盘中什么都不看」，预览连停两场也照样绿。
        products = ({
            "last_session": "20261109", "session": "2026-11-09",
            "quote_version": "v-old", "reference_version": "ref",
            "top": [{}, {}, {}],
            "source_as_of": "2026-11-09T21:30:00Z",
            "as_of_ts": "2026-11-09T21:30:00Z",
            "fresh_until": "2026-11-10T14:30:00Z"} if last_closed else {})
        for key in ("equity", "b/equity", "preview", "b/preview"):
            datasets[key] = dict(products)
        datasets["quotes"] = q
        meta = {k: {"updated_at": "2026-11-10T14:33:00+00:00"}
                for k in ("equity", "b/equity", "preview", "b/preview")}
        return {"schema": 1, "source": "turso-http",
                "generated_at": "2026-11-10T14:33:00+00:00",
                "datasets": datasets, "meta": meta}

    def test_intraday_snapshot_does_not_flag_equity_version_gap(self):
        # 盘中 run-guard 明确跳过净值/预览，版本落后是设计而非故障；
        # 判据换成「场次是否等于上一收盘场」之后，这条依然必须成立。
        problems = watchdog_site_check.snapshot_problems(
            self._dependency_doc(True), at("2026-11-10T15:00:00+00:00"))
        self.assertFalse(any("收益基准落后" in p or "次年预览未跟随" in p
                             for p in problems),
                         "盘中误判净值停更会触发告警+无效自愈空转: %r" % problems)

    def test_intraday_snapshot_still_flags_a_missing_close_result(self):
        # 但盘中也不是免检：上一收盘场压根没产出（2026-11-21 预览停在 11-18）
        # 必须当场报出来，否则停更要等到 96 小时时效阈值才被发现。
        problems = watchdog_site_check.snapshot_problems(
            self._dependency_doc(True, last_closed=False),
            at("2026-11-10T15:00:00+00:00"))
        self.assertTrue(any("收益基准落后" in p for p in problems),
                        "盘中放过净值缺场，停更就只剩 96 小时兜底: %r" % problems)
        self.assertTrue(any("次年预览未跟随" in p for p in problems),
                        "盘中放过预览缺场: %r" % problems)

    def test_closed_snapshot_still_flags_equity_version_gap(self):
        # 收盘后净值没跟上就是真故障，必须保留告警与自愈能力
        problems = watchdog_site_check.snapshot_problems(
            self._dependency_doc(False), at("2026-11-10T22:00:00+00:00"))
        self.assertTrue(any("收益基准落后" in p for p in problems),
                        "收盘后漏报净值未更新: %r" % problems)
        self.assertTrue(any("次年预览未跟随" in p for p in problems),
                        "收盘后漏报预览未更新: %r" % problems)


class HealthSnapshotAgeTests(unittest.TestCase):
    """health 体检快照只有新鲜时才有发言权。

    它是「站点全量构建那一刻」的快照，只由手动派发的全量更新作业刷新（2026-11-22
    线上这份 checked_at 停在 11-03），记的却全是行情停更、净值末点过期这类有
    时效的事实。照单全收会把一条早已修好的旧问题永久钉成恒红；反过来「读不出
    checked_at 就不采信」又是另一头的坑——产出方丢一个字段就能静默关掉报警。
    所以：确证过期才静音，缺时间戳按新鲜处理并另记一条 warning。
    """

    NOW = at("2026-11-22T22:00:00+00:00")
    ISSUE = "净值末点与已收盘日不一致"

    def _doc(self, health):
        q = {"ts": "2026-11-20T21:00:00Z", "session": "2026-11-20", "ok": True,
             "intraday": False, "previous_session": "2026-11-19",
             "quotes": {"SPY": {"as_of": "2026-11-20T21:00:00+00:00"}}}
        datasets = {k: {"ok": True} for k in watchdog_site_check.REQUIRED}
        datasets.update(quotes=q, health=health,
                        equity={"last_session": "20261119", "session": "2026-11-19",
                                "quote_version": "v1", "position": [{"ticker": "SPY"}],
                                "stats": {"strategy": 1.0}},
                        preview={"session": "2026-11-19", "quote_version": "v1",
                                 "top": [{}, {}, {}]})
        for key in ("b/equity", "b/preview"):
            datasets[key] = dict(datasets[key[len("b/"):]])
        return {"schema": 1, "source": "turso-http",
                "generated_at": "2026-11-20T21:00:00+00:00",
                "datasets": datasets, "meta": {}}

    def problems(self, health):
        warnings = []
        return (watchdog_site_check.snapshot_problems(self._doc(health), self.NOW,
                                                     warnings), warnings)

    def test_fresh_degraded_snapshot_still_alerts(self):
        problems, warnings = self.problems(
            {"status": "degraded", "issues": [self.ISSUE],
             "checked_at": "2026-11-22T21:00:00Z"})
        self.assertIn(self.ISSUE, problems, "新鲜体检快照的报警被吞掉了：%r" % problems)
        self.assertEqual(warnings, [])

    def test_stale_snapshot_is_muted_and_reported_as_a_warning(self):
        problems, warnings = self.problems(
            {"status": "degraded", "issues": [self.ISSUE],
             "checked_at": "2026-11-03T13:00:00Z"})
        self.assertNotIn(self.ISSUE, problems, "过期快照仍在替现状报警：%r" % problems)
        self.assertTrue(any("health 体检快照已" in w for w in warnings),
                        "静音必须留下可见痕迹，否则就是一条无人知晓的门禁：%r" % warnings)

    def test_missing_checked_at_is_treated_as_fresh_not_silenced(self):
        problems, warnings = self.problems({"status": "degraded", "issues": [self.ISSUE]})
        self.assertIn(self.ISSUE, problems, "缺时间戳不得静默报警：%r" % problems)
        self.assertTrue(any("缺 checked_at" in w for w in warnings), "%r" % warnings)

    def test_unparseable_checked_at_is_treated_as_fresh(self):
        problems, _warnings = self.problems(
            {"status": "degraded", "issues": [self.ISSUE], "checked_at": "昨天下午"})
        self.assertIn(self.ISSUE, problems, "%r" % problems)

    def test_quote_issues_are_still_excluded_by_the_quote_gate(self):
        # 行情时效由场次判据单独负责，体检快照里那条重复项不参与判定。
        problems, _warnings = self.problems(
            {"status": "degraded", "issues": ["行情 30 小时未更新"],
             "checked_at": "2026-11-22T21:30:00Z"})
        self.assertNotIn("行情 30 小时未更新", problems, "%r" % problems)


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

    def test_sync_only_after_successful_collection(self):
        """2026-11-17：盘中必须实时更新。采集成功后立即推备库，绝不先同步。
        08133d2 曾改成「只收盘一次、不同步」，导致 egress freeze 档整日停更。"""
        self.assertIn("gh workflow run db-sync.yml", self.quotes)
        self.assertIn("success() && steps.refresh.outcome == 'success'", self.quotes)

    def test_all_collection_is_calendar_gated(self):
        self.assertIn('AS_OF_INPUT: ${{ github.event.inputs.as_of }}', self.quotes)
        self.assertIn('python3 trading_window.py --task "$TASK" $EXTRA --gate', self.quotes)
        self.assertIn('python live_quotes.py --write', self.quotes)
        # dispatch 分两种：带 as_of = 收盘补跑，不带 = 盘中实时验证。
        self.assertIn('if [ -n "$AS_OF_INPUT" ]', self.quotes)
        self.assertIn('TASK=intraday', self.quotes)

    def test_intraday_and_close_crons_present(self):
        """盘中 30 分钟 + 开盘后关键 + 收盘关键：freeze 档靠对账补采兜底。"""
        self.assertIn('"*/30 13-20 * * 1-5"', self.quotes)
        self.assertIn('"0 14 * * 1-5"', self.quotes)
        self.assertIn('"0 15 * * 1-5"', self.quotes)
        self.assertIn('"17 21 * * 1-5"', self.quotes)
        self.assertIn('"50 21 * * 1-5"', self.quotes)

    def test_collection_has_enough_timeout_and_one_retry(self):
        # 4 分钟 job 超时实测把 500+ 标的采集腰斩，两次补采都被 canceled
        self.assertIn("timeout-minutes: 20", self.quotes)
        self.assertIn("timeout-minutes: 14", self.quotes)
        self.assertIn("for attempt in 1 2", self.quotes)

    def test_sync_close_cron_runs_after_collection(self):
        # 21:55 UTC > 21:17 / 21:50 两次收盘采集，否则同步的永远是旧数据
        self.assertIn('"55 16 * * 1-5"', self.sync)
        self.assertNotIn('"10 21 * * 1-5"', self.sync)


if __name__ == "__main__":
    unittest.main()
