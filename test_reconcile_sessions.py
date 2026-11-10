"""场次对账器回归：状态驱动补采、迟到容错、防自激、fail-open。"""
import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import reconcile_sessions as rs
import trading_window as tw

UTC = dt.timezone.utc
CAL = tw.load_calendar()


def quotes(session, ts, intraday=False):
    return {"session": session, "ts": ts, "intraday": intraday,
            "ok": True, "quotes": {"SPY": {"p": 600.0}}}


class EvaluateTests(unittest.TestCase):
    def test_intraday_missing_session_requests_realtime_backfill(self):
        got = rs.evaluate(dt.datetime.fromisoformat("2026-11-10T15:30:00+00:00"),
                          CAL, quotes("2026-11-09", "2026-11-09T21:17:00Z"))
        self.assertTrue(got["need_action"])
        self.assertEqual(got["mode"], "intraday")
        self.assertEqual(got["as_of"], "")
        self.assertEqual(got["expected_session"], "2026-11-10")

    def test_intraday_present_snapshot_is_idempotent(self):
        got = rs.evaluate(dt.datetime.fromisoformat("2026-11-10T15:30:00+00:00"),
                          CAL, quotes("2026-11-10", "2026-11-10T14:33:00Z", True))
        self.assertFalse(got["need_action"])

    def test_opening_grace_window_does_not_misfire(self):
        got = rs.evaluate(dt.datetime.fromisoformat("2026-11-10T14:40:00+00:00"),
                          CAL, quotes("2026-11-09", "2026-11-09T21:17:00Z"))
        self.assertFalse(got["need_action"])

    def test_after_close_intraday_snapshot_requires_closing_version(self):
        got = rs.evaluate(dt.datetime.fromisoformat("2026-11-10T22:00:00+00:00"),
                          CAL, quotes("2026-11-10", "2026-11-10T20:00:00Z", True))
        self.assertTrue(got["need_action"])
        self.assertEqual(got["mode"], "closing")
        self.assertEqual(got["as_of"], "2026-11-10T21:17:00+00:00")

    def test_closing_version_online_is_idempotent(self):
        got = rs.evaluate(dt.datetime.fromisoformat("2026-11-10T22:00:00+00:00"),
                          CAL, quotes("2026-11-10", "2026-11-10T21:17:00Z"))
        self.assertFalse(got["need_action"])

    def test_cron_delayed_six_hours_still_backfills(self):
        """GitHub 把 21:17 收盘任务拖到次日 03:49，仍按逻辑时间补回。"""
        got = rs.evaluate(dt.datetime.fromisoformat("2026-11-10T03:49:00+00:00"),
                          CAL, quotes("2026-11-06", "2026-11-06T21:17:00Z"))
        self.assertTrue(got["need_action"])
        self.assertEqual(got["mode"], "closing")
        self.assertEqual(got["as_of"], "2026-11-09T21:17:00+00:00")
        self.assertEqual(got["expected_session"], "2026-11-09")

    def test_weekend_within_20h_backfills_but_expired_gap_does_not(self):
        recent = rs.evaluate(dt.datetime.fromisoformat("2026-11-14T12:00:00+00:00"),
                             CAL, quotes("2026-11-06", "2026-11-06T21:17:00Z"))
        self.assertTrue(recent["need_action"])
        expired = rs.evaluate(dt.datetime.fromisoformat("2026-11-16T12:00:00+00:00"),
                              CAL, quotes("2026-11-06", "2026-11-06T21:17:00Z"))
        self.assertFalse(expired["need_action"])

    def test_exchange_holiday_does_not_misfire(self):
        got = rs.evaluate(dt.datetime.fromisoformat("2026-11-26T15:00:00+00:00"),
                          CAL, quotes("2026-11-25", "2026-11-25T21:17:00Z"))
        self.assertFalse(got["need_action"])

    def test_missing_timestamp_counts_as_gap(self):
        got = rs.evaluate(dt.datetime.fromisoformat("2026-11-10T15:30:00+00:00"),
                          CAL, {"session": "2026-11-10"})
        self.assertTrue(got["need_action"])


class DispatchGuardTests(unittest.TestCase):
    NOW = dt.datetime.fromisoformat("2026-11-10T15:30:00+00:00")

    def test_in_flight_run_blocks_duplicate_dispatch(self):
        allowed, why = rs.dispatch_allowed("2026-11-10",
                                           [{"status": "in_progress"}], self.NOW)
        self.assertFalse(allowed)
        self.assertIn("在途", why)

    def test_cooldown_blocks_duplicate_dispatch(self):
        runs = [{"status": "completed", "createdAt": "2026-11-10T15:10:00Z"}]
        allowed, why = rs.dispatch_allowed("2026-11-10", runs, self.NOW)
        self.assertFalse(allowed)
        self.assertIn("冷却", why)

    def test_per_session_cap_blocks_endless_retry(self):
        runs = [{"status": "completed", "createdAt": "2026-11-10T%02d:00:00Z" % h}
                for h in (1, 3, 5, 7)]
        allowed, why = rs.dispatch_allowed("2026-11-10", runs, self.NOW)
        self.assertFalse(allowed)
        self.assertIn("上限", why)

    def test_clean_history_allows_dispatch(self):
        allowed, _ = rs.dispatch_allowed("2026-11-10", [], self.NOW)
        self.assertTrue(allowed)

    def test_missing_history_defaults_to_allow(self):
        allowed, _ = rs.dispatch_allowed("2026-11-10", None, self.NOW)
        self.assertTrue(allowed)


class FailOpenTests(unittest.TestCase):
    def test_unreachable_turso_never_fails_the_pipeline(self):
        """监控链路抖动不能变成一次失败的 CI 运行，也不能制造假缺口。"""
        with patch.object(rs, "read_quotes", side_effect=RuntimeError("503")), \
             patch.object(rs, "_recent_runs", return_value=[]), \
             tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.txt"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(out)}), \
                 patch("sys.argv", ["reconcile_sessions.py", "--gate"]):
                self.assertEqual(rs.main(), 0)
            text = out.read_text(encoding="utf-8")
            self.assertIn("need_action=False", text)
            self.assertIn("dispatch_allowed=False", text)
            self.assertIn("source=unavailable", text)

    def test_gate_writes_actionable_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            fixture = Path(d) / "q.json"
            fixture.write_text(json.dumps(
                quotes("2026-11-09", "2026-11-09T21:17:00Z")), encoding="utf-8")
            out = Path(d) / "out.txt"
            with patch.object(rs, "_recent_runs", return_value=[]), \
                 patch.dict(os.environ, {"GITHUB_OUTPUT": str(out)}), \
                 patch("sys.argv", ["reconcile_sessions.py", "--gate",
                                    "--no-history",
                                    "--at", "2026-11-10T15:30:00+00:00",
                                    "--quotes-json", str(fixture)]):
                self.assertEqual(rs.main(), 0)
            text = out.read_text(encoding="utf-8")
            self.assertIn("need_action=True", text)
            self.assertIn("dispatch_allowed=True", text)
            self.assertIn("mode=intraday", text)
            self.assertIn("expected_session=2026-11-10", text)


class WorkflowContractTests(unittest.TestCase):
    """防止「多触发源 + 防自激 + 同步不取消」这套容错骨架被误删。"""

    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parent / ".github" / "workflows"
        cls.reconcile = (root / "reconcile.yml").read_text(encoding="utf-8")
        cls.sync = (root / "db-sync.yml").read_text(encoding="utf-8")

    def test_independent_triggers_break_the_common_mode_failure(self):
        for workflow in ("行情快照", "备库同步", "网页发布", "看门狗（停更告警）"):
            self.assertIn('"%s"' % workflow, self.reconcile)
        self.assertIn('cron: "17,47 * * * *"', self.reconcile)
        self.assertIn('cron: "25 22 * * 1-5"', self.reconcile)
        self.assertIn("workflow_dispatch:", self.reconcile)
        # 平台外时钟的接入点不能被删：这是唯一不依赖 GitHub cron 的触发源
        self.assertIn("repository_dispatch:", self.reconcile)
        self.assertIn("reconcile-tick", self.reconcile)

    def test_successful_quotes_run_defers_reconcile_until_sync_lands(self):
        self.assertIn("github.event.workflow_run.name != '行情快照'", self.reconcile)
        self.assertIn("conclusion != 'success'", self.reconcile)

    def test_dispatch_is_gated_by_data_state_not_by_clock(self):
        self.assertIn("dispatch_allowed == 'true'", self.reconcile)
        self.assertIn("-f critical=true", self.reconcile)
        self.assertIn("-f as_of=", self.reconcile)
        self.assertIn("python3 reconcile_sessions.py --gate", self.reconcile)

    def test_in_flight_sync_is_never_cancelled(self):
        self.assertIn("cancel-in-progress: false", self.sync)


if __name__ == "__main__":
    unittest.main()
