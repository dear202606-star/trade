"""盘中秒退必须先于报价时效校验（2026-11-10 线上红色失败复盘）。

live_strategy.run_equity / live_ranking.run 原先第一行就是严格的
load_snapshot()：30 分钟采集节奏下「报价刷新任务超过 15 分钟未成功」
必然命中，作业还没走到 run_guard.should_skip('盘中不重算') 就抛错，
本应秒退的盘中运行天天变红。修复后先用 strict=False 轻量读一次，
确定要重算（收盘场次）时再严格复核，fail-closed 门禁不变。
"""
import unittest
from pathlib import Path
from unittest.mock import patch

import live_ranking
import live_strategy

INTRADAY = {"intraday": True, "session": "2026-11-10", "version": "v1"}
CLOSING = {"intraday": False, "session": "2026-11-10", "version": "v1"}


class SkipOrderTests(unittest.TestCase):
    def _loader(self, payload, calls, strict_error="在线报价未通过核验：报价刷新任务超过 15 分钟未成功"):
        def load(strict=True):
            calls.append(strict)
            if strict:
                raise RuntimeError(strict_error)
            return payload
        return load

    def test_equity_skips_intraday_before_strict_validation(self):
        calls = []
        with patch.object(live_strategy.live_quotes, "load_snapshot",
                          side_effect=self._loader(INTRADAY, calls)), \
             patch.object(live_strategy.run_guard, "should_skip", return_value=True):
            self.assertIsNone(live_strategy.run_equity())
        self.assertEqual(calls, [False], "盘中不应触发严格报价校验")

    def test_preview_skips_intraday_before_strict_validation(self):
        calls = []
        with patch.object(live_ranking.live_quotes, "load_snapshot",
                          side_effect=self._loader(INTRADAY, calls)), \
             patch.object(live_ranking.run_guard, "should_skip", return_value=True):
            self.assertIsNone(live_ranking.run())
        self.assertEqual(calls, [False], "盘中不应触发严格报价校验")

    def test_equity_close_session_still_requires_strict_validation(self):
        """真要写净值时门禁仍是 fail-closed，没有被这次修复绕过。"""
        calls = []
        with patch.object(live_strategy.live_quotes, "load_snapshot",
                          side_effect=self._loader(CLOSING, calls, "strict-guard-blocked")), \
             patch.object(live_strategy.run_guard, "should_skip", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "strict-guard-blocked"):
                live_strategy.run_equity()
        self.assertEqual(calls, [False, True])

    def test_preview_close_session_still_requires_strict_validation(self):
        calls = []
        with patch.object(live_ranking.live_quotes, "load_snapshot",
                          side_effect=self._loader(CLOSING, calls, "strict-guard-blocked")), \
             patch.object(live_ranking.run_guard, "should_skip", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "strict-guard-blocked"):
                live_ranking.run()
        self.assertEqual(calls, [False, True])


class LedgerContractTests(unittest.TestCase):
    """2026-11-11：场次幂等账本两处线上缺陷的结构回归。"""

    def test_no_collection_branch_still_marks_ledger(self):
        text = Path(__file__).with_name("live_strategy.py").read_text(encoding="utf-8")
        marker = "Both account results already match this online quote; no collection"
        self.assertIn(marker, text)
        branch = text.split(marker, 1)[1].split("active=[]", 1)[0]
        self.assertIn("run_guard.mark_done('equity',quotes)", branch)

    def test_ledger_kind_is_allowed_by_sys_event_check(self):
        text = Path(__file__).with_name("run_guard.py").read_text(encoding="utf-8")
        self.assertIn("values('validation'", text)
        self.assertNotIn("values('compute'", text)


if __name__ == "__main__":
    unittest.main()
