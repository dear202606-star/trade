"""Keep infrastructure degradation out of the page-data alarm.

The Supabase REST gateway can be unavailable (egress quota 402, throttling,
5xx) while the page itself is perfectly healthy because it reads the CI
direct-connection mirror. Such failures belong in `warnings`; genuine data
problems (stale worker, mismatched evidence, wrong dates) must stay blocking.
"""
import unittest

import requests

from realtime_check import is_gateway_outage


class GatewayOutageTest(unittest.TestCase):

    @staticmethod
    def http_error(code):
        response = requests.Response()
        response.status_code = code
        return requests.HTTPError("HTTP %d" % code, response=response)

    def test_quota_throttle_and_server_errors_are_gateway_outages(self):
        for code in (402, 403, 429, 500, 502, 503, 504):
            with self.subTest(code=code):
                self.assertTrue(is_gateway_outage(self.http_error(code)))

    def test_ordinary_http_and_data_errors_stay_blocking(self):
        for code in (400, 401, 404, 422):
            with self.subTest(code=code):
                self.assertFalse(is_gateway_outage(self.http_error(code)))
        for exc in (ValueError("evidence mismatch"), RuntimeError("stale worker"),
                    OSError("dns failure")):
            with self.subTest(exc=type(exc).__name__):
                self.assertFalse(is_gateway_outage(exc))


if __name__ == "__main__":
    unittest.main()
