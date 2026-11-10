"""Cross-session ex-dividend splice regression (measured 2026-11-09 bars).

On 2026-11-09 the collection pipeline skipped its session (GitHub schedule
delay), so ranking contexts stayed sealed at 2026-11-06. Four NYSE names went
ex-dividend on 11-09 (AMP $1.70, GWW $2.49, VMC $0.52) or 11-10 (IBM $1.69).
When the incremental splice finally ran on 11-10, `factor_rebase_evidence`
rejected them: it required the single factor jump to land exactly on the
current session, and used a 1e-6 tolerance on an adjusted/raw ratio that a
2-decimal vendor price makes wobble by ~1e-4 (IBM got a phantom 11-09 jump).

That was a wrong assumption about the splice window, NOT a data-quality
exception. These tests pin down that a lawful cross-session re-base is now
accepted with auditable evidence, while every other shape stays fail-closed.
All bars below are the real push2his `106.<SYM>` klt=101 fqt=0/1 responses.
"""
import datetime as dt
import unittest
from pathlib import Path

from prepare_live_ranking import factor_rebase_evidence

SESSION = "2026-11-10"
PREVIOUS = "2026-11-09"
ANCHOR = "2026-11-06"

MEASURED = {
    # symbol: (raw bars, adjusted bars, quote pc, quote p)
    "AMP": ({"2026-11-06": 501.49, "2026-11-09": 518.30, "2026-11-10": 519.96},
            {"2026-11-06": 499.79, "2026-11-09": 518.30, "2026-11-10": 519.96},
            518.30, 519.96),
    "GWW": ({"2026-11-06": 1440.70, "2026-11-09": 1447.67, "2026-11-10": 1472.91},
            {"2026-11-06": 1438.21, "2026-11-09": 1447.67, "2026-11-10": 1472.91},
            1447.67, 1472.91),
    "VMC": ({"2026-11-06": 282.72, "2026-11-09": 289.44, "2026-11-10": 287.31},
            {"2026-11-06": 282.20, "2026-11-09": 289.44, "2026-11-10": 287.31},
            289.44, 287.31),
    # IBM: ex-date is the CURRENT session. The 1.69 cash difference is constant
    # on 11-06 and 11-09, but the adjusted/raw ratio differed by 3.2e-5 and
    # used to be counted as a second, phantom jump.
    "IBM": ({"2026-11-06": 245.00, "2026-11-09": 246.11, "2026-11-10": 244.53},
            {"2026-11-06": 243.31, "2026-11-09": 244.42, "2026-11-10": 244.53},
            244.42, 244.53),
}


class ReviewedCalendar:
    """Minimal stand-in for market_calendar.Calendar (weekends closed)."""

    def bounds(self, day):
        if day.weekday() > 4:
            return None
        return ("open", "close")


def sealed(raw, day=ANCHOR):
    """Context sealed before the ex-date: raw == adjusted at the anchor."""
    return {"raw": {day: raw}, "adjusted": {day: raw}, "source_end": day}


def call(symbol, data, old, quote, prev_raw, prev_adj,
         session=SESSION, begin=ANCHOR, calendar=None):
    return factor_rebase_evidence(symbol, old, data, begin, quote,
                                  prev_raw, prev_adj, session, calendar)


def measured(symbol, calendar=None):
    raw, adj, pc, p = MEASURED[symbol]
    data = {"raw": dict(raw), "adjusted": dict(adj)}
    old = sealed(raw[ANCHOR])
    quote = {"pc": pc, "p": p}
    prev_raw, prev_adj = raw[PREVIOUS], adj[PREVIOUS]
    return call(symbol, data, old, quote, prev_raw, prev_adj,
                calendar=calendar or ReviewedCalendar()), data, old, quote, prev_raw, prev_adj


class CrossSessionDividendTest(unittest.TestCase):

    def test_all_four_measured_rebases_are_accepted(self):
        expected = {"AMP": ("2026-11-09", 1.70), "GWW": ("2026-11-09", 2.49),
                    "VMC": ("2026-11-09", 0.52), "IBM": ("2026-11-10", 1.69)}
        for symbol, (ex_date, cash) in expected.items():
            with self.subTest(symbol=symbol):
                ev = measured(symbol)[0]
                self.assertIsNotNone(ev)
                self.assertEqual(ev["ex_date"], ex_date)
                self.assertEqual(ev["ex_dividend_days"], [ex_date])
                self.assertAlmostEqual(ev["cash_amount"], cash, places=2)
                self.assertEqual(ev["verified_by"], "sealed_quote_snapshot_pc")
                self.assertTrue(0.95 <= ev["ratio"] <= 1.05)

    def test_cross_session_ex_date_is_not_forced_to_current_session(self):
        # The exact production failure: `jumps[0] != session` used to reject.
        ev = measured("AMP")[0]
        self.assertNotEqual(ev["ex_date"], SESSION)
        self.assertGreater(ev["ex_date"], ev["begin"])
        self.assertLessEqual(ev["ex_date"], SESSION)

    def test_two_decimal_ratio_noise_is_not_a_second_jump(self):
        # IBM's adjusted/raw ratios: 0.993102 (11-06), 0.993134 (11-09),
        # 1.0 (11-10). Only the last one is a real jump.
        ev = measured("IBM")[0]
        self.assertEqual(ev["ex_dividend_days"], [SESSION])

    def test_multiple_real_jumps_still_rejected(self):
        _, data, old, _, prev_raw, prev_adj = measured("AMP")
        data["adjusted"][PREVIOUS] = 517.80   # diffs 1.70 -> 0.50 -> 0
        with self.assertRaisesRegex(ValueError, "unstable factor shape"):
            call("AMP", data, old, {"pc": 518.30}, prev_raw, prev_adj)

    def test_non_trading_ex_date_still_rejected(self):
        data = {"raw": {"2026-11-06": 100.0, "2026-11-07": 101.0, SESSION: 102.0},
                "adjusted": {"2026-11-06": 99.0, "2026-11-07": 101.0, SESSION: 102.0}}
        with self.assertRaisesRegex(ValueError, "not a reviewed exchange session"):
            call("X", data, sealed(100.0), {"pc": 101.0}, 101.0, 101.0,
                 calendar=ReviewedCalendar())

    def test_negative_cash_adjustment_still_rejected(self):
        data = {"raw": {"2026-11-06": 100.0, PREVIOUS: 101.0, SESSION: 102.0},
                "adjusted": {"2026-11-06": 101.0, PREVIOUS: 101.0, SESSION: 102.0}}
        with self.assertRaisesRegex(ValueError, "not one constant dividend"):
            call("X", data, sealed(100.0), {"pc": 101.0}, 101.0, 101.0,
                 calendar=ReviewedCalendar())

    def test_post_ex_date_not_at_factor_one_still_rejected(self):
        data = {"raw": {"2026-11-06": 100.0, PREVIOUS: 101.0, SESSION: 102.0},
                "adjusted": {"2026-11-06": 99.0, PREVIOUS: 100.5, SESSION: 101.5}}
        with self.assertRaisesRegex(ValueError, "post-ex-date series is not at factor 1.0"):
            call("X", data, sealed(100.0), {"pc": 101.0}, 101.0, 100.5,
                 calendar=ReviewedCalendar())

    def test_ex_date_outside_splice_window_still_rejected(self):
        data = {"raw": {"2026-11-05": 100.0, PREVIOUS: 101.0, SESSION: 102.0},
                "adjusted": {"2026-11-05": 99.0, PREVIOUS: 101.0, SESSION: 102.0}}
        with self.assertRaisesRegex(ValueError, "ex-date outside splice window"):
            call("X", data, sealed(100.0, "2026-11-05"), {"pc": 101.0},
                 101.0, 101.0, session="2026-11-06", begin="2026-11-05",
                 calendar=ReviewedCalendar())

    def test_unreconciled_previous_close_still_rejected(self):
        _, data, old, _, prev_raw, prev_adj = measured("AMP")
        with self.assertRaisesRegex(ValueError, "sealed previous close"):
            call("AMP", data, old, {"pc": 500.00}, prev_raw, prev_adj,
                 calendar=ReviewedCalendar())

    def test_missing_quote_still_rejected(self):
        _, data, old, _, prev_raw, prev_adj = measured("AMP")
        with self.assertRaisesRegex(ValueError, "does not confirm ex-dividend"):
            call("AMP", data, old, None, prev_raw, prev_adj,
                 calendar=ReviewedCalendar())

    def test_gross_factor_ratio_still_rejected(self):
        _, data, old, quote, prev_raw, prev_adj = measured("AMP")
        data["adjusted"][ANCHOR] = 471.49   # 30 / 501.49 ~ 6% re-base
        with self.assertRaisesRegex(ValueError, "gross factor ratio"):
            call("AMP", data, old, quote, prev_raw, prev_adj,
                 calendar=ReviewedCalendar())

    def test_same_session_ex_date_still_requires_adjusted_previous_close(self):
        # IBM's ex-date IS the current session: pc must equal the adjusted
        # previous close, not the raw one. Feed the raw pc and it must refuse.
        _, data, old, _, prev_raw, prev_adj = measured("IBM")
        with self.assertRaisesRegex(ValueError, "does not confirm ex-dividend"):
            call("IBM", data, old, {"pc": 246.11}, prev_raw, prev_adj,
                 calendar=ReviewedCalendar())

    def test_no_rebase_short_circuit_is_unchanged(self):
        _, data, old, quote, prev_raw, prev_adj = measured("AMP")
        data["adjusted"][ANCHOR] = data["raw"][ANCHOR]
        self.assertIsNone(call("AMP", data, old, quote, prev_raw, prev_adj,
                               calendar=ReviewedCalendar()))


class ProductionWiringTest(unittest.TestCase):

    def test_prepare_passes_reviewed_calendar_into_the_audit(self):
        text = Path("prepare_live_ranking.py").read_text(encoding="utf-8")
        self.assertIn("from v_market_calendar", text)
        self.assertIn("prev_raw,prev_adj,session,calendar)", text)
        self.assertIn("calendar.sha!=q.get('calendar_sha256')", text)

    def test_weekend_is_not_a_reviewed_session(self):
        self.assertIsNone(ReviewedCalendar().bounds(dt.date(2026, 11, 7)))
        self.assertIsNotNone(ReviewedCalendar().bounds(dt.date(2026, 11, 9)))


if __name__ == "__main__":
    unittest.main()
