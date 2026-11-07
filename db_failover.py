# -*- coding: utf-8 -*-
"""db_failover.py — 写入侧「一主一备」运维剧本（只读，不执行破坏性动作）。

读取侧的故障切换是全自动的（app.js 多端点 + 每端点独立熔断）。
写入侧涉及「谁是主库」这一全局事实，若自动双向合并必然产生冲突，
因此本脚本只提供：状态体检、提升剧本、单向回切剧本。所有实际写操作
都需要运维按剧本显式执行，且回切严格单向（Neon → Supabase）。

用法
  python db_failover.py --status
  python db_failover.py --promote-plan
  python db_failover.py --rebuild-plan
"""
import argparse
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import db_sources  # noqa: E402
import sync_site_dataset as sync  # noqa: E402

PROMOTE_PLAN = """
【提升 Neon 为可写主库】（仅在主库 PostgreSQL 直连也不可用时执行）
  1. 确认 db_failover.py --status 中 supabase=FAIL、neon=OK。
  2. 在 GitHub 仓库 Settings → Variables 增加 DB_TARGET=neon，
     或在相关写入 workflow 的 env 里加 DB_TARGET: neon。
     db_sources.db() 会自动把 ~30 个写入脚本路由到 Neon；
     也可设 DB_TARGET=auto，让每次连接先探测主库、失败自动提升。
  3. 把 db-sync.yml 的同步命令改为：
       python sync_site_dataset.py --from-neon --targets turso
     这样 Turso（浏览器直读备库）改从 Neon 写主库取数，页面继续更新。
  4. 页面本身无需改动：Supabase 402/不可达时 app.js 已自动走 Turso。
  5. 严禁同时让两个库接受同一批次写入；提升期间暂停 Supabase 写入管线。
"""

REBUILD_PLAN = """
【恢复后单向回切】（主库恢复后执行，绝不双向合并）
  1. 先对现状做整库逻辑备份：db_backup.py（主库恢复后也要先留档）。
  2. 用 restore_to_neon.py 的导入逻辑反向执行：从 Neon 导出 24 表
     → 在 Supabase 侧按 tables_schema.sql 建表 → 逐表批量灌回。
     只回放「提升期间 Neon 新增/变更」的数据，不合并两边历史。
  3. 用 verify_replication.py --with-primary 三方比对 14 个展示 key 的
     payload 哈希，全部一致才可继续。
  4. 把 DB_TARGET 改回 supabase，db-sync.yml 去掉 --from-neon，
     触发一次 python sync_site_dataset.py 让 Turso 追平主库。
  5. 观察一个完整交易日（看门狗 PROBLEMS=0）后关闭本次故障单。
"""


def status():
    print("DB_TARGET      =", os.environ.get("DB_TARGET") or "(unset -> supabase)")
    print("解析写入目标   =", db_sources.current_target())
    for target in ("supabase", "neon"):
        ok, detail = db_sources.probe(target)
        print("%-10s = %-4s %s" % (target, "OK" if ok else "FAIL", detail))
    url, token = sync.turso_credentials()
    try:
        rows = sync.turso_execute(url, token, [
            ("select count(*) as n from site_dataset", [])])[0]
        print("Turso 展示 key =", rows[0][0])
    except Exception as exc:
        print("Turso 备库     = FAIL", str(exc)[:120])
    try:
        import psycopg2
        conn = psycopg2.connect(sync.neon_dsn(), connect_timeout=20)
        try:
            with conn.cursor() as cur:
                cur.execute("select count(*) from public.site_dataset_mirror")
                print("Neon 镜像 key  =", cur.fetchone()[0])
        finally:
            conn.close()
    except Exception as exc:
        print("Neon 镜像表     = FAIL", str(exc)[:120])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--promote-plan", action="store_true")
    ap.add_argument("--rebuild-plan", action="store_true")
    a = ap.parse_args()
    if a.promote_plan:
        print(PROMOTE_PLAN)
    elif a.rebuild_plan:
        print(REBUILD_PLAN)
    else:
        status()
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
