"""selfheal.py — 看门狗的自愈执行器（只允许白名单内的幂等动作）。

2026-11-07 审查结论：原看门狗「只告警不修复」，失败只能等下一次 schedule
或人工 dispatch。本模块把**可安全自动恢复**的三类问题接到对应的幂等工作流，
其余问题仍然只告警——绝不对「数据口径错配」「凭据失效」这类需要人工判断
的问题做自动动作。

安全边界
  · 动作白名单固定，且全部是幂等、无破坏性、不会删除数据的发布/重算工作流；
  · 每个动作有冷却与在途检查：同工作流正在运行或刚跑过则不重复派发，
    避免告警 → 重跑 → 再告警的自激循环；
  · 单轮最多派发 2 个动作，且只在 problems.txt 有内容时执行；
  · 任何派发失败只记录、不抛错，不影响看门狗本身的告警。

用法（CI）
  python selfheal.py --problems problems.txt
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

# 问题正则 → (工作流文件, 动作说明)。顺序即优先级。
RULES = (
    (("镜像停更", "镜像读取失败", "镜像缺失数据集", "镜像格式无效"),
     "ui-publish.yml", "重新从在线库导出同源镜像并原子发布"),
    (("收益基准落后", "次年预览未跟随", "净值停更", "预览停更"),
     "live-strategy.yml", "重算当期净值与次年预览"),
    (("行情停更", "行情缺少有效时间戳", "行情快照缺少 SPY", "在线报价监控回执缺失"),
     "quotes.yml", "重新采集行情快照"),
)

COOLDOWN_MIN = 30
MAX_ACTIONS = 2
# 在线报价只保留最近一个场次；超过这个时数再去补采只会反复撞上已被
# 改写的字段（盘前 SPY 前收价 = 现价），不如等下一场次自动采集。
MAX_BACKFILL_HOURS = 20


def gh(*args):
    return subprocess.run(["gh", *args], check=False, capture_output=True, text=True).stdout.strip()


def recently_active(workflow: str) -> bool:
    """同工作流在途、或冷却期内刚跑过 → 不再派发。

    只统计 workflow_dispatch：quotes.yml 盘中每 30 分钟有一次 schedule，
    若把它们算进冷却，看门狗（每小时 :05）几乎永远命中「刚跑过」，
    自愈会被自己的正常调度永久饿死。
    """
    try:
        rows = json.loads(gh("run", "list", "--workflow", workflow,
                             "--event", "workflow_dispatch", "--limit", "5",
                             "--json", "status,createdAt") or "[]")
    except (ValueError, TypeError):
        return False
    now = dt.datetime.now(dt.timezone.utc)
    for r in rows:
        if r.get("status") in ("in_progress", "queued", "pending", "waiting"):
            return True
        try:
            created = dt.datetime.fromisoformat(str(r.get("createdAt", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=dt.timezone.utc)
        if (now - created).total_seconds() < COOLDOWN_MIN * 60:
            return True
    return False


def dispatch_args(workflow: str, now: dt.datetime):
    """派发参数；返回 None 代表「这个缺口补不回来，不要派发无效补跑」。

    行情补跑必须带 critical（freeze 档放行）+ as_of（逻辑时间）。as_of
    取「最近一个已收盘交易日的收盘后 17 分钟」，由纯标准库的
    trading_window 从已复核 NYSE 日历算出，绝不猜测日期。
    """
    if workflow != "quotes.yml":
        return []
    try:
        import trading_window as tw
        as_of = tw.closing_as_of(now)
        if not as_of:
            return None
        age_h = (now - dt.datetime.fromisoformat(as_of)).total_seconds() / 3600
        if age_h > MAX_BACKFILL_HOURS:
            return None
    except Exception:
        return None
    return ["-f", "critical=true", "-f", "as_of=" + as_of]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--problems", default="problems.txt")
    ap.add_argument("--dry-run", action="store_true", help="只输出决策，不派发")
    a = ap.parse_args()

    path = Path(a.problems)
    problems = [x.strip() for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    problems = [x for x in problems if x]
    decisions = []
    dispatched = 0
    used = set()

    for keywords, workflow, reason in RULES:
        if dispatched >= MAX_ACTIONS:
            break
        if workflow in used:
            continue
        matched = [p for p in problems if any(k in p for k in keywords)]
        if not matched:
            continue
        if recently_active(workflow):
            decisions.append({"workflow": workflow, "action": "skip",
                              "reason": "冷却期内或已有在途运行", "matched": matched})
            used.add(workflow)
            continue
        args = dispatch_args(workflow, dt.datetime.now(dt.timezone.utc))
        if args is None:
            decisions.append({"workflow": workflow, "action": "skip",
                              "reason": "目标场次已超出 %d 小时补采窗口，"
                                        "在线报价字段已被改写；等下一场次自动采集"
                                        % MAX_BACKFILL_HOURS,
                              "matched": matched})
            used.add(workflow)
            continue
        if a.dry_run:
            decisions.append({"workflow": workflow, "action": "would_dispatch",
                              "reason": reason, "matched": matched, "args": args})
        else:
            out = gh("workflow", "run", workflow, *args)
            ok = bool(out)
            decisions.append({"workflow": workflow,
                              "action": "dispatched" if ok else "dispatch_failed",
                              "reason": reason, "matched": matched, "args": args})
            if ok:
                dispatched += 1
        used.add(workflow)

    report = {
        "problems": len(problems),
        "actions_dispatched": dispatched,
        "max_actions": MAX_ACTIONS,
        "cooldown_min": COOLDOWN_MIN,
        "decisions": decisions,
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
    }
    # 明确列出「不做自动动作」的问题，便于人工跟进
    covered = set()
    for keywords, _, _ in RULES:
        for p in problems:
            if any(k in p for k in keywords):
                covered.add(p)
    report["manual_only"] = [p for p in problems if p not in covered]
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
