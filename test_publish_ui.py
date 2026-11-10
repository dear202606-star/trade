"""发布链路容错回归：Pages 基线 5xx 不得阻断 build / deploy。"""
import io
import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

import publish_ui


def response(obj):
    return io.BytesIO(json.dumps(obj).encode("utf-8"))


def http_error(code):
    return urllib.error.HTTPError("https://example.invalid/", code,
                                  "Service Unavailable", {}, io.BytesIO(b""))


class FetchJsonTests(unittest.TestCase):
    def test_404_is_treated_as_migrated_baseline_not_failure(self):
        with patch.object(publish_ui.urllib.request, "urlopen",
                          side_effect=http_error(404)) as call:
            self.assertIsNone(publish_ui.fetch_json("https://example.invalid/x"))
        self.assertEqual(call.call_count, 1)

    def test_transient_503_is_retried_then_succeeds(self):
        with patch.object(publish_ui.urllib.request, "urlopen",
                          side_effect=[http_error(503), response({"ok": 1})]) as call, \
             patch.object(publish_ui.time, "sleep"):
            self.assertEqual(publish_ui.fetch_json("https://example.invalid/x"), {"ok": 1})
        self.assertEqual(call.call_count, 2)

    def test_persistent_5xx_degrades_to_skip_instead_of_crashing(self):
        """2026-11-10 线上事故：503 直接抛错 → build 失败 → deploy skipped。"""
        with patch.object(publish_ui.urllib.request, "urlopen",
                          side_effect=[http_error(503)] * 4) as call, \
             patch.object(publish_ui.time, "sleep"):
            self.assertIsNone(publish_ui.fetch_json("https://example.invalid/x"))
        self.assertEqual(call.call_count, 4)

    def test_network_timeout_degrades_to_skip(self):
        with patch.object(publish_ui.urllib.request, "urlopen",
                          side_effect=TimeoutError("slow")), \
             patch.object(publish_ui.time, "sleep"):
            self.assertIsNone(publish_ui.fetch_json("https://example.invalid/x"))

    def test_non_retryable_http_error_still_fails_closed(self):
        with patch.object(publish_ui.urllib.request, "urlopen",
                          side_effect=http_error(403)):
            with self.assertRaises(urllib.error.HTTPError):
                publish_ui.fetch_json("https://example.invalid/x")

    def test_valid_payload_is_returned(self):
        with patch.object(publish_ui.urllib.request, "urlopen",
                          return_value=response({"position": [1]})):
            self.assertEqual(publish_ui.fetch_json("https://example.invalid/x"),
                             {"position": [1]})


class GhRetryTests(unittest.TestCase):
    def test_github_api_5xx_is_retried(self):
        fail = MagicMock(returncode=1, stdout="", stderr="HTTP 503")
        ok = MagicMock(returncode=0, stdout='{"artifacts":[]}', stderr="")
        with patch.object(publish_ui.subprocess, "run",
                          side_effect=[fail, ok]) as call, \
             patch.object(publish_ui.time, "sleep"):
            self.assertEqual(publish_ui.gh("api", "repos/x/actions/artifacts"),
                             '{"artifacts":[]}')
        self.assertEqual(call.call_count, 2)

    def test_persistent_github_failure_raises_with_context(self):
        fail = MagicMock(returncode=1, stdout="", stderr="HTTP 503")
        with patch.object(publish_ui.subprocess, "run", return_value=fail), \
             patch.object(publish_ui.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
                publish_ui.gh("api", "repos/x/actions/artifacts", attempts=2)


if __name__ == "__main__":
    unittest.main()
