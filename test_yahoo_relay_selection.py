"""Relay selection for the historical Yahoo backfill.

2026-11-21: `fill_yahoo_gaps.py` runs every 6 hours and writes 0 rows. Measured
against the live endpoints, the proxies regularly drop `period1/period2` and
answer with the last few monthly bars (BBBY 5, EQR 2, APC 11 — the same numbers
the CI job logs as "月份不足（5/2/11）"), while the identical request through
another channel returns the whole window (SPY/T/AAPL: 203 bars). The old
`if out: return out` treated a truncation as success and returned immediately, so
a better channel was never consulted, `check_payload` rejected the stub, the job
stayed red, and the next cycle paid the same egress for the same nothing.

Separately, `tries=4` over a 5-channel fleet with a global rotating cursor meant
the channel holding the complete answer could go untried in a given call.

These tests pin the two invariants the fix rests on: the *most complete* response
wins, and one sweep covers every channel exactly once — stopping early as soon as
a response already reaches the start of the requested window, so traffic cannot
grow for symbols that were served correctly on the first try.
"""
import datetime as dt
import json
import unittest
from unittest.mock import patch

import yahoo_fetch as YF

FIRST, SECOND = YF.CHANNELS[0][0], YF.CHANNELS[1][0]


def bars_json(months):
    """Minimal Yahoo chart payload covering exactly `months`."""
    ts = [int(dt.datetime.fromisoformat(m + "-01T00:00:00+00:00").timestamp())
          for m in months]
    adj = [10.0 + i for i in range(len(months))]
    return json.dumps({"chart": {"result": [{"timestamp": ts, "indicators": {
        "adjclose": [{"adjclose": adj}], "quote": [{"close": adj}]}}]}})


def month_range(start, count):
    year, month, out = int(start[:4]), int(start[5:]), []
    for _ in range(count):
        out.append("%04d-%02d" % (year, month))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


class RelaySelection(unittest.TestCase):
    def setUp(self):
        YF._cooldown.clear()
        YF._idx["i"] = 0
        self.addCleanup(YF._cooldown.clear)
        self.sleeps = patch.object(YF.time, "sleep").start()
        self.addCleanup(patch.stopall)

    def fleet(self, **overrides):
        """Every relay answers with `overrides`; the rest are down."""
        base = {name: OSError("down") for name, _ in YF.CHANNELS}
        base.update(overrides)
        return base

    def serve(self, by_channel):
        """Patch urlopen so each named channel answers with text or an exception."""
        seen = []

        class Response:
            def __init__(self, text):
                self.text = text

            def read(self):
                return self.text.encode("utf-8")

        def open(request, timeout=None):
            url = request.full_url
            # startswith, not substring: every relay URL embeds Yahoo's own host.
            channel = next((name for name, tpl in YF.CHANNELS
                            if url.startswith(tpl.split("%s")[0])), None)
            if channel is None:
                raise AssertionError("url matches no channel: " + url[:70])
            self.assertIn(channel, by_channel, "unexpected channel in rotation")
            seen.append(channel)
            payload = by_channel[channel]
            if isinstance(payload, Exception):
                raise payload
            return Response(payload)

        return patch.object(YF.urllib.request, "urlopen", open), seen

    def test_truncated_relay_loses_to_the_complete_one(self):
        handler, seen = self.serve(self.fleet(
            **{FIRST: bars_json(month_range("2025-08", 5)),
               SECOND: bars_json(month_range("2010-01", 30))}))
        with handler:
            got = YF.fetch("BBBY")
        self.assertEqual(len(got), 30)                 # not the 5-bar stub
        self.assertEqual(seen, [FIRST, SECOND])        # rotation was not short-circuited

    def test_sweep_covers_the_last_channel(self):
        # tries=4 over 5 channels meant a complete answer on the last relay was
        # unreachable from a call that started mid-rotation.
        self.assertEqual(YF.SWEEP_TRIES, len(YF.CHANNELS))
        last = YF.CHANNELS[-1][0]
        handler, seen = self.serve(self.fleet(**{last: bars_json(month_range("2010-01", 40))}))
        with handler:
            got = YF.fetch("APC")
        self.assertIn(last, seen)
        self.assertEqual(len(got), 40)

    def test_rotation_sweeps_the_fleet_once_and_no_more(self):
        handler, seen = self.serve(self.fleet())
        with handler:
            self.assertEqual(YF.fetch("AAPL"), {})     # total outage stays fail-closed
        self.assertEqual(len(seen), len(YF.CHANNELS))
        self.assertEqual(len(seen), YF.SWEEP_TRIES)

    def test_complete_first_response_stops_the_rotation(self):
        handler, seen = self.serve(self.fleet(**{FIRST: bars_json(month_range("2010-01", 200))}))
        with handler:
            got = YF.fetch("AAPL")
        self.assertEqual(len(got), 200)
        self.assertEqual(seen, [FIRST])                # no extra egress when history is whole

    def test_every_relay_truncated_returns_the_longest_for_validation(self):
        # Nothing may be written from these, so check_payload must still see and
        # reject them; returning the best partial keeps the rejection informative.
        answers = {name: bars_json(month_range("2025-08", 5)) for name, _ in YF.CHANNELS}
        answers[SECOND] = bars_json(month_range("2025-04", 8))
        handler, _ = self.serve(self.fleet(**answers))
        with handler:
            got = YF.fetch("EQR")
        self.assertEqual(len(got), 8)

    def test_authoritative_404_stops_rotating_without_discarding_evidence(self):
        # Yahoo's own "Not Found" (delisted symbol) means no relay can do better,
        # so the rotation must stop — but a partial taken earlier must survive for
        # check_payload to reject as "月份不足（5）" instead of degrading to "空数据".
        fourth = YF.CHANNELS[3][0]
        handler, seen = self.serve(self.fleet(
            **{FIRST: bars_json(month_range("2025-08", 5)),
               fourth: OSError("HTTP Error 404: Not Found")}))
        with handler:
            got = YF.fetch("ATVI")
        self.assertEqual(len(got), 5)
        self.assertEqual(len(seen), 4)                 # stopped at the 404, fifth channel untouched

    def test_total_absence_is_reported_as_absent_not_as_an_outage(self):
        # fill_yahoo_gaps 要靠这个区分结案 404 与机群故障：前者补不出任何东西，
        # 每 6 小时重试一次只是白烧流量；后者必须继续重试。
        fourth = YF.CHANNELS[3][0]
        handler, seen = self.serve(self.fleet(
            **{FIRST: OSError("down"), fourth: OSError("HTTP Error 404: Not Found")}))
        with handler:
            with self.assertRaises(YF.SymbolNotFound):
                YF.fetch("BBY")
        self.assertEqual(len(seen), 4)                 # rotated on, then stopped at the 404

    def test_requested_window_start_is_the_reach_reference(self):
        self.assertEqual(YF.WINDOW_START_MONTH, "2010-01")
        self.assertTrue(YF.reaches_window_start({"2010-01": (1., 1.)}))
        self.assertTrue(YF.reaches_window_start({"2009-06": (1., 1.)}))
        self.assertFalse(YF.reaches_window_start({"2025-08": (1., 1.)}))
        self.assertFalse(YF.reaches_window_start({}))


if __name__ == "__main__":
    unittest.main()
