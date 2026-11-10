# -*- coding: utf-8 -*-
"""test_external_heartbeat.py — 平台外心跳安装器的离线契约回归。

全部为纯函数测试：不连数据库、不发网络请求、不需要 psycopg2，
因此可以直接放进只装标准库的 reconcile.yml 自检步骤。
"""
import json
import unittest

import external_heartbeat as eh


class HeartbeatContract(unittest.TestCase):
    def test_default_interval_is_low_frequency(self):
        # 2026-11-06 事故：每 5 分钟派发整条采集链，1139 次/10 天。
        # 心跳必须低频，且硬下限不许被调成 1 分钟。
        self.assertTrue(eh.MIN_TICK_MIN <= eh.DEFAULT_TICK_MIN <= eh.MAX_TICK_MIN)
        self.assertEqual(eh.DEFAULT_TICK_MIN, 20)
        self.assertEqual(eh.cron_expression(20), "*/20 * * * *")
        with self.assertRaises(ValueError):
            eh.cron_expression(1)
        with self.assertRaises(ValueError):
            eh.cron_expression(61)

    def test_mode_selection_prefers_repository_dispatch(self):
        self.assertEqual(eh.choose_mode({"repository_dispatch": 204,
                                         "workflow_dispatch": 204}),
                         "repository_dispatch")
        # 旧 fine-grained PAT 常只有 actions:write（无 contents:write），
        # repository_dispatch 会 403，必须能降级到 workflow_dispatch。
        self.assertEqual(eh.choose_mode({"repository_dispatch": 403,
                                         "workflow_dispatch": 204}),
                         "workflow_dispatch")
        self.assertIsNone(eh.choose_mode({"repository_dispatch": 401,
                                          "workflow_dispatch": 403}))
        self.assertIsNone(eh.choose_mode({"repository_dispatch": "error:URLError"}))

    def test_sql_never_contains_plaintext_token(self):
        for mode in eh.ENDPOINTS:
            cmd = eh.build_command(mode)
            self.assertIn("vault.decrypted_secrets", cmd)
            self.assertIn(eh.VAULT_SECRET, cmd)
            self.assertIn("Bearer ' ||", cmd)
            self.assertIn(eh.ENDPOINTS[mode], cmd)
            self.assertNotIn("github_pat_", cmd)
            self.assertNotIn("ghp_", cmd)
            # 只唤起对账，绝不派发采集工作流
            self.assertNotIn("quotes.yml", cmd)

    def test_dispatch_bodies_only_wake_reconcile(self):
        cmd = eh.build_command("repository_dispatch")
        self.assertIn("reconcile-tick", cmd)
        self.assertIn("event_type", cmd)
        self.assertEqual(eh.BODIES["repository_dispatch"]["event_type"], "reconcile-tick")
        self.assertEqual(eh.BODIES["repository_dispatch"]["client_payload"]["source"], "pg-cron")
        wf = eh.BODIES["workflow_dispatch"]
        self.assertEqual(wf["ref"], "main")
        self.assertEqual(wf["inputs"]["source"], "pg-cron")
        self.assertEqual(json.loads(json.dumps(wf)), wf)

    def test_enable_sql_is_idempotent_and_scoped(self):
        sql = eh.build_enable_sql("repository_dispatch", 20)
        self.assertIn("cron.unschedule", sql)
        self.assertIn("cron.schedule", sql)
        self.assertIn("*/20 * * * *", sql)
        self.assertIn(eh.JOB, sql)
        # 绝不恢复 11-06 停掉的两个高频采集作业
        self.assertNotIn("trade-dispatch-market", sql)
        self.assertNotIn("quotes.yml", sql)
        self.assertNotIn("21 * * *", sql)

    def test_disable_only_removes_own_job(self):
        sql = eh.build_disable_sql()
        self.assertIn(eh.JOB, sql)
        self.assertIn("cron.unschedule", sql)
        self.assertNotIn("trade-dispatch", sql.replace(eh.JOB, ""))

    def test_workflow_file_contract(self):
        """安装入口必须是手动派发，且不被 GitHub cron 自动改动线上调度。"""
        path = eh.BASE / ".github" / "workflows" / "heartbeat-setup.yml"
        text = path.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("schedule:", text)
        self.assertIn("SUPABASE_DB_PASSWORD", text)
        self.assertIn("test_external_heartbeat", text)
        self.assertIn("rm -f output/_supabase_dbpass.txt", text)

    def test_reconcile_accepts_external_dispatch(self):
        path = eh.BASE / ".github" / "workflows" / "reconcile.yml"
        text = path.read_text(encoding="utf-8")
        self.assertIn("repository_dispatch:", text)
        self.assertIn("types: [reconcile-tick]", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("run-name:", text)
        self.assertIn("github.event.inputs.source", text)
        self.assertIn("external_heartbeat.py --health", text)

    def test_health_only_counts_real_heartbeats(self):
        now = "2026-11-10T16:00:00+00:00"
        freshest = [{"displayTitle": "场次对账 · pg-cron",
                     "event": "workflow_dispatch",
                     "createdAt": "2026-11-10T15:40:00Z"}]
        self.assertTrue(eh.health(freshest, now)["healthy"])
        manual = [{"displayTitle": "场次对账 · manual",
                   "event": "workflow_dispatch",
                   "createdAt": "2026-11-10T15:59:00Z"}]
        self.assertFalse(eh.health(manual, now)["healthy"])
        self.assertTrue(eh.is_tick_run({"displayTitle": "场次对账 · pg-cron",
                                        "event": "workflow_dispatch"}))
        self.assertFalse(eh.is_tick_run({"displayTitle": "场次对账 · schedule",
                                         "event": "schedule"}))
        self.assertTrue(eh.is_tick_run({"event": "repository_dispatch"}))


if __name__ == "__main__":
    unittest.main()
