# -*- coding: utf-8 -*-
"""出口流量预算门禁的回归测试（纯函数，不联网、不连库）。"""
import datetime as dt
import unittest

import egress_budget as eb


class CycleTest(unittest.TestCase):

    def test_mid_cycle_uses_previous_month_anchor(self):
        now = dt.datetime(2026, 11, 7, 14, 0, tzinfo=dt.timezone.utc)
        start, end = eb.cycle_bounds(now)
        self.assertEqual(start.date().isoformat(), "2026-10-28")
        self.assertEqual(end.date().isoformat(), "2026-11-28")

    def test_after_anchor_uses_current_month(self):
        now = dt.datetime(2026, 11, 29, 1, 0, tzinfo=dt.timezone.utc)
        start, end = eb.cycle_bounds(now)
        self.assertEqual(start.date().isoformat(), "2026-11-28")
        self.assertEqual(end.date().isoformat(), "2026-12-28")

    def test_year_boundary(self):
        start, end = eb.cycle_bounds(
            dt.datetime(2027, 1, 3, tzinfo=dt.timezone.utc))
        self.assertEqual(start.date().isoformat(), "2026-12-28")
        self.assertEqual(end.date().isoformat(), "2027-01-28")


class DecideTest(unittest.TestCase):

    def test_normal_allows_everything(self):
        self.assertEqual(eb.decide(100, 2, False, 3000), ("normal", True))

    def test_throttle_only_allows_critical(self):
        self.assertEqual(eb.decide(2500, 2, False, 3000), ("throttle", False))
        self.assertEqual(eb.decide(2500, 2, True, 3000), ("throttle", True))

    def test_freeze_is_minimal_daily_mode_not_total_shutdown(self):
        # 超额后仍放行收盘关键任务，避免页面数据连续三周冻结
        self.assertEqual(eb.decide(4000, 2, True, 3000), ("freeze", True))
        self.assertEqual(eb.decide(4000, 2, False, 3000), ("freeze", False))


class EstimateTest(unittest.TestCase):

    def test_legacy_and_steady_state_costs_are_split_by_cutoff(self):
        runs = [
            {"name": "在线净值与动态排名", "created_at": "2026-11-01T10:00:00Z",
             "conclusion": "success"},
            {"name": "在线净值与动态排名", "created_at": "2026-11-08T10:00:00Z",
             "conclusion": "success"},
        ]
        est = eb.estimate(runs)
        self.assertEqual(est["runs"], 2)
        self.assertEqual(est["estimated_mb"], 38.0)  # 35 (旧) + 3 (新)
        self.assertEqual(est["by_workflow"][0]["count"], 2)

    def test_truncated_ledger_is_conservatively_amplified(self):
        runs = [{"name": "行情快照", "created_at": "2026-11-01T10:00:00Z",
                 "conclusion": "success"}] * 10
        full = eb.estimate(runs, False)
        cut = eb.estimate(runs, True)
        self.assertTrue(cut["truncated"])
        self.assertGreater(cut["estimated_mb"], full["estimated_mb"])
        self.assertEqual(cut["estimated_mb"], full["estimated_mb"] * 1.5)

    def test_unknown_workflow_uses_conservative_default(self):
        est = eb.estimate([{"name": "未知作业",
                            "created_at": "2026-11-08T10:00:00Z",
                            "conclusion": "success"}])
        self.assertEqual(est["estimated_mb"], eb.DEFAULT_COST_MB)


if __name__ == "__main__":
    unittest.main()
