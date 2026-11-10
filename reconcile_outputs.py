#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""展示输出（净值 / 预览）场次对账：计算结果落后时自动重算。

2026-11-11 复盘：quotes 有独立的状态驱动对账，但「净值 / 预览」没有。
后果是 ranking 一旦因收盘 bar 未结算而失败（线上实测 19 只标的报
`Closing bar not settled` → `Incomplete current universe context`），
整条输出链就此停住，页面「净值截至」连续 4 天停在 11-06，而所有
quotes 对账都显示「数据已齐」。唯一能救它的只有下一次 push 或人工派发。

本模块只读浏览器同源的 Turso 公开端点，比较：

    quotes.session（必须是收盘版 intraday=False）
      vs preview.session / b/preview.session

落后就派发 `live-strategy.yml` 重算；由 reconcile.yml 的每 20 分钟心跳、
workflow_run、错峰 cron 任一对账运行唤起。数据源 settle 后下一拍即自动
补回，不需要人工干预，也不会为「让 CI 全绿」绕过任何数据质量门禁。

限流（纯函数，可离线测试）
  · 冷却 30 分钟（心跳 20 分钟一拍，避免每拍都撞同一次未结算的数据源）
  · 同一 UTC 日最多 6 次 workflow_dispatch 重算，防止无限自激
  · 在途运行一律阻断

用法
  python reconcile_outputs.py --gate        # CI：写 $GITHUB_OUTPUT
  python reconcile_outputs.py --self-test   # 离线演练，不联网
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys

import reconcile_sessions as rs

UTC = dt.timezone.utc
MAX_RECOMPUTE_PER_DAY = 6
RECOMPUTE_COOLDOWN_MIN = 30
OUTPUT_KEYS = ("quotes", "preview", "b/preview", "equity", "b/equity")


def read_outputs(cloud_url=rs.SITE_CLOUD, timeout=20):
    """一次 pipeline 查询取回展示输出；返回 {key: payload dict}。"""
    cfg = rs._urlopen_json(
        cloud_url + ("&" if "?" in cloud_url else "?") +
        "nocache=" + str(int(dt.datetime.now(UTC).timestamp())), timeout=timeout)
    endpoints = cfg.get("endpoints") or []
    ep = next((e for e in endpoints
               if e.get("type") == "turso" and e.get("url") and e.get("key")), None)
    if not ep:
        raise RuntimeError("公开配置缺少 Turso 备库端点")
    # OUTPUT_KEYS 是模块内硬编码常量，直接内联可避开 libsql args 的类型差异。
    literals = ",".join("'" + k.replace("'", "''") + "'" for k in OUTPUT_KEYS)
    body = {"baton": None, "requests": [
        {"type": "execute", "stmt": {
            "sql": "select key,payload from site_dataset where key in (%s)"
                   % literals, "args": []}},
        {"type": "close"}]}
    raw = rs._urlopen_json(
        ep["url"] + "/v2/pipeline", data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": "Bearer " + ep["key"],
                 "Content-Type": "application/json"}, timeout=timeout)
    item = (raw.get("results") or [])[0]
    if not item or item.get("type") != "ok":
        raise RuntimeError("备库展示输出查询返回失败")
    rows = ((item.get("response") or {}).get("result") or {}).get("rows") or []
    out = {}
    for key, payload in rows:
        k = key.get("value") if isinstance(key, dict) else key
        v = payload.get("value") if isinstance(payload, dict) else payload
        try:
            out[str(k)] = json.loads(v) if isinstance(v, str) else (v or {})
        except (ValueError, TypeError):
            out[str(k)] = {}
    return out


def evaluate(quotes, preview, now=None):
    """纯函数：比较收盘行情场次与预览场次，判断要不要重算。"""
    q = quotes if isinstance(quotes, dict) else {}
    p = preview if isinstance(preview, dict) else {}
    target = str(q.get("session") or "")
    pv = str(p.get("session") or "")

    def ok(reason):
        return {"need_recompute": False, "target_session": target,
                "preview_session": pv, "reason": reason}

    if not target:
        return ok("行情场次未知，不做输出对账")
    if bool(q.get("intraday")):
        return ok("行情仍是盘中版，净值/预览只在收盘场次计算")
    if pv < target:
        return {"need_recompute": True, "target_session": target,
                "preview_session": pv or "无",
                "reason": "预览场次 %s 落后于收盘场次 %s，需重算净值/排名"
                          % (pv or "无", target)}
    return ok("预览场次与收盘场次一致（%s）" % pv)


def dispatch_allowed(runs, now, cooldown_min=RECOMPUTE_COOLDOWN_MIN,
                     cap=MAX_RECOMPUTE_PER_DAY):
    """在途 / 冷却 / 当日次数上限；runs 为 gh run list 的 JSON 列表。"""
    now = rs._parse(now)
    if runs is None:
        return True, "无运行记录，允许重算"
    day_start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    active = 0
    for r in runs or []:
        if str(r.get("status") or "").lower() in (
                "in_progress", "queued", "pending", "waiting"):
            return False, "净值重算仍在途"
        created = rs._parse(r.get("createdAt"))
        if created is None:
            continue
        age_min = (now - created).total_seconds() / 60
        if 0 <= age_min < cooldown_min:
            return False, "重算冷却期内（%.0f 分钟 < %d）" % (age_min, cooldown_min)
        if created.astimezone(UTC) >= day_start:
            active += 1
    if active >= cap:
        return False, "当日已重算 %d 次，达上限 %d（等数据源结算）" % (active, cap)
    return True, "允许重算（当日已 %d 次）" % active


def _recent_runs():
    """只统计本机制派发的 workflow_dispatch 重算运行。"""
    try:
        out = subprocess.run(
            ["gh", "run", "list", "--workflow", "live-strategy.yml", "--event",
             "workflow_dispatch", "--limit", "30", "--json", "status,createdAt"],
            check=False, capture_output=True, text=True, timeout=30).stdout.strip()
        return json.loads(out or "[]")
    except Exception:                        # noqa: BLE001
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gate", action="store_true", help="写 $GITHUB_OUTPUT")
    ap.add_argument("--quotes-json", help="离线演练：quotes JSON")
    ap.add_argument("--preview-json", help="离线演练：preview JSON")
    ap.add_argument("--no-history", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()

    now = dt.datetime.now(UTC)
    result = {"need_recompute": False, "target_session": "",
              "preview_session": "", "reason": "", "source": "turso-http",
              "dispatch_allowed": False, "dispatch_reason": ""}
    try:
        if args.quotes_json and args.preview_json:
            from pathlib import Path
            q = json.loads(Path(args.quotes_json).read_text(encoding="utf-8"))
            p = json.loads(Path(args.preview_json).read_text(encoding="utf-8"))
            result["source"] = "fixture"
        else:
            data = read_outputs()
            q, p = data.get("quotes") or {}, data.get("preview") or {}
        result.update(evaluate(q, p, now))
    except Exception as exc:                 # noqa: BLE001
        # 和场次对账一致：监控链路抖动绝不变成红色运行或假告警。
        result["reason"] = "输出对账不可用，fail-open 不派发：%s" % str(exc)[:160]
        result["source"] = "unavailable"
        result["warning"] = str(exc)[:200]

    if result["need_recompute"]:
        runs = None if args.no_history else _recent_runs()
        allowed, why = dispatch_allowed(runs, now)
        result["dispatch_allowed"] = allowed
        result["dispatch_reason"] = why
        if not allowed:
            result["reason"] += "；" + why
    else:
        result["dispatch_reason"] = "输出已齐或数据不可用"

    result["evaluated_at"] = now.replace(microsecond=0).isoformat()
    print(json.dumps(result, ensure_ascii=False))
    if args.gate:
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a", encoding="utf-8") as fh:
                for key in ("need_recompute", "dispatch_allowed", "target_session",
                            "preview_session", "reason", "source"):
                    fh.write("%s=%s\n" % (key, str(result.get(key, "")).replace("\n", " ")))
    return 0


def self_test():
    now = dt.datetime.fromisoformat("2026-11-10T22:00:00+00:00")
    cases = [
        ("收盘版但预览落后", {"session": "2026-11-10", "intraday": False},
         {"session": "2026-11-06"}, True),
        ("预览与收盘一致", {"session": "2026-11-10", "intraday": False},
         {"session": "2026-11-10"}, False),
        ("盘中版不重算", {"session": "2026-11-10", "intraday": True},
         {"session": "2026-11-06"}, False),
        ("预览缺场次", {"session": "2026-11-10", "intraday": False}, {}, True),
        ("行情缺场次不误判", {}, {"session": "2026-11-10"}, False),
    ]
    failed = []
    for label, q, p, expect in cases:
        got = evaluate(q, p, now)
        if got["need_recompute"] != expect:
            failed.append("%s expect=%s got=%s" % (label, expect, got))
    if dispatch_allowed([{"status": "in_progress"}], now)[0]:
        failed.append("在途运行不应派发")
    if dispatch_allowed([{"status": "completed",
                          "createdAt": "2026-11-10T21:50:00Z"}], now)[0]:
        failed.append("冷却期内不应派发")
    many = [{"status": "completed", "createdAt": "2026-11-10T%02d:00:00Z" % h}
            for h in (1, 3, 5, 7, 9, 11, 13)]
    if dispatch_allowed(many, now)[0]:
        failed.append("超过当日上限仍派发")
    if not dispatch_allowed([], now)[0]:
        failed.append("干净历史应允许派发")
    print(json.dumps({"self_test": "FAIL" if failed else "OK",
                      "cases": len(cases) + 4, "failed": failed},
                     ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
