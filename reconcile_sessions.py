#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reconcile_sessions.py — 场次对账器：用「数据齐不齐」决定要不要补跑。

为什么需要它（2026-11-10 线上复盘）
  GitHub Actions 免费版的 scheduled events 是尽力而为：实测 21:17 / 21:50 UTC
  的收盘任务被延迟到次日 03:35 / 03:49 UTC（约 6 小时），21:55 的备库同步
  拖到次日 03:26；11-10 整个盘中 13:00~15:13 UTC 一次 schedule 都没触发。
  而原有自愈挂在「看门狗」里，看门狗自己也是 schedule 触发 —— 被兜底者
  与兜底者同时哑火，是典型共模故障。

四条设计原则（勿回退）
  1. 状态驱动：不问「现在几点、cron 该不该跑」，只问「浏览器真正读到的那份
     quotes 里有没有当前应有的场次」。缺就补，cron 迟到数小时照样补齐。
  2. 多触发源：由 reconcile.yml 用 workflow_run（上游成功/失败都触发）+
     独立错峰 cron + 手动三重方式唤起，任一到达即对账，不再单点依赖看门狗。
  3. 幂等防自激：同一场次补采次数有上限，并复用 selfheal 的冷却 / 在途检查；
     数据已齐绝不派发，避免「补采 → 完成 → 再补采」死循环。
  4. fail-open：只读公开 Turso 只读端点；网络异常时 need_action=false 并以 0
     退出，绝不因监控链路抖动阻断发布 / 同步 / 告警主流程。

用法
  python reconcile_sessions.py --gate          # CI：写 $GITHUB_OUTPUT
  python reconcile_sessions.py --self-test     # 离线演练，不联网
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import trading_window as tw

UTC = dt.timezone.utc
SITE_CLOUD = "https://dear202606-star.github.io/trade/data/cloud.json"

# 与 selfheal.MAX_BACKFILL_HOURS 同口径：在线挂牌只保留最近一个场次，
# 超过这个时数再去补采只会撞上已被改写的前收价字段。
MAX_BACKFILL_HOURS = 20
MAX_CLOSING_DISPATCH = 6
CLOSING_COOLDOWN_MIN = 10


def _parse(value):
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        d = dt.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def evaluate(now, calendar, quotes, max_backfill_hours=MAX_BACKFILL_HOURS):
    """Check the settled session that should already be visible to readers."""
    now = _parse(now)
    if now is None:
        raise ValueError("时刻无效")
    q = quotes or {}
    current = str(q.get('session') or '')
    target = tw.closing_collection_gate(now, calendar)
    expected = target['session']
    age = now - _parse(target['as_of']) if target['allow'] else None
    stamp = _parse(q.get('ts'))
    closing = tw.latest_closed_session(now, calendar)
    covered = bool(closing and stamp and stamp <= now and stamp >= closing['close'] and
                   current == closing['session'] and q.get('intraday') is False)
    missing = bool(target['allow'] and age <= dt.timedelta(hours=max_backfill_hours) and not covered)
    return dict(need_action=missing, mode='closing' if missing else 'none',
                as_of=target['as_of'] if missing else '', expected_session=expected,
                current_session=current, reason='缺少 %s 收盘快照' % expected if missing else '无需补采')


def _urlopen_json(url, data=None, headers=None, timeout=20, attempts=3):
    """GitHub Pages / Turso 偶发 5xx 时退避重试；全部失败才抛错。"""
    import time
    last = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, data=data, headers=headers or {})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
            last = exc
            if attempt < attempts - 1:
                time.sleep(1 + attempt * 2)
    raise RuntimeError("请求失败 (%d 次): %s" % (attempts, str(last)[:160]))


def read_quotes(cloud_url=SITE_CLOUD, timeout=20):
    """只读浏览器同源的 Turso 备库，取回 quotes 一份 payload。"""
    cfg = _urlopen_json(cloud_url + ("&" if "?" in cloud_url else "?") +
                        "nocache=" + str(int(dt.datetime.now(UTC).timestamp())),
                        timeout=timeout)
    endpoints = cfg.get("endpoints") or []
    ep = next((e for e in endpoints
               if e.get("type") == "turso" and e.get("url") and e.get("key")), None)
    if not ep:
        raise RuntimeError("公开配置缺少 Turso 备库端点")
    body = {"baton": None, "requests": [
        {"type": "execute", "stmt": {
            "sql": "select payload from site_dataset where key = 'quotes'", "args": []}},
        {"type": "close"}]}
    raw = _urlopen_json(ep["url"] + "/v2/pipeline",
                        data=json.dumps(body).encode("utf-8"),
                        headers={"Authorization": "Bearer " + ep["key"],
                                 "Content-Type": "application/json"},
                        timeout=timeout)
    item = (raw.get("results") or [])[0]
    if not item or item.get("type") != "ok":
        raise RuntimeError("备库 quotes 查询返回失败")
    rows = ((item.get("response") or {}).get("result") or {}).get("rows") or []
    if not rows:
        return {}
    value = rows[0][0]["value"]
    return json.loads(value) if isinstance(value, str) else (value or {})


def dispatch_allowed(expected_session, runs, now):
    """Allow limited retries without overlapping scheduled collections."""
    now = _parse(now)
    day = dt.date.fromisoformat(expected_session)
    start = dt.datetime.combine(day, dt.time(0), UTC)
    active = 0
    for run in runs or []:
        if str(run.get('status') or '').lower() in ('in_progress', 'queued', 'pending', 'waiting'):
            return False, '行情采集仍在途'
        if 'backfill-closing' not in str(run.get('displayTitle') or ''):
            continue
        created = _parse(run.get('createdAt'))
        if created is None:
            continue
        if dt.timedelta(0) <= now - created < dt.timedelta(minutes=CLOSING_COOLDOWN_MIN):
            return False, '收盘补采冷却期内'
        if start <= created <= start + dt.timedelta(hours=44):
            active += 1
    if active >= MAX_CLOSING_DISPATCH:
        return False, '收盘补采达到场次上限'
    return True, '允许收盘补采'


def _recent_runs():
    """Check pending collectors and downstream jobs before dispatching a repair."""
    import subprocess
    try:
        runs = []
        for workflow in ("quotes.yml", "live-strategy.yml", "db-sync.yml"):
            limit = "30" if workflow == "quotes.yml" else "4"
            process = subprocess.run(
                ["gh", "run", "list", "--workflow", workflow, "--limit", limit, "--json",
                 "status,createdAt,displayTitle"],
                check=True, capture_output=True, text=True, timeout=15)
            runs.extend(json.loads(process.stdout or "[]"))
        return runs
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gate", action="store_true", help="把决策写入 $GITHUB_OUTPUT")
    ap.add_argument("--at", help="离线演练时刻 ISO 8601")
    ap.add_argument("--quotes-json", help="离线演练用的 quotes JSON 文件")
    ap.add_argument("--no-history", action="store_true", help="跳过运行历史检查")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    now = _parse(args.at) or dt.datetime.now(UTC)
    result = {"need_action": False, "mode": "none", "as_of": "",
              "expected_session": "", "current_session": "",
              "reason": "", "source": "turso-http"}
    try:
        cal = tw.load_calendar()
        quotes = (json.loads(Path(args.quotes_json).read_text(encoding="utf-8"))
                  if args.quotes_json else read_quotes())
        result.update(evaluate(now, cal, quotes))
        result["source"] = "fixture" if args.quotes_json else "turso-http"
    except Exception as exc:
        # fail-open：监控链路抖动不能阻断任何主流程，也不能制造假告警。
        result.update({"need_action": False, "mode": "none", "as_of": "",
                       "reason": "对账数据不可用，fail-open 不派发：%s" % str(exc)[:160],
                       "source": "unavailable"})
        result["warning"] = str(exc)[:200]

    if result["need_action"]:
        runs = None if args.no_history else _recent_runs()
        allowed, why = dispatch_allowed(result["expected_session"], runs, now)
        result["dispatch_allowed"] = allowed
        result["dispatch_reason"] = why
        if not allowed:
            result["reason"] += "；" + why
    else:
        result["dispatch_allowed"] = False
        result["dispatch_reason"] = "数据已齐或对账不可用"

    result["evaluated_at"] = now.replace(microsecond=0).isoformat()
    print(json.dumps(result, ensure_ascii=False))
    if args.gate:
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a", encoding="utf-8") as fh:
                for key in ("need_action", "dispatch_allowed", "mode", "as_of",
                            "expected_session", "reason", "source"):
                    fh.write("%s=%s\n" % (key, str(result.get(key, "")).replace("\n", " ")))
    return 0


def self_test():
    cal = tw.load_calendar()
    cases = [
        ('盘中', '2026-11-10T16:00:00+00:00', False),
        ('结算宽限', '2026-11-10T21:44:00+00:00', False),
        ('收盘', '2026-11-10T21:46:00+00:00', True),
        ('半日市', '2026-11-27T18:46:00+00:00', True),
        ('休市日', '2026-11-26T20:00:00+00:00', False),
    ]
    failures = [label for label, now, expected in cases
                if evaluate(now, cal, {})['need_action'] != expected]
    print(json.dumps({'cases': len(cases), 'failures': failures}, ensure_ascii=False))
    return bool(failures)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
