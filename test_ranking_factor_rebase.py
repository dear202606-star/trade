"""Regression tests for the ex-dividend adjustment-factor splice.

2026-11-06: eight symbols (JBHT/CMS/CCL/MAS/NSC/PFE/WFC/FE) all went
ex-dividend on the same session. Their forward-adjusted history was re-based,
so the splice point's absolute factor changed by 0.2%-1.4% while raw prices
stayed identical. The old `f_old == f_new` guard rejected all of them,
`stored=0`, and the next-year preview froze at the previous session.

These tests pin down that a *verified* re-base is accepted, while every other
kind of factor change stays fail-closed.
"""
import unittest

from prepare_live_ranking import (factor_rebase_evidence, splice_anchor,
                                  splice_raw_ok, normalized)


BEGIN = "2026-11-05"
SESSION = "2026-11-06"


def old_context():
    """Sealed before the ex-date: factor == 1.0 everywhere."""
    return {"raw": {"2026-11-04": 99.0, BEGIN: 100.0},
            "adjusted": {"2026-11-04": 99.0, BEGIN: 100.0},
            "source_end": BEGIN}


def new_window(adj_begin=99.0):
    """Fetched after the ex-date: history scaled down, latest bar at factor 1.0."""
    return {"raw": {BEGIN: 100.0, SESSION: 98.0},
            "adjusted": {BEGIN: adj_begin, SESSION: 98.0}}


class FactorRebaseTest(unittest.TestCase):

    def test_verified_ex_dividend_is_accepted_and_audited(self):
        data = new_window(adj_begin=99.0)
        quote = {"pc": 99.0, "p": 98.0}
        ev = factor_rebase_evidence("JBHT", old_context(), data, BEGIN,
                                    quote, 100.0, 99.0, SESSION)
        self.assertIsNotNone(ev)
        self.assertAlmostEqual(ev["f_old"], 1.0)
        self.assertAlmostEqual(ev["f_new"], 0.99)
        self.assertEqual(ev["ex_dividend_days"], [SESSION])
        self.assertEqual(ev["verified_by"], "sealed_quote_snapshot_pc")
        # The splice must restore the dividend: normalized change == p/pc.
        values = normalized(old_context())
        spliced = {d: values[BEGIN] * v / data["adjusted"][BEGIN]
                   for d, v in data["adjusted"].items()}
        self.assertAlmostEqual(spliced[SESSION] / values[BEGIN],
                               quote["p"] / quote["pc"], places=9)

    def test_no_rebase_returns_none(self):
        old = old_context()
        data = {"raw": {BEGIN: 100.0, SESSION: 98.0},
                "adjusted": {BEGIN: 100.0, SESSION: 98.0}}
        self.assertIsNone(factor_rebase_evidence(
            "SPY", old, data, BEGIN, {"pc": 100.0, "p": 98.0},
            100.0, 100.0, SESSION))

    def test_factor_change_without_ex_dividend_is_rejected(self):
        # pc equals the RAW previous close => an ordinary session, so a factor
        # change cannot be explained by a dividend and must stay fail-closed.
        data = new_window(adj_begin=99.0)
        with self.assertRaisesRegex(ValueError, "does not confirm ex-dividend"):
            factor_rebase_evidence("CMS", old_context(), data, BEGIN,
                                   {"pc": 100.0, "p": 98.0},
                                   100.0, 99.0, SESSION)

    def test_missing_quote_is_rejected(self):
        data = new_window(adj_begin=99.0)
        with self.assertRaisesRegex(ValueError, "does not confirm ex-dividend"):
            factor_rebase_evidence("CCL", old_context(), data, BEGIN,
                                   None, 100.0, 99.0, SESSION)

    def test_unstable_factor_shape_is_rejected(self):
        # 锚点之后出现多次因子跳变 => 不是单次除息重基准（混源/损坏）。
        begin='2026-11-02'
        raw={'2026-11-02':90.0,'2026-11-03':91.0,'2026-11-04':92.0,SESSION:98.0}
        adjusted={'2026-11-02':89.1,'2026-11-03':89.18,
                  '2026-11-04':91.08,SESSION:98.0}
        old={'raw':{begin:90.0},'adjusted':{begin:90.0}}
        data={'raw':raw,'adjusted':adjusted}
        with self.assertRaisesRegex(ValueError, "unstable factor shape"):
            factor_rebase_evidence("MAS", old, data, begin,
                                   {"pc": 89.1, "p": 98.0},
                                   90.0, 89.1, SESSION)

    def test_gross_factor_ratio_is_rejected(self):
        # A wrong symbol / methodology break produces a huge scaling.
        data = new_window(adj_begin=50.0)
        with self.assertRaisesRegex(ValueError, "gross factor ratio"):
            factor_rebase_evidence("PFE", old_context(), data, BEGIN,
                                   {"pc": 50.0, "p": 98.0},
                                   100.0, 50.0, SESSION)

    def test_real_world_dividend_sizes_are_all_accepted(self):
        # Measured 2026-11-06 factors: PFE 0.98598 ... JBHT 0.997945.
        for factor in (0.98598, 0.990237, 0.991945, 0.994179,
                       0.995008, 0.996095, 0.996168, 0.997945):
            with self.subTest(factor=factor):
                data = {"raw": {BEGIN: 100.0, SESSION: 98.0},
                        "adjusted": {BEGIN: 100.0 * factor, SESSION: 98.0}}
                ev = factor_rebase_evidence("X", old_context(), data, BEGIN,
                                            {"pc": 100.0 * factor, "p": 98.0},
                                            100.0, 100.0 * factor, SESSION)
                self.assertIsNotNone(ev)


class SplitRebaseTest(unittest.TestCase):
    """2026-11-20 SNDK went 1:8, and the whole 504-symbol ranking froze.

    Measured from push2his 105.SNDK (fqt=0 raw / fqt=1 adjusted):

        2026-11-18  raw 776.22   adj  97.028   adj/raw 0.1250
        2026-11-19  raw 755.16   adj  94.395   adj/raw 0.1250
        2026-11-20  raw  79.24   adj  79.240   adj/raw 1.0000   volume x9.9

    The context sealed on 11-19 carried factor 1.0; re-fetching after the split
    returns 0.125 at the same anchor, so `ratio` is exactly 1/8 and no 5% band
    can admit it. Raw is not retroactively scaled, which is why `splice_raw_ok`
    passes and the rejection lands on the ratio alone.
    """

    SPLIT_DAY = "2026-11-20"
    ANCHOR = "2026-11-19"

    @staticmethod
    def sealed():
        # Fetched while 11-19 was still the latest session: adjusted == raw.
        return {"raw": {SplitRebaseTest.ANCHOR: 755.16},
                "adjusted": {SplitRebaseTest.ANCHOR: 755.16},
                "source_end": SplitRebaseTest.ANCHOR}

    @staticmethod
    def window(raw_close=79.24, adj_anchor=94.395):
        return {"raw": {SplitRebaseTest.ANCHOR: 755.16,
                        SplitRebaseTest.SPLIT_DAY: raw_close},
                "adjusted": {SplitRebaseTest.ANCHOR: adj_anchor,
                             SplitRebaseTest.SPLIT_DAY: raw_close}}

    def evidence(self, **kwargs):
        data = self.window(**kwargs)
        return factor_rebase_evidence(
            "SNDK", self.sealed(), data, self.ANCHOR,
            {"pc": 94.395, "p": float(data["raw"][self.SPLIT_DAY])},
            755.16, 94.395, self.SPLIT_DAY), data

    def test_forward_split_is_accepted_and_audited(self):
        ev, _ = self.evidence()
        self.assertIsNotNone(ev)
        self.assertEqual(ev["kind"], "split")
        self.assertEqual(ev["split_ratio"], 8)
        self.assertEqual(ev["ex_date"], self.SPLIT_DAY)
        self.assertAlmostEqual(ev["f_old"], 1.0)
        self.assertAlmostEqual(ev["f_new"], 0.125)
        # -16.05%: the real session return, which the re-base must not corrupt.
        self.assertAlmostEqual(ev["split_session_return"], 0.83946, places=4)
        self.assertEqual(ev["verified_by"], "sealed_quote_snapshot_pc")

    def test_split_splice_restores_the_true_return(self):
        ev, data = self.evidence()
        self.assertIsNotNone(ev)
        values = normalized(self.sealed())
        spliced = {d: values[self.ANCHOR] * v / data["adjusted"][self.ANCHOR]
                   for d, v in data["adjusted"].items()}
        quote = {"pc": 94.395, "p": 79.24}
        self.assertAlmostEqual(spliced[self.SPLIT_DAY] / values[self.ANCHOR],
                               quote["p"] / quote["pc"], places=9)

    def test_split_ratio_with_implausible_session_return_is_rejected(self):
        # Same clean 1/8 factor step, but the adjusted series jumps 2.1x, which
        # no real 1:8 split day produces. Must stay fail-closed.
        with self.assertRaisesRegex(ValueError, "gross factor ratio"):
            self.evidence(raw_close=200.0)

    def test_non_integer_factor_ratio_is_rejected(self):
        # 0.13 is not 1/N for any plausible N; a wrong-symbol splice looks
        # exactly like this.
        with self.assertRaisesRegex(ValueError, "gross factor ratio"):
            self.evidence(raw_close=88.35, adj_anchor=98.17)

    def test_dividend_path_is_labelled_as_such(self):
        data = new_window(adj_begin=99.0)
        ev = factor_rebase_evidence("JBHT", old_context(), data, BEGIN,
                                    {"pc": 99.0, "p": 98.0}, 100.0, 99.0, SESSION)
        self.assertEqual(ev["kind"], "dividend")
        self.assertNotIn("split_ratio", ev)


class SpliceAnchorTest(unittest.TestCase):
    """A thinly traded warrant can have no bar on the nominal splice day."""

    def test_uses_begin_when_both_series_have_it(self):
        old=old_context()
        data={"raw":{BEGIN:100.0,SESSION:98.0},"adjusted":{BEGIN:99.0,SESSION:98.0}}
        self.assertEqual(splice_anchor(old,data,BEGIN),BEGIN)

    def test_falls_back_to_the_latest_common_day_before_begin(self):
        old=old_context()
        data={"raw":{"2026-11-03":98.0,"2026-11-04":99.0,SESSION:98.0},
              "adjusted":{"2026-11-03":98.0,"2026-11-04":99.0,SESSION:98.0}}
        self.assertEqual(splice_anchor(old,data,BEGIN),"2026-11-04")

    def test_returns_none_when_there_is_no_common_day(self):
        old=old_context()
        data={"raw":{SESSION:98.0},"adjusted":{SESSION:98.0}}
        self.assertIsNone(splice_anchor(old,data,BEGIN))

    def test_never_anchors_on_a_future_bar(self):
        old=old_context()
        data={"raw":{"2026-11-07":101.0},"adjusted":{"2026-11-07":101.0}}
        self.assertIsNone(splice_anchor(old,data,BEGIN))


class SpliceRawTest(unittest.TestCase):
    """Anchor prices come from two collection passes, sometimes two vendors."""

    def test_vendor_tail_differences_are_accepted(self):
        self.assertTrue(splice_raw_ok(100.0, 100.005))
        self.assertTrue(splice_raw_ok(174.755, 174.76))
        self.assertTrue(splice_raw_ok(432.97, 433.74))

    def test_wrong_symbol_or_wrong_day_is_rejected(self):
        self.assertFalse(splice_raw_ok(100.0, 110.0))
        self.assertFalse(splice_raw_ok(100.0, 101.0))
        self.assertFalse(splice_raw_ok(20.0, 20.1))

    def test_exact_match_is_accepted(self):
        self.assertTrue(splice_raw_ok(100.0, 100.0))


if __name__ == "__main__":
    unittest.main()
