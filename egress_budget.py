#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Supabase 免费档出口流量（Egress）预算门禁。

背景
  Supabase Free 档每计费周期含 5 GB Egress，超额触发 Fair Use 限制，
  PostgREST 返回 402，项目只读、页面白屏。2026-10-28 周期实测
  19.928 GB（399%），根因是「单位传输量 × 工作流运行次数」双向放大：
  GitHub schedule 与 pg_cron 同一 cron 双触发、workflow_run 连锁，
  一个周期内行情快照 432 次、在线净值排名 288 次。

本脚本做什么
  按「本计费周期真实运行次数 × 每次保守成本上限」估算累计 Egress，
  分三档决定当前这次重型任务是否允许执行：
    normal   (< 70% 预算)  正常运行
    throttle (70%~100%)    只放行收盘关键任务，盘中重复任务跳过
    freeze   (>= 100%)     最小日更模式：只放行 critical 收盘/日终任务，
                           盘中重复刷新与非关键校验全部跳过
  预算耗尽不等于故障：页面始终展示最近一次完整收盘结果（配合
  run_guard 场次幂等，每天只算一次）；下个周期配额重置后自动恢复。

为什么用估算而不是账单
  Supabase 免费档不提供按来源拆分的 Egress 明细，也无法在 CI 里
  用无凭据的方式读取组织级用量。成本表按代码实际读取的表与行数量级
  取**保守上限**（宁可高估），因此门禁只可能提前降级，不会算漏。
  真实用量以控制台 Usage 页为准，用于事后校准成本表。

用法
  CI 门禁：python egress_budget.py --workflow "行情快照" --critical
  本地报告：python egress_budget.py --report
  本地模拟：python egress_budget.py --report --budget-mb 3000
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request

REPO = os.environ.get("GITHUB_REPOSITORY", "dear202606-star/trade")
API = "https://api.github.com"

# 计费周期锚点（控制台实测：2026-10-28 ~ 2026-11-28）。
CYCLE_ANCHOR_DAY = 28
# Supabase Free 档 Egress 上限 5 GB；按 60% 设安全预算，留 2 GB 给
# 控制台访问、临时排查、REST 网关恢复后的正常前端请求。
PLAN_LIMIT_MB = 5000.0
SAFE_BUDGET_MB = 3000.0
THROTTLE_RATIO = 0.70

# 治理上线时刻：之前的运行按「优化前实测成本」归因，之后按「优化后
# 稳态期望成本」预算。这样同一份账本既能解释本周期 19.9GB 的来历，
# 又不会因为旧成本把下个周期误判为已超额。
CUTOFF_UTC = "2026-11-07T15:00:00Z"

# 优化前：每次触发都真算、每次备份都全量传输。
LEGACY_COST_MB = {
    "整库备份与冻结巡检": 180.0,
    "在线净值与动态排名": 35.0,
    "网页发布": 12.0,
    "行情快照": 2.0,
    "线上 E2E 校验": 1.0,
    "看门狗（停更告警）": 2.0,
    "每日全量更新": 25.0,
    "采集与锁定（CI 主力，写库）": 20.0,
    "CI 采集（写暂存表，无需 secret）": 8.0,
    "每日补数与校验": 12.0,
    "数据质量核对": 15.0,
    "回填历史行情": 10.0,
    "证券身份生成防护回归": 1.0,
    "页面收益计算回归": 1.0,
    "工程回归": 1.0,
    "在线库维护": 5.0,
    "数据库连通与写入自检": 1.0,
}
# 优化后稳态期望成本：场次幂等让 17 次触发里只有 1 次真正搬数据；
# 整库备份改为「库内指纹 + 复用未变化表字节」，历史冻结表不再重复传输。
COST_MB = dict(LEGACY_COST_MB)
COST_MB.update({
    "在线净值与动态排名": 3.0,    # 触发 17 次/日，仅收盘 1 次约 35MB
    "整库备份与冻结巡检": 25.0,   # 增量：只传变化表，首次全量摊薄
})

DEFAULT_COST_MB_LEGACY = 3.0
DEFAULT_COST_MB = 1.0

# throttle 档只放行这些「不可跳过」的收盘/日终任务，其余一律延后。
CRITICAL_WORKFLOWS = {"整库备份与冻结巡检", "在线净值与动态排名"}


def cycle_bounds(now: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    """返回 (本周期起始, 下一周期起始)，按 UTC 计算。"""
    y, m = now.year, now.month
    if now.day >= CYCLE_ANCHOR_DAY:
        start = dt.datetime(y, m, CYCLE_ANCHOR_DAY, tzinfo=dt.timezone.utc)
        nm, ny = (m + 1, y) if m < 12 else (1, y + 1)
    else:
        pm, py = (m - 1, y) if m > 1 else (12, y - 1)
        start = dt.datetime(py, pm, CYCLE_ANCHOR_DAY, tzinfo=dt.timezone.utc)
        nm, ny = m, y
    end = dt.datetime(ny, nm, CYCLE_ANCHOR_DAY, tzinfo=dt.timezone.utc)
    return start, end


def _headers() -> dict:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    h = {"Accept": "application/vnd.github+json",
         "User-Agent": "trade-egress-budget",
         "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = "Bearer " + token
    return h


def _gh_fallback_token() -> str:
    """本地无 GH_TOKEN 时借用已登录的 gh CLI。"""
    try:
        return subprocess.run(["gh", "auth", "token"], capture_output=True,
                              text=True, timeout=15).stdout.strip()
    except Exception:
        return ""


def list_runs(start: dt.datetime, max_pages: int = 60) -> tuple[list[dict], bool]:
    """拉取本周期内工作流运行。返回 (runs, truncated)。

    truncated=True 表示分页上限内仍未走到周期起点，账本不完整；
    此时调用方必须对累计值做保守放大，绝不能因为「没数全」而放行。
    """
    if not (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")):
        t = _gh_fallback_token()
        if t:
            os.environ["GH_TOKEN"] = t
    out, page, truncated = [], 1, False
    while page <= max_pages:
        q = urllib.parse.urlencode({"per_page": 100, "page": page})
        url = "%s/repos/%s/actions/runs?%s" % (API, REPO, q)
        req = urllib.request.Request(url, headers=_headers())
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                batch = json.load(r).get("workflow_runs", [])
        except Exception as exc:
            # 读不到运行记录时绝不放行重型任务（fail-closed）。
            raise RuntimeError("无法读取工作流运行记录：%s" % str(exc)[:160])
        if not batch:
            break
        stop = False
        for run in batch:
            created = dt.datetime.fromisoformat(
                run["created_at"].replace("Z", "+00:00"))
            if created < start:
                stop = True
                continue
            out.append({"name": run.get("name") or "",
                        "created_at": run["created_at"],
                        "conclusion": run.get("conclusion")})
        if stop or len(batch) < 100:
            break
        if page == max_pages:
            truncated = True
        page += 1
    return out, truncated


def estimate(runs: list[dict], truncated: bool = False) -> dict:
    by_name: dict[str, dict] = {}
    for r in runs:
        e = by_name.setdefault(r["name"], {"count": 0, "mb": 0.0})
        e["count"] += 1
        legacy = r["created_at"] < CUTOFF_UTC
        table = LEGACY_COST_MB if legacy else COST_MB
        fallback = DEFAULT_COST_MB_LEGACY if legacy else DEFAULT_COST_MB
        e["mb"] += table.get(r["name"], fallback)
    total = sum(v["mb"] for v in by_name.values())
    # 账本没数全时按 1.5 倍保守放大：门禁宁可提前降级，不冒打穿配额的风险。
    if truncated:
        total *= 1.5
    return {"runs": len(runs), "estimated_mb": round(total, 1),
            "truncated": truncated,
            "by_workflow": sorted(
                [{"name": k, **v, "mb": round(v["mb"], 1)}
                 for k, v in by_name.items()],
                key=lambda x: -x["mb"])}


def decide(estimated_mb: float, cost: float, critical: bool,
           budget_mb: float) -> tuple[str, bool]:
    """返回 (mode, allow)。

    freeze 不是「全站停摆」：仍放行 critical 的收盘/日终任务，让页面
    数据每天前进一次；盘中重复刷新、非关键校验一律跳过。这样即便
    某个周期已经超额（此时限制已生效，省流量已无意义），也不会让
    页面数据连续三周冻结，用户仍能看到最近一次完整收盘结果。
    """
    after = estimated_mb + cost
    if after <= budget_mb * THROTTLE_RATIO:
        return "normal", True
    if after <= budget_mb:
        return "throttle", bool(critical)
    return "freeze", bool(critical)


def emit(output: dict) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            for key in ("allow", "mode", "estimated_mb", "projected_mb",
                        "budget_mb", "cycle_start", "cycle_end"):
                v = output[key]
                fh.write("%s=%s\n" % (key, str(v).lower() if isinstance(v, bool) else v))
    print(json.dumps(output, ensure_ascii=False, indent=2), flush=True)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workflow", default="", help="当前工作流 name")
    p.add_argument("--critical", action="store_true",
                   help="标记为收盘/日终关键任务，throttle 档仍放行")
    p.add_argument("--report", action="store_true", help="只打印本周期明细")
    p.add_argument("--budget-mb", type=float, default=SAFE_BUDGET_MB)
    p.add_argument("--fast", action="store_true",
                   help="CI 门禁模式：最多读 10 页，账本截断时保守放大")
    a = p.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    start, end = cycle_bounds(now)
    try:
        runs, truncated = list_runs(start, max_pages=10 if a.fast else 60)
    except Exception as exc:
        print("FATAL: %s" % exc, file=sys.stderr)
        # fail-closed：读不到账本就不放行，避免静默打穿配额。
        if os.environ.get("GITHUB_OUTPUT"):
            emit({"allow": False, "mode": "unknown", "estimated_mb": 0,
                  "projected_mb": 0, "budget_mb": a.budget_mb,
                  "cycle_start": start.date().isoformat(),
                  "cycle_end": end.date().isoformat(),
                  "error": str(exc)[:160]})
            return 0
        return 1

    est = estimate(runs, truncated)
    if a.report:
        print(json.dumps({"cycle": {"start": start.isoformat(), "end": end.isoformat()},
                          "plan_limit_mb": PLAN_LIMIT_MB,
                          "safe_budget_mb": a.budget_mb,
                          **est}, ensure_ascii=False, indent=2))
        return 0

    cost = COST_MB.get(a.workflow, DEFAULT_COST_MB)
    if a.critical or a.workflow in CRITICAL_WORKFLOWS:
        a.critical = True
    mode, allow = decide(est["estimated_mb"], cost, a.critical, a.budget_mb)
    # 显式人工覆盖：仅用于验证备份/迁移等一次性动作，输出会留痕。
    if os.environ.get("TRADE_FORCE_BUDGET") == "1":
        mode, allow = "manual-override", True
    emit({"allow": allow, "mode": mode,
          "estimated_mb": est["estimated_mb"],
          "projected_mb": round(est["estimated_mb"] + cost, 1),
          "budget_mb": a.budget_mb,
          "cycle_start": start.date().isoformat(),
          "cycle_end": end.date().isoformat(),
          "workflow": a.workflow, "run_cost_mb": cost,
          "cycle_runs": est["runs"],
          "top_costs": est["by_workflow"][:5]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
