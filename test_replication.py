# -*- coding: utf-8 -*-
"""一主两备改造的纯函数回归：写入路由、规范哈希、复制校验口径。

不访问网络、不依赖本地凭据，可在 CI 与本机无条件运行。
"""
import base64
import hashlib
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

import db_sources
import export_site_snapshot as ess
import sync_site_dataset as sync
import verify_replication as verify

BASE = os.path.dirname(os.path.abspath(__file__))


class TestWriteRouting(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop("DB_TARGET", None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("DB_TARGET", None)
        else:
            os.environ["DB_TARGET"] = self._saved

    def test_default_target_is_supabase(self):
        self.assertEqual(db_sources.current_target(), "supabase")

    def test_auto_and_neon_are_accepted(self):
        os.environ["DB_TARGET"] = "auto"
        self.assertEqual(db_sources.current_target(), "auto")
        os.environ["DB_TARGET"] = "neon"
        self.assertEqual(db_sources.current_target(), "neon")

    def test_unknown_target_is_rejected_before_connecting(self):
        with self.assertRaises(ValueError):
            db_sources.db("bogus")


class TestReplicationContract(unittest.TestCase):
    def test_required_keys_match_frontend_contract(self):
        self.assertEqual(verify.REQUIRED, set(ess.ALL_KEYS))
        self.assertEqual(len(verify.REQUIRED), 14)

    def test_payload_digest_is_canonical_and_stable(self):
        datasets = {k: {"b": 2, "a": 1} for k in ess.ALL_KEYS}
        meta = {k: {"updated_at": "2026-11-08T00:00:00Z"} for k in ess.ALL_KEYS}
        rows = sync.build_rows(datasets, meta)
        self.assertEqual(len(rows), 14)
        expected = hashlib.sha256(
            json.dumps({"a": 1, "b": 2}, ensure_ascii=False,
                       sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        for row in rows:
            self.assertEqual(row[3], expected)
            self.assertEqual(json.loads(row[1]), {"a": 1, "b": 2})

    def test_neon_dsn_drops_unsupported_channel_binding(self):
        os.environ["NEON_DSN"] = (
            "postgresql://u:p@host/db?sslmode=require&channel_binding=require")
        try:
            self.assertEqual(sync.neon_dsn(), "postgresql://u:p@host/db?sslmode=require")
        finally:
            os.environ.pop("NEON_DSN", None)


class TestPublishMigration(unittest.TestCase):
    """迁移首轮：旧 Pages artifact 残留 snapshot.json 时发布不能被误判阻断。

    2026-11-08：KEEP_STATIC 收窄为 {cloud} 后，若把 snapshot.json 当成
    数据集，collect() 会缺少 4 个必需 key，publish_site_dataset.py 直接
    fail-closed，网页发布 workflow 必然失败；更糟的是它还会被写回在线库。
    """

    @staticmethod
    def _legacy_artifact(d):
        Path(d, "cloud.json").write_text(
            json.dumps({"url": "https://example.supabase.co", "key": "public"}),
            encoding="utf-8")
        Path(d, "snapshot.json").write_text(
            json.dumps({"schema": 1, "source": "postgres-direct",
                        "datasets": {"curve": [1, 2, 3]}}), encoding="utf-8")

    def test_legacy_snapshot_is_never_read_as_a_dataset(self):
        import publish_site_dataset as psd
        with tempfile.TemporaryDirectory() as d:
            self._legacy_artifact(d)
            records = psd.collect(d)
            self.assertNotIn("snapshot", records)
            self.assertNotIn("cloud", records)
            self.assertIn("sectors", records)   # 由 sector_dataset() 合成
            artifact_keys = {p.relative_to(d).with_suffix("").as_posix()
                             for p in Path(d).rglob("*.json")}
            self.assertEqual(artifact_keys - psd.KEEP_STATIC - psd.LEGACY_MIRRORS,
                             set(), "迁移首轮必须被识别为 online-only 稳态")

    def test_main_accepts_legacy_artifact_as_online_only_and_prunes_it(self):
        """端到端模拟发布步骤：稳态放行、清理旧镜像、只留引导配置。"""
        import publish_site_dataset as psd
        with tempfile.TemporaryDirectory() as d:
            self._legacy_artifact(d)
            saved_argv, saved_check = sys.argv, psd.healthy_source
            psd.healthy_source = lambda: True      # 在线库齐备，本机不连库
            sys.argv = ["publish_site_dataset.py", "--source", d, "--prune", "--keys"]
            try:
                self.assertEqual(psd.main(), 0)
            finally:
                sys.argv, psd.healthy_source = saved_argv, saved_check
            self.assertFalse(Path(d, "snapshot.json").exists())
            self.assertTrue(Path(d, "cloud.json").exists())

    def test_prune_deletes_legacy_snapshot_and_keeps_only_cloud(self):
        import publish_site_dataset as psd
        with tempfile.TemporaryDirectory() as d:
            self._legacy_artifact(d)
            removed, keep = psd.prune_files(d)
            self.assertEqual(removed, 1)
            self.assertEqual(keep, ["cloud.json"])
            self.assertFalse(Path(d, "snapshot.json").exists())
            self.assertTrue(all(k in psd.MIRROR_FILES for k in keep))


class TestPublicConfig(unittest.TestCase):
    """公开引导配置 fail-closed：只读 token、副本一致、写 token 拒绝发布。

    2026-11-08 事故复盘：负向演练曾把 rw token 临时写进 site_src/cloud.json，
    site_build.py 先 copy_site() 后校验，导致 docs/cloud.json 残留写 token。
    以下用例把「校验先于产出 + 全仓库无写 token」变成可回归的硬约束。
    """

    JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
    TEXT_EXT = (".py", ".js", ".mjs", ".json", ".yml", ".yaml", ".md", ".html", ".css")

    @staticmethod
    def _claims(token):
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))

    @classmethod
    def _jwt(cls, claim):
        payload = base64.urlsafe_b64encode(
            json.dumps({"a": claim}).encode("utf-8")).decode("ascii").rstrip("=")
        return "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9." + payload + ".AAAA"

    @staticmethod
    def _sample_config(claim):
        return {
            "url": "https://example.supabase.co",
            "key": "sb_publishable_test",
            "endpoints": [
                {"type": "supabase", "url": "https://example.supabase.co",
                 "key": "sb_publishable_test"},
                {"type": "turso", "url": "https://example.turso.io",
                 "key": TestPublicConfig._jwt(claim), "table": "site_dataset"},
            ],
        }

    def test_site_src_endpoints_are_readonly(self):
        with open(os.path.join(BASE, "site_src", "cloud.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        self.assertTrue(str(cfg.get("key", "")).startswith("sb_publishable_"))
        eps = cfg.get("endpoints") or []
        self.assertEqual([e.get("type") for e in eps], ["supabase", "turso"])
        for ep in eps:
            self.assertTrue(str(ep.get("url", "")).startswith("https://"))
            if ep.get("type") == "turso":
                self.assertEqual(self._claims(ep["key"]).get("a"), "ro")
            else:
                self.assertTrue(str(ep.get("key", "")).startswith("sb_publishable_"))

    def test_tracked_cloud_copies_are_semantically_identical(self):
        paths = ["site_src/cloud.json", "static/cloud.json", "docs/cloud.json"]
        generated = "docs/data/cloud.json"
        if os.path.exists(os.path.join(BASE, generated)):
            paths.append(generated)
        configs = []
        for rel in paths:
            with open(os.path.join(BASE, rel), encoding="utf-8") as fh:
                configs.append(json.load(fh))
        for rel, cfg in zip(paths[1:], configs[1:]):
            self.assertEqual(cfg, configs[0], "%s 与发布配置不一致" % rel)

    def test_no_write_token_in_public_text(self):
        """扫描所有会随仓库/Pages 公开的文本，任何可解码 JWT 都不得带 a=rw。"""
        scanned = 0
        for root in ("site_src", "static", "docs", ".github"):
            for cur, _dirs, files in os.walk(os.path.join(BASE, root)):
                for fn in files:
                    if not fn.endswith(self.TEXT_EXT):
                        continue
                    path = os.path.join(cur, fn)
                    if os.path.getsize(path) > 2 * 1024 * 1024:
                        continue
                    with open(path, encoding="utf-8", errors="ignore") as fh:
                        text = fh.read()
                    scanned += 1
                    for token in self.JWT_RE.findall(text):
                        try:
                            claims = self._claims(token)
                        except Exception:
                            continue
                        self.assertNotEqual(claims.get("a"), "rw",
                                            "发现 Turso 写 token：%s" % os.path.relpath(path, BASE))
        for fn in os.listdir(BASE):
            if not fn.endswith(self.TEXT_EXT):
                continue
            path = os.path.join(BASE, fn)
            if not os.path.isfile(path) or os.path.getsize(path) > 2 * 1024 * 1024:
                continue
            with open(path, encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
            scanned += 1
            for token in self.JWT_RE.findall(text):
                try:
                    claims = self._claims(token)
                except Exception:
                    continue
                self.assertNotEqual(claims.get("a"), "rw", "发现 Turso 写 token：%s" % fn)
        self.assertGreater(scanned, 10)

    def test_write_cloud_config_rejects_write_token_without_emitting_files(self):
        import site_build
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "site_src"))
            with open(os.path.join(d, "site_src", "cloud.json"), "w", encoding="utf-8") as fh:
                json.dump(self._sample_config("rw"), fh)
            saved = site_build.BASE
            site_build.BASE = d
            try:
                with self.assertRaises(SystemExit):
                    site_build.write_cloud_config(os.path.join(d, "out", "data"))
                self.assertFalse(os.path.exists(os.path.join(d, "out", "data", "cloud.json")))
                self.assertFalse(os.path.exists(os.path.join(d, "out", "cloud.json")))
            finally:
                site_build.BASE = saved

    def test_write_cloud_config_writes_both_copies_for_readonly_token(self):
        import site_build
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "site_src"))
            with open(os.path.join(d, "site_src", "cloud.json"), "w", encoding="utf-8") as fh:
                json.dump(self._sample_config("ro"), fh)
            saved = site_build.BASE
            site_build.BASE = d
            try:
                self.assertTrue(site_build.write_cloud_config(os.path.join(d, "out", "data")))
                with open(os.path.join(d, "out", "data", "cloud.json"), encoding="utf-8") as fh:
                    a = json.load(fh)
                with open(os.path.join(d, "out", "cloud.json"), encoding="utf-8") as fh:
                    b = json.load(fh)
                self.assertEqual(a, b)
                self.assertEqual(len(a["endpoints"]), 2)
                self.assertEqual(a["endpoints"][1]["table"], "site_dataset")
            finally:
                site_build.BASE = saved


if __name__ == "__main__":
    unittest.main()
