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

from prepare_live_ranking import factor_rebase_evidence, classify_errors, normalized


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
        # Two jumps inside the window => not a single re-base (corrupt / mixed source).
        raw = {"2026-11-04": 99.0, BEGIN: 100.0, SESSION: 98.0}
        adjusted = {"2026-11-04": 98.01, BEGIN: 98.0, SESSION: 98.0}
        data = {"raw": raw, "adjusted": adjusted}
        with self.assertRaisesRegex(ValueError, "unstable factor shape"):
            factor_rebase_evidence("MAS", old_context(), data, BEGIN,
                                   {"pc": 98.0, "p": 98.0},
                                   100.0, 98.0, SESSION)

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


class AdvisoryTest(unittest.TestCase):

    def test_only_universe_failures_block_the_pipeline(self):
        errors = [{"symbol": "SKYD_WS", "error": "unavailable"},
                  {"symbol": "PFE", "error": "mismatch"}]
        blocking, advisory = classify_errors(errors, {"PFE", "SPY"})
        self.assertEqual([e["symbol"] for e in blocking], ["PFE"])
        self.assertEqual([e["symbol"] for e in advisory], ["SKYD_WS"])

    def test_all_universe_failures_still_block(self):
        errors = [{"symbol": "PFE", "error": "mismatch"}]
        blocking, advisory = classify_errors(errors, {"PFE"})
        self.assertEqual(len(blocking), 1)
        self.assertEqual(advisory, [])

    def test_no_error_is_dropped(self):
        errors = [{"symbol": s, "error": "x"} for s in ("A", "B", "C")]
        blocking, advisory = classify_errors(errors, {"A"})
        self.assertEqual(len(blocking) + len(advisory), 3)


if __name__ == "__main__":
    unittest.main()
