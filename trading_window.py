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
  opening   开盘后 30 分钟关键一次          按日历开盘时刻 +25~45 分钟
            当日盘中延迟到达也放行（实时报价仍有效），收盘后交给 closing
  closing   收盘关键一次 / Neon 唤醒        收盘后 0~120 分钟
            GitHub schedule 实测可延迟数小时；12 小时内仍放行并输出
            as_of（目标交易日收盘后 17 分钟），供 live_quotes.py --as-of
            按「逻辑时间」补采当时仍挂在线的收盘报价，绝不因平台延迟丢数据
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
    "opening": (0, 0),        # 使用下方按日历计算的专门窗口，不走 before/after
    "closing": (0, 120),
    "catchup": (0, 8 * 60),
    "publish": (4 * 60, 4 * 60),
}
TASKS = tuple(WINDOWS) + ("heartbeat",)

# 开盘后 30 分钟关键任务的容差；当日盘中延迟到达仍抓实时价，不回溯。
OPENING_FROM_MIN, OPENING_TO_MIN = 25, 45
# 收盘关键任务允许的延迟补跑上限。实测 GitHub 把 21:50 UTC 的任务
# 拖到次日 03:49 UTC（收盘后 410 分钟），必须在容忍范围内。
CLOSING_LATE_LIMIT_MIN = 12 * 60
# 补采逻辑时间固定在收盘后 17 分钟，与 21:17 UTC 的收盘 cron 对齐。
CLOSING_AS_OF_MIN = 17


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


def latest_closed_session(now: dt.datetime | None = None,
                          calendar: dict | None = None) -> dict | None:
    """最近一个「已经收盘」的交易日场次信息；找不到返回 None。

    供看门狗判断「今天该有的收盘数据到了没」、供自愈计算补采逻辑时间。
    只读纯 JSON 日历，不连库、不发网络请求。
    """
    now = now or dt.datetime.now(UTC)
    cal = calendar or load_calendar()
    day = now.astimezone(ET).date()
    for _ in range(14):
        try:
            info = session_info(dt.datetime.combine(day, dt.time(12, 0), ET), cal)
        except CalendarUnavailable:
            return None
        if info["trading_day"] and info["close"] and now >= info["close"]:
            return info
        day -= dt.timedelta(days=1)
    return None


def closing_as_of(now: dt.datetime | None = None,
                  calendar: dict | None = None) -> str | None:
    """补采应使用的逻辑时间（UTC ISO），对齐收盘后 17 分钟。"""
    info = latest_closed_session(now, calendar)
    if not info:
        return None
    return (info["close"] + dt.timedelta(minutes=CLOSING_AS_OF_MIN)).isoformat()


CLOSING_SETTLE_MIN = 45


def due_close(now, calendar):
    """The latest regular or early close after its settlement grace."""
    return latest_closed_session(now - dt.timedelta(minutes=CLOSING_SETTLE_MIN), calendar)


def closing_collection_gate(now, calendar, as_of=None):
    info = session_info(now, calendar)
    latest = due_close(now, calendar)
    # Once the next session opens, the vendor can no longer supply the previous close snapshot.
    if latest and info['trading_day'] and info['session'] > latest['session'] and now >= info['open']:
        latest = None
    allow = bool(latest and now - latest['close'] <= dt.timedelta(hours=20))
    if as_of:
        try:
            requested = dt.datetime.fromisoformat(as_of.replace('Z', '+00:00'))
            allow = bool(allow and requested.tzinfo is not None and requested <= now and
                         requested.astimezone(ET).date().isoformat() == latest['session'] and
                         requested >= latest['close'])
        except ValueError:
            allow = False
    return {
        'allow': allow,
        'session': latest['session'] if allow else '',
        'as_of': (latest['close'] + dt.timedelta(minutes=CLOSING_SETTLE_MIN)).isoformat() if allow else '',
    }


def gate(task: str, now: dt.datetime | None = None, calendar: dict | None = None,
         event_name: str = "schedule", late_ok: bool = False) -> dict:
    """返回 {allow, reason, task, session, phase, calendar_sha256[, as_of]}。

    late_ok 只由 quotes.yml 的收盘关键 cron 显式打开：默认关闭时，收盘后
    到次日开盘前一律停采（保持「北京 6-20 点不采集」的原有语义）；打开后
    才允许把 GitHub 延迟数小时的收盘任务当补跑执行。
    """
    now = now or dt.datetime.now(UTC)
    base = {"task": task, "session": "", "phase": "unknown",
            "calendar_sha256": "", "allow": True}

    def allow(phase, reason):
        return dict(base, allow=True, phase=phase, reason=reason)

    def skip(phase, reason):
        return dict(base, allow=False, phase=phase, reason=reason)

    if event_name == "workflow_dispatch":
        return allow("manual", "手动/自愈派发，门禁放行以便随时补采与验证")
    # push 与 schedule 同等按日历窗口判定。2026-11-10 实测：push 若在
    # 盘前无条件放行，新浪 gb_spy 的「前收盘价」字段被写成现价，整轮
    # 采集报 No verified SPY source timestamp 而失败。改代码只跑回归，
    # 不在非交易时段误采集；需要立即验证请用 workflow_dispatch。
    if event_name not in ("schedule", "push"):
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

    # 收盘关键任务：GitHub scheduled 事件实测会延迟数小时，甚至拖到周末凌晨。
    # 用「最近一个已收盘交易日」而不是「当前美东日期」判定，并在容忍范围内
    # 输出 as_of，让下游用逻辑时间补采仍挂在线的收盘报价。
    if task == "closing" and late_ok:
        latest = latest_closed_session(now, cal)
        if latest:
            late_min = (now - latest["close"]).total_seconds() / 60
            if 0 <= late_min <= CLOSING_LATE_LIMIT_MIN:
                if late_min <= WINDOWS["closing"][1]:
                    base["session"] = latest["session"]
                    phase = "early_close" if latest["early_close"] else "regular"
                    return allow(phase, "%s 交易日收盘关键任务（收盘后 %.0f 分钟）"
                                 % (latest["session"], late_min))
                return dict(
                    allow("late_backfill",
                          "GitHub 延迟 %.0f 分钟，按 %s 收盘价补采，不丢当日数据"
                          % (late_min, latest["session"])),
                    session=latest["session"],
                    as_of=(latest["close"] + dt.timedelta(minutes=CLOSING_AS_OF_MIN)).isoformat(),
                )
        # 超出补跑容忍范围，且当下仍在盘前/盘中：不能把 closing 当常规任务
        # 放行，否则会在盘中唤醒 Neon、污染「每日收盘一次」的语义。
        if info["trading_day"] and info["close"] and now < info["close"]:
            return skip("before_close", "收盘关键任务未到收盘时刻，交还给盘中任务")

    if not info["trading_day"]:
        kind = "周末" if dt.date.fromisoformat(info["session"]).weekday() > 4 else "NYSE 休市日"
        return skip("closed_day", "%s %s，不采集、不同步、不唤醒冷备" % (info["session"], kind))

    if task == "opening":
        delta = (now - info["open"]).total_seconds() / 60
        if delta < OPENING_FROM_MIN:
            return skip("before_opening", "距开盘仅 %.0f 分钟，未到开盘后 30 分钟关键窗口" % delta)
        if now >= info["close"]:
            return skip("after_close", "已收盘，开盘快照由 closing 任务覆盖")
        if delta > OPENING_TO_MIN:
            return allow("late_opening",
                         "开盘关键任务延迟 %.0f 分钟，仍在盘中，抓实时价补一次" % delta)
        return allow("opening", "开盘后 %.0f 分钟，关键一次" % delta)

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
    p.add_argument("--closing-only", action="store_true", help="按已结算场次运行")
    p.add_argument("--as-of")
    p.add_argument("--late-ok", action="store_true",
                   help="仅收盘关键 cron 使用：允许 GitHub 延迟到达时按目标场次补采")
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()

    now = dt.datetime.fromisoformat(args.at) if args.at else dt.datetime.now(UTC)
    if args.closing_only:
        result = closing_collection_gate(now, load_calendar(), args.as_of)
        result.update(task='closing', reason='收盘场次可采集' if result['allow'] else '收盘采集窗口未到或已结束')
    else:
        result = gate(args.task, now, event_name=os.environ.get("EVENT_NAME", "schedule"), late_ok=args.late_ok)
    if args.gate:
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a", encoding="utf-8") as fh:
                fh.write("allow=%s\n" % ("true" if result["allow"] else "false"))
                fh.write("reason=%s\n" % result["reason"])
                fh.write("session=%s\n" % result["session"])
                fh.write("task=%s\n" % result["task"])
                # 延迟补跑专用：非空时下游必须以 --as-of 传入逻辑时间
                fh.write("as_of=%s\n" % result.get("as_of", ""))
                fh.write("critical=%s\n" % (
                    "true" if result["task"] in ("opening", "closing") else "false"))
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
            ("opening", "2026-11-09T15:00:00+00:00", True, "开盘后 30 分钟关键"),
            ("opening", "2026-11-09T13:00:00+00:00", False, "开盘前不放行"),
            ("opening", "2026-11-09T18:00:00+00:00", True, "盘中延迟仍抓实时价"),
            ("opening", "2026-11-26T15:00:00+00:00", False, "感恩节无开盘任务"),
            ("closing", "2026-11-08T21:17:00+00:00", False, "周末不补采过期场次"),
        ]
        failed = []
        for task, stamp, expected, label in cases:
            got = gate(task, dt.datetime.fromisoformat(stamp), cal)["allow"]
            if got != expected:
                failed.append("%s expect=%s got=%s" % (label, expected, got))
        delayed = dt.datetime.fromisoformat("2026-11-10T03:49:00+00:00")
        if gate("closing", delayed, cal)["allow"]:
            failed.append("未开 late_ok 时延迟任务不应放行（北京白天停采语义）")
        late = gate("closing", delayed, cal, late_ok=True)
        if not late["allow"] or late.get("phase") != "late_backfill":
            failed.append("late_ok 未放行延迟补采: %r" % late)
        if late.get("as_of") != "2026-11-09T21:17:00+00:00":
            failed.append("延迟补采 as_of 错误: %r" % late.get("as_of"))
        if closing_as_of(dt.datetime.fromisoformat("2026-11-10T13:00:00+00:00"), cal) \
                != "2026-11-09T21:17:00+00:00":
            failed.append("closing_as_of 未定位到 11-09 收盘")
        print(json.dumps({"self_test": "FAIL" if failed else "OK", "cases": len(cases),
                          "failed": failed}, ensure_ascii=False))
        return 1 if failed else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
