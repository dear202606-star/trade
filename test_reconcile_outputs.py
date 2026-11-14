#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""展示输出对账（净值/预览落后自动重算）回归测试。"""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import reconcile_outputs as ro

NOW = dt.datetime.fromisoformat("2026-11-10T22:00:00+00:00")


class EvaluateTests(unittest.TestCase):
    close = {"session": "2026-11-10", "version": "current", "intraday": False}
    live = {**close, "intraday": True, "ts": "2026-11-10T21:55:00Z",
            "fresh_until": "2026-11-10T22:15:00Z"}
    current = {key: {"quote_version": "current"} for key in ro.OUTPUT_KEYS[1:]}

    def test_any_variant_or_kind_lagging_requires_recompute(self):
        for key in self.current:
            with self.subTest(key=key):
                got = ro.evaluate(self.close, {**self.current, key: {"quote_version": "old"}}, NOW)
                self.assertTrue(got["need_recompute"])
                self.assertIn(key, got["reason"])

    def test_matching_results_are_clean(self):
        for quote in [self.close, self.live]:
            self.assertFalse(ro.evaluate(quote, self.current, NOW)["need_recompute"])

    def test_intraday_quote_retries_only_within_source_deadline(self):
        self.assertTrue(ro.evaluate(self.live, {}, NOW)["need_recompute"])
        self.assertFalse(ro.evaluate(self.live, {}, NOW + dt.timedelta(minutes=15))["need_recompute"])

    def test_missing_quotes_session_never_recomputes(self):
        self.assertFalse(ro.evaluate({}, self.current, NOW)["need_recompute"])


class DispatchGuardTests(unittest.TestCase):
    def test_in_flight_blocks(self):
        allowed, why = ro.dispatch_allowed([{"status": "in_progress"}], NOW)
        self.assertFalse(allowed)
        self.assertIn("在途", why)

    def test_cooldown_blocks(self):
        runs = [{"status": "completed", "createdAt": "2026-11-10T21:50:00Z"}]
        self.assertIn("冷却", ro.dispatch_allowed(runs, NOW)[1])

    def test_daily_cap_blocks_endless_retry(self):
        runs = [{"status": "completed", "createdAt": "2026-11-10T%02d:00:00Z" % h}
                for h in (1, 3, 5, 7, 9, 11, 13)]
        self.assertIn("上限", ro.dispatch_allowed(runs, NOW)[1])

    def test_yesterday_runs_do_not_consume_todays_cap(self):
        runs = [{"status": "completed", "createdAt": "2026-11-09T%02d:00:00Z" % h}
                for h in (1, 3, 5, 7, 9, 11, 13)]
        self.assertTrue(ro.dispatch_allowed(runs, NOW)[0])

    def test_clean_history_allows(self):
        self.assertTrue(ro.dispatch_allowed([], NOW)[0])

    def test_missing_history_defaults_to_allow(self):
        self.assertTrue(ro.dispatch_allowed(None, NOW)[0])


class FailOpenTests(unittest.TestCase):
    def test_unreachable_turso_never_fails_pipeline(self):
        with patch.object(ro, "read_outputs", side_effect=RuntimeError("503")), \
             tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.txt"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(out)}), \
                 patch("sys.argv", ["reconcile_outputs.py", "--gate"]):
                self.assertEqual(ro.main(), 0)
            text = out.read_text(encoding="utf-8")
            self.assertIn("need_recompute=False", text)
            self.assertIn("dispatch_allowed=False", text)
            self.assertIn("source=unavailable", text)

    def test_gate_writes_actionable_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            q, p = Path(d) / "q.json", Path(d) / "p.json"
            q.write_text(json.dumps(EvaluateTests.close),
                         encoding="utf-8")
            p.write_text(json.dumps({"session": "2026-11-06"}), encoding="utf-8")
            out = Path(d) / "out.txt"
            with patch.object(ro, "_recent_runs", return_value=[]), \
                 patch.dict(os.environ, {"GITHUB_OUTPUT": str(out)}), \
                 patch("sys.argv", ["reconcile_outputs.py", "--gate",
                                    "--quotes-json", str(q), "--preview-json", str(p)]):
                self.assertEqual(ro.main(), 0)
            text = out.read_text(encoding="utf-8")
            self.assertIn("need_recompute=True", text)
            self.assertIn("dispatch_allowed=True", text)
            self.assertIn("target_session=2026-11-10", text)


class WorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parent / ".github" / "workflows"
        cls.reconcile = (root / "reconcile.yml").read_text(encoding="utf-8")
        cls.strategy = (root / "live-strategy.yml").read_text(encoding="utf-8")

    def test_reconcile_runs_output_check_and_dispatches_recompute(self):
        self.assertIn("reconcile_outputs.py --gate", self.reconcile)
        self.assertIn("derived.outputs.dispatch_allowed == 'true'", self.reconcile)
        self.assertIn("gh workflow run live-strategy.yml", self.reconcile)

    def test_recompute_never_runs_in_parallel_with_backfill(self):
        """本轮已派发补采时不再重算，避免两写主库。"""
        self.assertIn("steps.check.outputs.dispatch_allowed != 'true'", self.reconcile)

    def test_strategy_pushes_turso_after_compute(self):
        """净值算完必须立刻进 Turso，否则页面要等下一次同步才刷新。"""
        self.assertEqual(self.strategy.count("db-sync.yml -f targets=turso"), 2)
        self.assertEqual(self.strategy.count("steps.compute.outcome == 'success'"), 2)
        self.assertIn("actions: write", self.strategy)

    def test_settlement_review_is_bounded_to_once_per_close(self):
        """收盘结算复核：不能变成每 20 分钟一次的采集放大器。"""
        self.assertIn("收盘结算复核（每日一次", self.reconcile)
        self.assertIn("derived.outputs.need_recompute == 'true'", self.reconcile)
        self.assertIn('"backfill-closing" in (r.get("displayTitle") or "")',
                      self.reconcile)
        self.assertIn('.startswith(t)', self.reconcile)
        self.assertIn('[ "$DONE" != "0" ]', self.reconcile)

    def test_settlement_review_only_runs_after_that_days_close(self):
        self.assertIn('[ "$SESSION" != "$TODAY" ]', self.reconcile)
        self.assertIn('[ "$H" -lt 21 ] || [ "$H" -gt 23 ]', self.reconcile)
        self.assertIn('[ "$M" -lt 30 ]', self.reconcile)
        self.assertIn('-f critical=true', self.reconcile)
        self.assertIn('as_of="${TODAY}T21:17:00+00:00"', self.reconcile)


if __name__ == "__main__":
    unittest.main()
