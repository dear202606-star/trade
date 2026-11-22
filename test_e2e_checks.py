"""e2e_check 里「纯判据」部分的回归：分年持仓 vs 冻结选股快照。

这条校验 2026-11-03 起一直在跳过（CI 日志：「可选数据 backtest_picks.json
不可读（HTTPError），跳过附加校验」），因为快照是从在线库读的，而在线库里
根本没有这个 key。判据本身反而从来没被执行过——所以必须单独立门：
一旦有人把它改回「读不到就算通过」，这里第一个变红。
"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import e2e_check


def history(*years):
    return {"years": [{"year": year,
                       "holdings": [{"ticker": t} for t in tickers]}
                      for year, tickers in years]}


class FrozenPicksTests(unittest.TestCase):
    def snapshot(self, years):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                             encoding="utf-8")
        self.addCleanup(os.unlink, handle.name)
        json.dump({"generated": "2026-10-29", "years": years}, handle)
        handle.close()
        return handle.name

    def test_committed_snapshot_loads_and_covers_every_published_year(self):
        picks = e2e_check.frozen_picks()
        self.assertEqual(sorted(picks)[:3], ["2010", "2011", "2012"])
        self.assertTrue(all(isinstance(v, list) and v for v in picks.values()))

    def test_missing_file_is_an_error_not_a_silent_pass(self):
        with self.assertRaises(FileNotFoundError):
            e2e_check.frozen_picks(os.path.join(tempfile.gettempdir(), "no-such-picks.json"))

    def test_empty_or_malformed_years_reject(self):
        for payload in ({}, {"years": {}}, {"years": {"2010": []}}, {"years": {"20x0": ["A"]}}):
            with self.subTest(payload=payload):
                path = self.snapshot(payload.get("years", payload))
                with self.assertRaises(ValueError):
                    e2e_check.frozen_picks(path)


class PicksConsistencyTests(unittest.TestCase):
    PICKS = {"2010": ["A", "B", "C"], "2011": ["D", "E", "F"]}

    def test_matching_holdings_produce_no_problem(self):
        self.assertEqual(
            e2e_check.picks_consistency(history((2010, ["A", "B", "C"]),
                                                 (2011, ["D", "E", "F"])), self.PICKS), [])

    def test_reordered_holdings_is_still_a_mismatch(self):
        problems = e2e_check.picks_consistency(history((2010, ["A", "C", "B"])), self.PICKS)
        self.assertEqual(len(problems), 1)
        self.assertIn("2010 年持仓与冻结快照不一致", problems[0])

    def test_published_year_absent_from_snapshot_is_a_mismatch(self):
        # 快照才是基准：线上多出一段没有冻结依据的历史，等于绕过「已复核」这道关。
        problems = e2e_check.picks_consistency(history((2026, ["Z", "Y", "X"])), self.PICKS)
        self.assertEqual(problems, ["2026 年不在冻结选股快照里（快照共 2 年）"])

    def test_empty_published_history_cannot_hide_a_missing_snapshot(self):
        self.assertEqual(e2e_check.picks_consistency({"years": []}, self.PICKS), [])
        self.assertEqual(e2e_check.picks_consistency({}, {}), [])


class PicksCheckRunsInMainTests(unittest.TestCase):
    """main() 必须产出这条判定，而不是「取不到就算跳过」。"""

    MISSING = object()

    def setUp(self):
        # oks/fails/skips 是模块级累加器，不清空就会把上一条用例的判定带进这一条。
        for name in ("oks", "fails", "skips"):
            getattr(e2e_check, name).clear()

    def datasets(self):
        picks = e2e_check.frozen_picks()
        years = sorted(picks)[:3]
        return {
            "signal": {"official": [{"ticker": t, "weight": 1 / 3} for t in picks[years[0]]],
                       "official_year": int(years[-1]), "month": "2026-11"},
            "quotes": {"ok": True, "count": 10, "universe": 10, "ts": "2026-11-20T21:00:00Z",
                       "session": "2026-11-20", "intraday": False, "quotes": {"SPY": {"c": 1}}},
            "equity": {"points": [{"d": "20261120", "s": 1.0, "dd": 0.0}],
                       "last_session": "20261120", "stats": {"strategy": 1.0},
                       "position": [{"ticker": "SPY"}]},
            "preview": {},
            "curve": {"points": [{"d": "20261120", "s": 6.0, "dd": -0.1}],
                      "years": [{"year": int(y), "ret": 1.0} for y in years]},
            "strategy_history": history(*[(int(y), picks[y]) for y in years]),
        }

    def run_main(self, picks=MISSING, error=None):
        datasets = self.datasets()
        frozen = e2e_check.frozen_picks() if picks is PicksCheckRunsInMainTests.MISSING else picks
        out = io.StringIO()
        with patch.object(e2e_check, "data",
                          side_effect=lambda name, optional=False: datasets.get(
                              name[:-5] if name.endswith(".json") else name)), \
             patch.object(e2e_check, "page", return_value=None), \
             patch.object(e2e_check, "due_session", return_value=(None, "测试：不判时效")), \
             patch.object(e2e_check, "frozen_picks",
                          side_effect=error, return_value=frozen), \
             contextlib.redirect_stdout(out):
            code = e2e_check.main()
        return out.getvalue(), code

    def test_verdict_is_reported_not_skipped(self):
        printed, _code = self.run_main()
        self.assertIn("✅ 分年持仓与冻结快照逐字一致（3 年）", printed)
        self.assertNotIn("[跳过]", printed)
        self.assertNotIn("backtest_picks", printed)

    def test_a_mismatched_snapshot_fails_the_run(self):
        printed, code = self.run_main(picks={"2010": ["X", "Y", "Z"], "2011": ["A", "B", "C"],
                                             "2012": ["A", "B", "C"]})
        self.assertIn("❌ 2010 年持仓与冻结快照不一致", printed)
        self.assertEqual(code, 1, printed)

    def test_an_unreadable_snapshot_fails_the_run(self):
        # 快照读不到 = 校验基准丢了，绝不能退回「可选数据不可读，跳过附加校验」。
        printed, code = self.run_main(error=FileNotFoundError("没了"))
        self.assertIn("❌ 分年持仓无法校验：没了", printed)
        self.assertEqual(code, 1, printed)


if __name__ == "__main__":
    unittest.main()
