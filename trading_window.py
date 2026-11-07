#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交易时段门禁（隔离层）：只回答「这一轮要不要采集 / 同步」。

为什么需要它
  GitHub cron 无法表达「美国节假日」。quotes.yml / db-sync.yml 原有
  `1-5` 只排除了周末，感恩节、独立日、Good Friday 照样每 30 分钟触发，
  照样连主库、照样唤醒 Neon。本模块在 workflow 最前面秒退这些空转。

设计原则（勿回退）
  1. **只读 + 纯标准库**：可在 `pip install` 之前用系统 `python3` 执行，
     不 import psycopg2 / db_sources / live_quotes，零 egress、零数据库写。
  2. **判定依据唯一**：`research/exchange_calendar.json`，即已人工复核并
     写进 `sys_event` 的 NYSE 官方交易日历（全日休市 + 13:00 提前收盘）。
     **绝不用「美国联邦法定假日」代替**：两者系统性不同——
       Good Friday        NYSE 休市、联邦不放假
       Columbus Day       NYSE 开市、联邦放假
       Veterans Day       NYSE 开市、联邦放假
       半日交易           联邦日历里根本不存在
     免费公共假日接口只用于年度漂移核对（见 research/check_public_holidays.py），
     不进生产判定。
  3. **与策略解耦**：不修改 live_quotes.py / live_strategy.py / market_calendar.py
     的任何计算口径，不写任何金融数据，只输出 allow / reason。
  4. **fail-open**：日历缺失、损坏、超出覆盖范围、环境变量异常，一律照常
     采集。宁可多采一次，绝不因日历问题造成行情缺口。
  5. **手动 / push / workflow_dispatch 不受约束**：门禁只对 schedule 生效，
     保证改代码后仍可随时验证；`TRADE_FORCE_COLLECT=1` 可人工强制放行。

任务窗口（全部按美东时间，DST 由 zoneinfo 自动处理）
  intraday  盘中 30 分钟采集 / Turso 同步   开盘 ~ 收盘后 20 分钟
  closing   收盘关键一次 / Neon 唤醒        收盘后 0~120 分钟
  catchup   次日凌晨补跑（容错）            收盘后 0~8 小时
  publish   盘前 / 盘后发布                 开盘前 4 小时 ~ 收盘后 4 小时
  heartbeat 周末低频一致性心跳              始终允许

用法
  python3 trading_window.py --task intraday --gate      # CI：写 $GITHUB_OUTPUT
  python3 trading_window.py --task closing --at 2026-11-26T20:00:00+00:00
  python3 trading_window.py --self-test
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = dt.timezone.utc
BASE = Path(__file__).resolve().parent
CALENDAR_PATH = BASE / "research" / "exchange_calendar.json"

# (开盘前允许的分钟数, 收盘后允许的分钟数)
WINDOWS = {
    "intraday": (0, 20),
    "closing": (0, 120),
    "catchup": (0, 8 * 60),
    "publish": (4 * 60, 4 * 60),
}
TASKS = tuple(WINDOWS) + ("heartbeat",)


class CalendarUnavailable(Exception):
    """日历不可读 / 覆盖不足 —— 由 gate() 统一转成 fail-open。"""


def fingerprint(calendar: dict) -> str:
    return hashlib.sha256(
        json.dumps(calendar, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def load_calendar(path: Path | None = None) -> dict:
    cal = json.loads((path or CALENDAR_PATH).read_text(encoding="utf-8"))
    if cal.get("market") != "XNYS" or cal.get("timezone") != "America/New_York":
        raise CalendarUnavailable("日历不是已复核的 XNYS 口径")
    dt.date.fromisoformat(cal["coverage_from"])
    dt.date.fromisoformat(cal["coverage_to"])
    dt.time.fromisoformat(cal["regular_open"])
    dt.time.fromisoformat(cal["regular_close"])
    closed = cal.get("closed")
    early = cal.get("early_close")
    if not isinstance(closed, list) or not isinstance(early, dict):
        raise CalendarUnavailable("日历缺少休市 / 提前收盘清单")
    if len(set(closed)) != len(closed) or set(closed) & set(early):
        raise CalendarUnavailable("休市与提前收盘日期冲突")
    return cal


def session_info(now: dt.datetime, cal: dict) -> dict:
    """返回美东当天的场次边界；非交易日返回 trading_day=False。"""
    if now.tzinfo is None:
        raise CalendarUnavailable("时刻缺少时区")
    day = now.astimezone(ET).date()
    start = dt.date.fromisoformat(cal["coverage_from"])
    end = dt.date.fromisoformat(cal["coverage_to"])
    if not start <= day <= end:
        raise CalendarUnavailable("日历覆盖不足: " + day.isoformat())
    iso = day.isoformat()
    trading = day.weekday() <= 4 and iso not in cal["closed"]
    info = {"session": iso, "trading_day": trading,
            "early_close": iso in cal["early_close"], "open": None, "close": None}
    if not trading:
        return info
    opened = dt.datetime.combine(day, dt.time.fromisoformat(cal["regular_open"]), ET).astimezone(UTC)
    close_at = dt.time.fromisoformat(cal["early_close"].get(iso, cal["regular_close"]))
    closed = dt.datetime.combine(day, close_at, ET).astimezone(UTC)
    if opened >= closed:
        raise CalendarUnavailable("场次开收盘时间非法")
    info["open"] = opened
    info["close"] = closed
    return info


def gate(task: str, now: dt.datetime | None = None, calendar: dict | None = None,
         event_name: str = "schedule") -> dict:
    """返回 {allow, reason, task, session, phase, calendar_sha256}。"""
    now = now or dt.datetime.now(UTC)
    base = {"task": task, "session": "", "phase": "unknown",
            "calendar_sha256": "", "allow": True}

    def allow(phase, reason):
        return dict(base, allow=True, phase=phase, reason=reason)

    def skip(phase, reason):
        return dict(base, allow=False, phase=phase, reason=reason)

    if event_name != "schedule":
        return allow("manual", "非定时触发（%s），门禁放行以便随时验证" % event_name)
    if os.environ.get("TRADE_FORCE_COLLECT") == "1":
        return allow("forced", "TRADE_FORCE_COLLECT=1 人工强制放行")
    if task == "heartbeat":
        return allow("heartbeat", "周末低频一致性心跳，按额度规划保留")
    if task not in WINDOWS:
        return allow("unknown_task", "未知任务类型，fail-open 放行")

    try:
        cal = calendar or load_calendar()
        base["calendar_sha256"] = fingerprint(cal)
        info = session_info(now, cal)
    except Exception as exc:                       # 任何日历问题都不能造成停采
        return allow("calendar_unavailable",
                     "日历不可用，fail-open 照常采集：%s" % str(exc)[:120])

    base["session"] = info["session"]
    if not info["trading_day"]:
        kind = "周末" if dt.date.fromisoformat(info["session"]).weekday() > 4 else "NYSE 休市日"
        return skip("closed_day", "%s %s，不采集、不同步、不唤醒冷备" % (info["session"], kind))

    before, after = WINDOWS[task]
    opened, closed = info["open"], info["close"]
    delta = (now - opened).total_seconds() / 60
    till_close = (now - closed).total_seconds() / 60
    if delta < -before:
        return skip("before_window", "距开盘还有 %.0f 分钟，未进入 %s 采集窗口"
                    % (-delta, task))
    if till_close > after:
        return skip("after_window", "已收盘 %.0f 分钟，超出 %s 采集窗口"
                    % (till_close, task))
    phase = "early_close" if info["early_close"] else "regular"
    return allow(phase, "%s 交易日 %s，%s 任务在采集窗口内（收盘 %s UTC）"
                 % (info["session"], "半日" if info["early_close"] else "常规",
                    task, closed.strftime("%H:%M")))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--task", default="intraday", choices=TASKS)
    p.add_argument("--at", help="ISO 8601 时刻，便于离线演练")
    p.add_argument("--gate", action="store_true", help="把结果写入 $GITHUB_OUTPUT")
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()

    now = dt.datetime.fromisoformat(args.at) if args.at else dt.datetime.now(UTC)
    result = gate(args.task, now, event_name=os.environ.get("EVENT_NAME", "schedule"))
    if args.gate:
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a", encoding="utf-8") as fh:
                fh.write("allow=%s\n" % ("true" if result["allow"] else "false"))
                fh.write("reason=%s\n" % result["reason"])
                fh.write("session=%s\n" % result["session"])
    print(json.dumps(result, ensure_ascii=False))

    if args.self_test:
        cal = load_calendar()
        cases = [
            ("intraday", "2026-11-09T15:00:00+00:00", True, "常规盘中"),
            ("intraday", "2026-11-26T16:00:00+00:00", False, "感恩节休市"),
            ("intraday", "2026-04-03T15:00:00+00:00", False, "Good Friday 休市"),
            ("intraday", "2026-10-12T15:00:00+00:00", True, "Columbus Day 照常开市"),
            ("intraday", "2026-11-11T15:00:00+00:00", True, "Veterans Day 照常开市"),
            ("intraday", "2026-11-09T02:00:00+00:00", False, "北京 10:00 非交易时段"),
            ("intraday", "2026-11-27T18:30:00+00:00", False, "半日 13:00 ET 已收盘"),
            ("closing", "2026-11-27T18:30:00+00:00", True, "半日收盘关键任务"),
            ("heartbeat", "2026-11-08T05:00:00+00:00", True, "周末心跳"),
            ("intraday", "2029-01-02T15:00:00+00:00", True, "超出覆盖范围 fail-open"),
        ]
        failed = []
        for task, stamp, expected, label in cases:
            got = gate(task, dt.datetime.fromisoformat(stamp), cal)["allow"]
            if got != expected:
                failed.append("%s expect=%s got=%s" % (label, expected, got))
        print(json.dumps({"self_test": "FAIL" if failed else "OK", "cases": len(cases),
                          "failed": failed}, ensure_ascii=False))
        return 1 if failed else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
