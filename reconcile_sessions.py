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
# 开盘后多久才认为「当日盘中快照本应存在」，避免把正常排队延迟误判成故障。
INTRADAY_GRACE_MIN = 35
# 同一场次最多补采派发次数（防止上游持续失败时无限自激）。
MAX_DISPATCH_PER_SESSION = 3
# 收盘版是每交易日「必须产出」的一次，与盘中临时补采分开计数。
# 盘中一次快照只影响展示，收盘版缺失会让净值/预览整场不重算。
MAX_CLOSING_DISPATCH = 6
# 心跳 20 分钟一拍，收盘重试的冷却必须短于一拍，否则下一拍总被挡。
CLOSING_COOLDOWN_MIN = 10
DEFAULT_COOLDOWN_MIN = 30
# 收盘后多久开始要求「收盘版」快照（盘中版 intraday=True 不算齐）。
CLOSE_GRACE_MIN = 20


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
    """纯函数：给定「现在 / 日历 / 线上 quotes」，返回要不要补采。

    返回 {need_action, mode, as_of, expected_session, current_session, reason}。
    mode: intraday（抓实时价，不带 as_of）| closing（按收盘逻辑时间补采）| none
    """
    now = _parse(now)
    if now is None:
        raise ValueError("时刻无效")
    q = quotes if isinstance(quotes, dict) else {}
    current = str(q.get("session") or "")
    have_ts = _parse(q.get("ts") or q.get("ts_local")) is not None
    intraday_snapshot = bool(q.get("intraday"))

    def ok(reason, expected=""):
        return {"need_action": False, "mode": "none", "as_of": "",
                "expected_session": expected, "current_session": current,
                "reason": reason}

    def need(mode, as_of, expected, reason):
        return {"need_action": True, "mode": mode, "as_of": as_of,
                "expected_session": expected, "current_session": current,
                "reason": reason}

    info = tw.session_info(now, calendar)
    if info["trading_day"] and info["open"] and info["close"]:
        opened, closed = info["open"], info["close"]
        session = info["session"]
        # A. 当日盘中（含收盘后 20 分钟宽限）：只要求当日场次存在。
        #    策略是年度持仓 + 日度净值，盘中一次有效快照即可，不强求 30 分钟级。
        if opened + dt.timedelta(minutes=INTRADAY_GRACE_MIN) <= now \
                <= closed + dt.timedelta(minutes=CLOSE_GRACE_MIN):
            if current != session or not have_ts:
                return need("intraday", "", session,
                            "盘中应有 %s 快照，线上为 %s，实时补采" % (session, current or "无"))
            return ok("盘中 %s 快照已在线" % session, session)
        # B. 当日收盘后：必须有该场次的收盘版（intraday=False）。
        #    GitHub 把收盘 cron 延迟数小时也走这里，用逻辑时间补回收盘价。
        if now > closed + dt.timedelta(minutes=CLOSE_GRACE_MIN):
            as_of = (closed + dt.timedelta(minutes=tw.CLOSING_AS_OF_MIN)).isoformat()
            age_h = (now - _parse(as_of)).total_seconds() / 3600
            if age_h < 0 or age_h > max_backfill_hours:
                return ok("超出 %d 小时补采窗口，在线挂牌已被改写，等下一场次"
                          % max_backfill_hours, session)
            stale = current < session or (current == session and intraday_snapshot)
            if stale:
                return need("closing", as_of, session,
                            "收盘应有 %s 收盘版，线上为 %s%s，按逻辑时间补采"
                            % (session, current or "无",
                               "（盘中版）" if intraday_snapshot else ""))
            return ok("收盘 %s 快照已在线" % session, session)

    # C. 盘前 / 周末 / 休市日：检查「最近一个已收盘交易日」有没有漏。
    #    这样即使整个交易日的 cron 全被丢弃，次日凌晨/周末到达的对账器也能补齐。
    latest = tw.latest_closed_session(now, calendar)
    if latest:
        as_of = (latest["close"] + dt.timedelta(minutes=tw.CLOSING_AS_OF_MIN)).isoformat()
        age_h = (now - _parse(as_of)).total_seconds() / 3600
        if 0 <= age_h <= max_backfill_hours:
            expected = latest["session"]
            stale = current < expected or (current == expected and intraday_snapshot)
            if stale:
                return need("closing", as_of, expected,
                            "最近收盘场次 %s 缺失（线上 %s），按逻辑时间补采"
                            % (expected, current or "无"))
    return ok("无需补采（盘前 / 休市 / 数据已齐）", latest["session"] if latest else "")


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


def run_kind(run):
    """从运行标题识别补采类型；加 run-name 之前的老运行算 legacy。

    quotes.yml 的 run-name 会带 backfill-closing / backfill-intraday。
    legacy 归入盘中桶（保守），绝不占用收盘版的独立配额。
    """
    title = str((run or {}).get("displayTitle") or "")
    if "backfill-closing" in title:
        return "closing"
    if "backfill-intraday" in title:
        return "intraday"
    return "legacy"


def dispatch_allowed(expected_session, runs, now, mode="intraday"):
    """冷却 / 在途 / 每场次派发上限；runs 为 gh run list 的 JSON 列表。

    2026-11-11：盘中补采与收盘补采**分桶计数**。收盘版是每日必须产出的
    一次（缺了净值/预览整场不重算），不能因为盘中已补 5 次就被挡住。
    收盘桶冷却 10 分钟 < 心跳一拍 20 分钟，且上限单独放宽到 6 次。
    """
    now = _parse(now)
    if runs is None:
        return True, "无运行记录，允许派发"
    closing = mode == "closing"
    cooldown_min = CLOSING_COOLDOWN_MIN if closing else DEFAULT_COOLDOWN_MIN
    cap = MAX_CLOSING_DISPATCH if closing else MAX_DISPATCH_PER_SESSION
    # 在途检查对所有类型生效，避免并发派发；其余判定只看本桶运行。
    for r in runs or []:
        if str(r.get("status") or "").lower() in ("in_progress", "queued", "pending", "waiting"):
            return False, "行情快照补采仍在途"
    active = 0
    for r in runs or []:
        kind = run_kind(r)
        if closing and kind != "closing":
            continue
        if not closing and kind == "closing":
            continue
        created = _parse(r.get("createdAt"))
        if created is None:
            continue
        age_min = (now - created).total_seconds() / 60
        if 0 <= age_min < cooldown_min:
            return False, "%s 冷却期内（%.0f 分钟 < %d）" % (
                "收盘" if closing else "盘中", age_min, cooldown_min)
        # 只统计同一目标场次窗口内的补采（场次当日 00:00 UTC 起 24 小时）
        if expected_session:
            try:
                day = dt.date.fromisoformat(expected_session)
            except ValueError:
                continue
            start = dt.datetime.combine(day, dt.time(0), UTC)
            if start - dt.timedelta(hours=6) <= created <= start + dt.timedelta(hours=30):
                active += 1
    if active >= cap:
        return False, "场次 %s 的 %s 补采已派发 %d 次，达上限 %d" % (
            expected_session, "收盘" if closing else "盘中", active, cap)
    return True, "允许派发（本场次 %s 已补 %d 次）" % (
        "收盘" if closing else "盘中", active)


def _recent_runs():
    """查最近 20 次手动补采运行；gh 不可用时返回 None（视为允许）。"""
    import subprocess
    try:
        out = subprocess.run(
            ["gh", "run", "list", "--workflow", "quotes.yml", "--event",
             "workflow_dispatch", "--limit", "30", "--json",
             "status,createdAt,displayTitle"],
            check=False, capture_output=True, text=True, timeout=30).stdout.strip()
        return json.loads(out or "[]")
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
        allowed, why = dispatch_allowed(result["expected_session"], runs, now,
                                        result["mode"])
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
        # (label, now, quotes, need_action, mode)
        ("盘中当日快照缺失", "2026-11-10T15:30:00+00:00",
         {"session": "2026-11-09", "ts": "2026-11-09T21:17:00Z", "intraday": False}, True, "intraday"),
        ("盘中当日快照已在线", "2026-11-10T15:30:00+00:00",
         {"session": "2026-11-10", "ts": "2026-11-10T14:33:00Z", "intraday": True}, False, "none"),
        ("开盘宽限内不误判", "2026-11-10T14:40:00+00:00",
         {"session": "2026-11-09", "ts": "2026-11-09T21:17:00Z", "intraday": False}, False, "none"),
        ("收盘后仍只有盘中版", "2026-11-10T22:00:00+00:00",
         {"session": "2026-11-10", "ts": "2026-11-10T20:00:00Z", "intraday": True}, True, "closing"),
        ("收盘版已在线", "2026-11-10T22:00:00+00:00",
         {"session": "2026-11-10", "ts": "2026-11-10T21:17:00Z", "intraday": False}, False, "none"),
        ("cron 迟到 6 小时仍补采", "2026-11-10T03:49:00+00:00",
         {"session": "2026-11-06", "ts": "2026-11-06T21:17:00Z", "intraday": False}, True, "closing"),
        ("周末 20 小时内可补周五缺口", "2026-11-14T12:00:00+00:00",
         {"session": "2026-11-06", "ts": "2026-11-06T21:17:00Z", "intraday": False}, True, "closing"),
        ("周末超出补采窗口不补", "2026-11-16T12:00:00+00:00",
         {"session": "2026-11-06", "ts": "2026-11-06T21:17:00Z", "intraday": False}, False, "none"),
        ("感恩节休市不误判", "2026-11-26T15:00:00+00:00",
         {"session": "2026-11-25", "ts": "2026-11-25T21:17:00Z", "intraday": False}, False, "none"),
    ]
    failed = []
    for label, stamp, quotes, expected_need, expected_mode in cases:
        got = evaluate(dt.datetime.fromisoformat(stamp), cal, quotes)
        if got["need_action"] != expected_need or got["mode"] != expected_mode:
            failed.append("%s expect=(%s,%s) got=(%s,%s) %s"
                          % (label, expected_need, expected_mode,
                             got["need_action"], got["mode"], got["reason"]))
    # as_of 必须落在目标场次收盘后 17 分钟
    got = evaluate(dt.datetime.fromisoformat("2026-11-10T03:49:00+00:00"), cal,
                   {"session": "2026-11-06", "ts": "2026-11-06T21:17:00Z"})
    if got.get("as_of") != "2026-11-09T21:17:00+00:00":
        failed.append("延迟补采 as_of 错误: %r" % got.get("as_of"))
    # 冷却 / 上限
    now = dt.datetime.fromisoformat("2026-11-10T15:00:00+00:00")
    if dispatch_allowed("2026-11-10", [{"status": "completed",
                                        "createdAt": "2026-11-10T14:50:00Z"}], now)[0]:
        failed.append("冷却期内不应派发")
    if dispatch_allowed("2026-11-10", [{"status": "in_progress"}], now)[0]:
        failed.append("在途运行不应重复派发")
    many = [{"status": "completed", "createdAt": "2026-11-10T%02d:00:00Z" % h}
            for h in (1, 3, 5, 7)]
    if dispatch_allowed("2026-11-10", many, now)[0]:
        failed.append("超过每场次上限仍派发")
    # 11-10 真实事故：盘中补采 5 次把配额吃光，收盘补采被永久拒发。
    intraday_many = [
        {"status": "completed", "createdAt": "2026-11-10T%02d:00:00Z" % h,
         "displayTitle": "行情快照 · backfill-intraday"} for h in (1, 3, 5, 7, 9)]
    if not dispatch_allowed("2026-11-10", intraday_many, now, "closing")[0]:
        failed.append("盘中补采不应吃掉收盘版配额")
    closing_many = [
        {"status": "completed", "createdAt": "2026-11-10T%02d:00:00Z" % h,
         "displayTitle": "行情快照 · backfill-closing"}
        for h in (1, 2, 3, 4, 5, 6, 7)]
    if dispatch_allowed("2026-11-10", closing_many, now, "closing")[0]:
        failed.append("收盘补采超过独立上限仍派发")
    if dispatch_allowed("2026-11-10", [
            {"status": "completed", "createdAt": "2026-11-10T14:55:00Z",
             "displayTitle": "行情快照 · backfill-closing"}], now, "closing")[0]:
        failed.append("收盘 5 分钟内不应重复派发")
    if not dispatch_allowed("2026-11-10", [
            {"status": "completed", "createdAt": "2026-11-10T14:48:00Z",
             "displayTitle": "行情快照 · backfill-closing"}], now, "closing")[0]:
        failed.append("收盘 12 分钟后应允许下一拍重试")
    if dispatch_allowed("2026-11-10", [{"status": "in_progress",
                                        "displayTitle": "行情快照 · backfill-intraday"}],
                        now, "closing")[0]:
        failed.append("在途运行对所有类型都应阻断")
    print(json.dumps({"self_test": "FAIL" if failed else "OK",
                      "cases": len(cases) + 9, "failed": failed}, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
