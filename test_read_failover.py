"""通路限额/故障自动切换回归（2026-11-10）。

边界（勿回退）：
  · **读侧**全自动：浏览器 Supabase REST → Turso → localStorage；
    同步链路的展示数据读源 主库 → Neon 也自动降级。
  · **写侧绝不自动切换**：让两个库同时接受同一批次写入会产生无法
    合并的分叉，DB_TARGET 默认仍是 supabase，提升/回切必须人工单向
    回放（db_failover.py）。自动降级只发生在「读」和「复制到备库」。
  · 降级必须防回退：Neon 是日更冷备，滞后于 Turso；任何「候选数据
    不比现有数据新」的情况一律 fail-closed，保留现有页面数据并告警。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import sync_site_dataset as s

BASE = Path(__file__).resolve().parent


def args(**kw):
    base = dict(from_archive="", from_neon=False, read_source="auto",
                targets="turso,neon", dry_run=False, force=False)
    base.update(kw)
    return argparse.Namespace(**base)


def datasets(ts):
    return ({"quotes": {"ts": ts, "ok": True, "quotes": {"SPY": {}}}}, {"quotes": {}})


class ResolveDatasetsTests(unittest.TestCase):
    def test_auto_uses_primary_when_healthy(self):
        with patch.object(s.ess, "collect", return_value=datasets("2026-11-09T21:00:00Z")), \
             patch.object(s, "collect_from_neon", side_effect=AssertionError("不应降级")):
            d, _m, source, degraded, reason = s.resolve_datasets(
                args(), "https://turso", "token")
        self.assertEqual(source, "supabase")
        self.assertFalse(degraded)
        self.assertEqual(reason, "")
        self.assertEqual(d["quotes"]["ts"], "2026-11-09T21:00:00Z")

    def test_auto_falls_back_to_neon_when_primary_is_unreadable(self):
        with patch.object(s.ess, "collect", side_effect=RuntimeError("egress 402")), \
             patch.object(s, "neon_dsn", return_value="dsn"), \
             patch.object(s, "collect_from_neon",
                          return_value=datasets("2026-11-09T21:00:00Z")), \
             patch.object(s, "turso_quotes_ts", return_value="2026-11-06T21:00:00Z"):
            _d, _m, source, degraded, reason = s.resolve_datasets(
                args(), "https://turso", "token")
        self.assertEqual(source, "neon")
        self.assertTrue(degraded)
        self.assertIn("egress 402", reason)

    def test_stale_neon_never_overwrites_newer_turso(self):
        with patch.object(s.ess, "collect", side_effect=RuntimeError("primary down")), \
             patch.object(s, "neon_dsn", return_value="dsn"), \
             patch.object(s, "collect_from_neon",
                          return_value=datasets("2026-11-06T21:00:00Z")), \
             patch.object(s, "turso_quotes_ts", return_value="2026-11-09T21:00:00Z"):
            with self.assertRaisesRegex(RuntimeError, "拒绝用滞后的 Neon"):
                s.resolve_datasets(args(), "https://turso", "token")

    def test_equal_timestamp_is_also_rejected(self):
        with patch.object(s.ess, "collect", side_effect=RuntimeError("primary down")), \
             patch.object(s, "neon_dsn", return_value="dsn"), \
             patch.object(s, "collect_from_neon",
                          return_value=datasets("2026-11-09T21:00:00Z")), \
             patch.object(s, "turso_quotes_ts", return_value="2026-11-09T21:00:00Z"):
            with self.assertRaisesRegex(RuntimeError, "拒绝用滞后的 Neon"):
                s.resolve_datasets(args(), "https://turso", "token")

    def test_candidate_without_timestamp_is_rejected(self):
        with patch.object(s.ess, "collect", side_effect=RuntimeError("primary down")), \
             patch.object(s, "neon_dsn", return_value="dsn"), \
             patch.object(s, "collect_from_neon",
                          return_value=({"quotes": {"ok": True}}, {})), \
             patch.object(s, "turso_quotes_ts", return_value="2026-11-06T21:00:00Z"):
            with self.assertRaisesRegex(RuntimeError, "缺少可核验的行情时间戳"):
                s.resolve_datasets(args(), "https://turso", "token")

    def test_unreadable_turso_fails_closed(self):
        with patch.object(s.ess, "collect", side_effect=RuntimeError("primary down")), \
             patch.object(s, "neon_dsn", return_value="dsn"), \
             patch.object(s, "collect_from_neon",
                          return_value=datasets("2026-11-09T21:00:00Z")), \
             patch.object(s, "turso_quotes_ts", side_effect=RuntimeError("turso down")):
            with self.assertRaises(RuntimeError):
                s.resolve_datasets(args(), "https://turso", "token")

    def test_explicit_sources_skip_auto_decision(self):
        with patch.object(s.ess, "collect", return_value=datasets("2026-11-09T21:00:00Z")):
            self.assertEqual(s.resolve_datasets(args(read_source="supabase"), "u", "t")[2],
                             "supabase")
        with patch.object(s, "neon_dsn", return_value="dsn"), \
             patch.object(s, "collect_from_neon",
                          return_value=datasets("2026-11-09T21:00:00Z")):
            self.assertEqual(s.resolve_datasets(args(read_source="neon"), "u", "t")[2],
                             "neon")


class TursoTimestampTests(unittest.TestCase):
    def test_parses_json_string_payload(self):
        payload = json.dumps({"ts": "2026-11-09T21:00:00Z", "ok": True})
        with patch.object(s, "turso_execute", return_value=[[[payload]]]):
            self.assertEqual(s.turso_quotes_ts("u", "t"), "2026-11-09T21:00:00Z")

    def test_missing_row_returns_none(self):
        with patch.object(s, "turso_execute", return_value=[[]]):
            self.assertIsNone(s.turso_quotes_ts("u", "t"))


class BoundaryTests(unittest.TestCase):
    """两侧边界的结构断言：读侧自动、写侧不自动。"""

    def test_write_target_defaults_to_primary(self):
        import db_sources
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(db_sources.current_target(), "supabase")
        self.assertIn("人工", db_sources.db.__doc__)
        self.assertIn("绝不双向合并", db_sources.db.__doc__)

    def test_visitor_read_chain_has_circuit_breaker_and_two_endpoints(self):
        app = (BASE / "site_src" / "assets" / "app.js").read_text(encoding="utf-8")
        cloud = json.loads((BASE / "docs" / "cloud.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(cloud["endpoints"]), 2)
        types = [e.get("type") for e in cloud["endpoints"]]
        self.assertIn("supabase", types)
        self.assertIn("turso", types)
        for key in ("LS_CIRCUIT", "failEndpoint", "402", "localStorage"):
            self.assertIn(key, app, "前端缺少自动切换要素: " + key)

    def test_sync_workflow_does_not_force_a_read_source(self):
        yml = (BASE / ".github" / "workflows" / "db-sync.yml").read_text(encoding="utf-8")
        # 默认 auto；不得写死 --from-neon，否则主库恢复后仍读冷备
        self.assertNotIn("--from-neon", yml)
        self.assertNotIn("--read-source supabase", yml)


if __name__ == "__main__":
    unittest.main()
