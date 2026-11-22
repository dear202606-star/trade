"""Relay selection for the historical Yahoo backfill.

2026-11-21: `fill_yahoo_gaps.py` runs every 6 hours and writes 0 rows. The old
`if out: return out` treated *any* non-empty response as the end of the search, so
whichever channel answered first decided the result — a 400, a timeout or a partial
window included — and a better channel was never consulted. `check_payload` then
rejected the stub, the job stayed red, and the next cycle paid the same egress for
the same nothing. Separately, `tries=4` over a 5-channel fleet with a global
rotating cursor meant the channel that could answer went untried in a given call.

Those two defects were real; the story told about them the first time was not. See
the paragraph below, and keep the distinction: a sweep that returns the longest
answer is only correct behaviour if a longer answer exists somewhere.

These tests pin the invariants the fix rests on: the *most complete* response wins,
and one sweep covers every channel exactly once — stopping early as soon as a
response already reaches the start of the requested window, so traffic cannot grow
for symbols that were served correctly on the first try.

The next round of evidence (dispatch 44914876045) took failures 135→67 but renamed
what was left: `HTTP 400` from codetabs plus 「月份不足」. They turned out to have
different causes. The 400 is ours: the inner query was hung on the proxy's *own* URL,
so Yahoo received a parameter-less chart request. The short answers are Yahoo's — SATS
and BBBY report meta.firstTradeDate 2026-10 / 2026-07, i.e. relisted tickers whose
history really does start after the window, so no channel can add months. And a
delisted code answers HTTP 200 with the verdict inside `chart.error`, which the old
code hit as a TypeError and mistook for a dead channel. So a sweep must emit one
correctly encoded URL per channel, and an authoritative "no data" must stop rotating
and reach the absence branch — otherwise 65 delisted symbols keep paying for a sweep
that can never produce anything.
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


def not_found_body():
    """Yahoo's own verdict for a delisted code, carried in an HTTP 200 body.

    2026-11-21 取证（WFM 经 allorigins）：状态码是 200，缺席写在 chart.error 里，
    result 为 null。旧代码对 result 下标抛 TypeError，被当成通道故障继续轮换。
    """
    return json.dumps({"chart": {"result": None, "error": {
        "code": "Not Found", "description": "No data found, symbol may be delisted"}}})


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

    def serve(self, by_channel, urls=None):
        """Patch urlopen so each named channel answers with text or an exception.

        `urls` collects the full request URL of every call, in the same order as
        `seen`, so a test can assert on how the query was assembled rather than
        only on which channel answered.
        """
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
            if urls is not None:
                urls.append(url)
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

    def test_proxy_receivers_get_their_inner_query_encoded_whole(self):
        # 一次全机群扫挂，就能同时看到 5 条通道各自拼出的 URL。
        # 旧写法 `tpl % sym + "&" + qs` 把 period1/period2 挂在代理自己的 query 上，
        # Yahoo 收到的是无参 chart 请求——codetabs 因此稳定 400 Bad Request。
        urls = []
        handler, seen = self.serve(self.fleet(), urls)
        with handler:
            YF.fetch("AAPL")
        proxied = {name: url for name, url in zip(seen, urls)
                   if "?quest=" in url or "?url=" in url}
        self.assertEqual(sorted(proxied), ["allorigins", "codetabs"])
        for name, url in proxied.items():
            self.assertIn("%3Fperiod1%3D", url, name)
            self.assertNotIn("&period2=", url, "%s 把区间参数漏在了外层" % name)
        plain = [url for name, url in zip(seen, urls) if name not in proxied]
        self.assertEqual(len(plain), len(YF.CHANNELS) - 2)
        for url in plain:
            self.assertIn("?period1=", url)

    def test_yahoo_symbol_spelling_reaches_every_channel(self):
        # _sym 只在下标处生效过一次；代理通道的 %s 同样得吃 BRK-B，否则退市 B 股查无此人。
        urls = []
        handler, _ = self.serve(self.fleet(), urls)
        with handler:
            YF.fetch("BRK.B")
        self.assertEqual(len(urls), len(YF.CHANNELS))
        for url in urls:
            self.assertIn("BRK-B", url)
            self.assertNotIn("BRK.B", url)

    def test_absence_in_a_200_body_is_the_authoritative_kind(self):
        # 状态码 200 不代表拿到了数据：result 为 null + chart.error 必须是缺席，
        # 而不是「通道故障、冷却 15 秒再试」——后者让退市票每 6 小时白烧一轮流量。
        fourth = YF.CHANNELS[3][0]
        handler, seen = self.serve(self.fleet(
            **{FIRST: OSError("down"), fourth: not_found_body()}))
        with handler:
            with self.assertRaises(YF.SymbolNotFound):
                YF.fetch("WFM")
        self.assertEqual(len(seen), 4)                 # 扫到缺席即止，不在余下通道续烧

    def test_absence_in_a_200_body_keeps_the_partial_evidence(self):
        fourth = YF.CHANNELS[3][0]
        handler, _ = self.serve(self.fleet(
            **{FIRST: bars_json(month_range("2025-08", 5)), fourth: not_found_body()}))
        with handler:
            got = YF.fetch("WFM")
        self.assertEqual(len(got), 5)                  # 交给校验判「月份不足（5）」


if __name__ == "__main__":
    unittest.main()
