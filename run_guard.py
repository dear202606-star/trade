#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""计算结果管线的「场次幂等」门禁。

问题
  live-strategy.yml 由每次行情快照 workflow_run 拉起，一个计费周期内
  被触发 288 次；行情快照本身又被 GitHub schedule 与 pg_cron 双触发。
  每次净值/排名计算都要读大量已封存证据（跨网络），是本周期 Egress
  19.928 GB 中约 10 GB 的来源。而策略是年度持仓 + 日度净值，
  同一场次、同一报价版本、同一份代码，算 200 次结果完全相同。

规则（全部 fail-safe，不会掩盖真实问题）
  1. 盘中快照不重算净值/预览 —— 只认收盘场次。
  2. 收盘场次若「session + quote_version + 计算代码指纹」已有成功记录，
     跳过；三者任一变化都自动重算（改代码、换报价场次不会漏算）。
  3. 账本写在 sys_event（append-only，单条 <1KB），不新增表、不改
     live_strategy_current 的既有语义。
  4. 账本查询失败时**继续执行**（不可用不能导致管线停摆），但打印告警；
     写入失败只告警不抛错，下一轮会重算一次，最坏情况是多花一次流量。
  5. 提供 --force 环境变量 TRADE_FORCE_RECOMPUTE=1 供人工强制重算。

用法（由 live_strategy.run_equity / live_ranking.run 调用）
  quotes = live_quotes.load_snapshot()
  if run_guard.should_skip("equity", quotes): return
  ... 计算并成功 store ...
  run_guard.mark_done("equity", quotes)
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from psycopg2.extras import Json

import db_sources

# 这些文件任一变化都会改变计算结果，纳入指纹。
CODE_FILES = (
    "live_strategy.py", "live_ranking.py", "prepare_live_ranking.py",
    "live_quotes.py", "db_ref.py", "market_calendar.py",
    "db_prices.py", "db_select.py", "db_sources.py",
    "live_cash_dividends.py", "run_guard.py",
)
SOURCE = "egress_idempotency"


def code_sha() -> str:
    h = hashlib.sha256()
    base = Path(__file__).resolve().parent
    for name in sorted(CODE_FILES):
        p = base / name
        if p.exists():
            h.update(name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def identity(quotes: dict) -> dict:
    return {
        "session": str(quotes.get("session") or ""),
        "quote_version": str(quotes.get("version") or ""),
        "code_sha256": code_sha(),
    }


def should_skip(kind: str, quotes: dict) -> bool:
    """kind: 'equity' | 'preview'。返回 True 表示本轮无需重算。"""
    if os.environ.get("TRADE_FORCE_RECOMPUTE") == "1":
        print("[run-guard] force recompute", kind, flush=True)
        return False
    if quotes.get("intraday"):
        print("[run-guard] SKIP %s: 盘中快照，净值/预览只在收盘场次计算" % kind,
              flush=True)
        return True
    ident = identity(quotes)
    if not (ident["session"] and ident["quote_version"]):
        return False
    try:
        conn = db_sources.db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """select 1 from sys_event
                       where source=%s and status='pass' and subject=%s
                         and metrics->>'session'=%s
                         and metrics->>'quote_version'=%s
                         and metrics->>'code_sha256'=%s
                       limit 1""",
                    (SOURCE, kind, ident["session"], ident["quote_version"],
                     ident["code_sha256"]))
                hit = cur.fetchone() is not None
        finally:
            conn.close()
    except Exception as exc:
        print("[run-guard] WARN 账本不可读，照常计算：%s" % str(exc)[:120],
              flush=True)
        return False
    if hit:
        print("[run-guard] SKIP %s: session=%s quote_version=%s code=%s 已有结果"
              % (kind, ident["session"], ident["quote_version"][:12],
                 ident["code_sha256"][:12]), flush=True)
    return hit


def mark_done(kind: str, quotes: dict) -> None:
    ident = identity(quotes)
    metrics = dict(ident, kind=kind, note="场次幂等账本，供 egress 预算与去重使用")
    try:
        conn = db_sources.db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    # 2026-11-11：sys_event_kind_check 不接受 'compute'，
                    # 账本一直写入失败（线上 WARN：violates check constraint），
                    # 场次幂等形同虚设、每轮都重算白耗 egress。改用生产里
                    # 已验证合法的 'validation'（prepare_live_ranking.py /
                    # publish_pipeline_status.py 同样用它）。should_skip 只按
                    # source/subject/status/metrics 查询，kind 变更不影响去重。
                    """insert into sys_event(kind,source,subject,status,metrics,finished_at)
                       values('validation',%s,%s,'pass',%s,now())""",
                    (SOURCE, kind, Json(metrics)))
            conn.commit()
        finally:
            conn.close()
        print("[run-guard] marked %s session=%s version=%s"
              % (kind, ident["session"], ident["quote_version"][:12]), flush=True)
    except Exception as exc:
        print("[run-guard] WARN 账本写入失败（下轮会重算一次）：%s"
              % str(exc)[:120], flush=True)


if __name__ == "__main__":
    print(json.dumps({"code_sha256": code_sha(),
                      "sources": list(CODE_FILES)}, ensure_ascii=False, indent=2))
