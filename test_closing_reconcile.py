"""test_closing_reconcile.py — 收盘价独立源交叉复核的回归测试。

覆盖 live_strategy.reconcile_closing_prices 的安全边界：
单源污染可自愈、双源不吻合/独立源不可达/盘中一律不放宽 fail-closed。
"""
import unittest
from unittest.mock import patch

import live_strategy as ls

SESSION = "2026-11-06"
QUOTES = {
    "session": SESSION,
    "previous_session": "2026-11-05",
    "intraday": False,
    "quotes": {"NEM": {"p": 120.91}, "SPY": {"p": 735.29}},
}

YAHOO_OK = {
    "raw": {"2026-11-06": 120.91000366210938},
    "adjusted": {"2026-11-06": 120.91000366210938},
}


def prices():
    return {
        "NEM": {
            "raw": {"2026-11-05": 119.71, SESSION: 118.00},
            "adjusted": {"2026-11-05": 119.71, SESSION: 118.00},
            "evidence": {"online_context_sha256": ["sealed"]},
        },
        "SPY": {
            "raw": {SESSION: 735.29},
            "adjusted": {SESSION: 735.29},
        },
    }


class ClosingReconcileTest(unittest.TestCase):

    @patch.object(ls, "fetch_daily_yahoo", return_value=YAHOO_OK)
    def test_single_source_pollution_is_repaired_with_evidence(self, _fetch):
        p, notes = ls.reconcile_closing_prices(prices(), QUOTES, ["NEM", "SPY"])
        self.assertTrue(ls.close_enough(p["NEM"]["raw"][SESSION], 120.91))
        self.assertEqual(p["NEM"]["evidence"]["closing_cross_check"]["source"], "yahoo")
        self.assertEqual(p["NEM"]["raw"]["2026-11-05"], 119.71)   # 历史封存证据不动
        self.assertEqual(p["NEM"]["evidence"]["online_context_sha256"], ["sealed"])
        self.assertEqual(p["SPY"]["raw"][SESSION], 735.29)         # 未分歧的标的零改动
        self.assertEqual(len(notes), 1)

    @patch.object(ls, "fetch_daily_yahoo",
                  return_value={"raw": {SESSION: 999.0}, "adjusted": {}})
    def test_both_sources_disagree_keeps_original_for_fail_closed(self, _fetch):
        p, notes = ls.reconcile_closing_prices(prices(), QUOTES, ["NEM"])
        self.assertEqual(p["NEM"]["raw"][SESSION], 118.00)
        self.assertIn("双源均不吻合", notes[0])

    @patch.object(ls, "fetch_daily_yahoo", side_effect=RuntimeError("offline"))
    def test_unreachable_independent_source_keeps_original(self, _fetch):
        p, notes = ls.reconcile_closing_prices(prices(), QUOTES, ["NEM"])
        self.assertEqual(p["NEM"]["raw"][SESSION], 118.00)
        self.assertIn("独立源不可达", notes[0])

    @patch.object(ls, "fetch_daily_yahoo", return_value=YAHOO_OK)
    def test_intraday_never_touches_prices(self, fetch):
        intraday = dict(QUOTES, intraday=True)
        p, notes = ls.reconcile_closing_prices(prices(), intraday, ["NEM"])
        self.assertEqual(p["NEM"]["raw"][SESSION], 118.00)
        self.assertEqual(notes, [])
        fetch.assert_not_called()

    @patch.object(ls, "fetch_daily_yahoo", return_value=YAHOO_OK)
    def test_adjusted_ratio_is_preserved(self, _fetch):
        original = prices()
        original["NEM"]["adjusted"][SESSION] = 118.00 * 0.9   # 主源复权比例 0.9
        p, _ = ls.reconcile_closing_prices(original, QUOTES, ["NEM"])
        self.assertAlmostEqual(p["NEM"]["adjusted"][SESSION] / p["NEM"]["raw"][SESSION],
                               0.9, places=6)


if __name__ == "__main__":
    unittest.main()
