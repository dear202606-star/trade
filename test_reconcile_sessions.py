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
    def check(self, stamp, snapshot=None):
        return rs.evaluate(dt.datetime.fromisoformat(stamp + "+00:00"), CAL, snapshot)

    def test_no_collection_during_trading(self):
        for stamp in ("2026-07-01T14:00:00", "2026-11-10T15:30:00", "2026-11-10T21:44:00"):
            with self.subTest(stamp=stamp):
                self.assertFalse(self.check(stamp)["need_action"])

    def test_regular_and_early_closes_use_exchange_time(self):
        for stamp in ("2026-07-01T20:45:00", "2026-11-10T21:45:00", "2026-11-27T18:45:00"):
            with self.subTest(stamp=stamp):
                result = self.check(stamp)
                self.assertTrue(result["need_action"])
                self.assertEqual(result["mode"], "closing")
                self.assertEqual(result["as_of"], stamp + "+00:00")

    def test_delayed_close_retry_never_targets_the_next_open(self):
        self.assertEqual(self.check("2026-11-11T03:30:00")["expected_session"], "2026-11-10")
        self.assertFalse(self.check("2026-11-11T14:30:00")["need_action"])
        self.assertFalse(self.check("2026-11-14T18:00:00")["need_action"])
        self.assertFalse(self.check("2026-11-26T22:00:00")["need_action"])

    def test_only_settled_quote_covers_target(self):
        base = quotes("2026-11-10", "2026-11-10T21:00:00Z")
        for patch in ({}, {"intraday": True}, {"ts": None}, {"ts": "2026-11-10T23:00:00Z"},
                      {"session": "2026-11-09"}):
            with self.subTest(patch=patch):
                self.assertEqual(self.check("2026-11-10T22:00:00", {**base, **patch})["need_action"],
                                 bool(patch))


class DispatchGuardTests(unittest.TestCase):
    day = "2026-11-10"
    now = dt.datetime.fromisoformat("2026-11-10T23:00:00+00:00")

    def test_in_flight_scheduled_run_blocks_duplicate(self):
        self.assertFalse(rs.dispatch_allowed(self.day, [{"status": "queued"}], self.now)[0])

    def test_recent_dispatch_has_cooldown(self):
        run = {"status": "completed", "displayTitle": "行情快照 · backfill-closing",
               "createdAt": "2026-11-10T22:55:00Z"}
        self.assertFalse(rs.dispatch_allowed(self.day, [run], self.now)[0])

    def test_per_session_cap_blocks_endless_retries(self):
        run = {"status": "completed", "displayTitle": "行情快照 · backfill-closing",
               "createdAt": "2026-11-10T21:50:00Z"}
        self.assertFalse(rs.dispatch_allowed(self.day, [run] * rs.MAX_CLOSING_DISPATCH, self.now)[0])
        self.assertTrue(rs.dispatch_allowed(self.day, [run], self.now)[0])
        self.assertTrue(rs.dispatch_allowed(self.day, [], self.now)[0])


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
                                    "--at", "2026-11-10T22:00:00+00:00",
                                    "--quotes-json", str(fixture)]):
                self.assertEqual(rs.main(), 0)
            text = out.read_text(encoding="utf-8")
            self.assertIn("need_action=True", text)
            self.assertIn("dispatch_allowed=True", text)
            self.assertIn("mode=closing", text)
            self.assertIn("expected_session=2026-11-10", text)


class WorkflowContractTests(unittest.TestCase):
    """防止「多触发源 + 防自激 + 同步不取消」这套容错骨架被误删。"""

    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parent / ".github" / "workflows"
        cls.reconcile = (root / "reconcile.yml").read_text(encoding="utf-8")
        cls.quotes = (root / "quotes.yml").read_text(encoding="utf-8")
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

    def test_workflow_names_closing_retries(self):
        self.assertIn("backfill-closing", self.quotes)
        self.assertIn("--closing-only", self.quotes)


if __name__ == "__main__":
    unittest.main()
