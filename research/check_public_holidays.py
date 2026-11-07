#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""免费公共假日接口 → 年度漂移核对（只报告，绝不写生产日历）。

为什么只是「核对」而不是「数据源」
  Nager.Date（https://date.nager.at，开源、免密钥、无需注册）给的是
  **美国联邦 / 州的公共假日**，与 NYSE 交易日历系统性不同：
    Good Friday        NYSE 休市，联邦不放假（部分州才放）
    Columbus Day       NYSE 照常开市，联邦放假
    Veterans Day       NYSE 照常开市，联邦放假
    半日 13:00 收盘    联邦日历里根本没有这个概念
    12-31 遇周六元旦   NYSE Rule 7.2 通常不补休，联邦会顺延观测
  直接拿它跳过采集，会漏采两个完整交易日、又漏掉 Good Friday 休市。
  所以生产判定只用已人工复核的 research/exchange_calendar.json；
  本脚本负责每年把它们对齐一次，发现「日历快过期」或「出现未知差异」时
  提醒人工去 NYSE 官方公告复核，绝不自动改写证据。

用法
  python research/check_public_holidays.py                 # 当年 + 未来 2 年
  python research/check_public_holidays.py --years 2026 2027
  python research/check_public_holidays.py --output output/calendar_drift.json

退出码：0 正常 / 1 需要人工复核或日历覆盖不足 / 2 接口不可用
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import trading_window as tw  # noqa: E402  纯标准库，复用同一份日历与指纹

API = "https://date.nager.at/api/v3/PublicHolidays/{year}/US"
FEDERAL_ONLY_TRADING = ("columbus", "indigenous peoples", "veterans", "new year")


def select_federal(raw: list, year: int) -> list[dict]:
    """只保留全国性 Public 假日：州级假日 / Observance / Bank 专用项全部排除。

    Nager.Date 的 US 数据里 Good Friday 只属于部分州（global=false +
    counties），Lincoln's Birthday 是 Observance；若不过滤，会把它们误当成
    联邦假日，进而把 NYSE 的 Good Friday 休市判成「日历错误」。
    """
    if not isinstance(raw, list):
        raise ValueError("接口返回格式异常: %s" % type(raw).__name__)
    return [r for r in raw
            if isinstance(r, dict)
            and r.get("global") is True
            and "Public" in (r.get("types") or [])
            and not r.get("counties")
            and str(r.get("date", "")).startswith(str(year))]


def fetch_year(year: int, timeout: float = 15.0) -> list[dict]:
    """拉一年的美国公共假日；只保留全国性 Public 假日。"""
    req = urllib.request.Request(
        API.format(year=year),
        headers={"User-Agent": "trade-calendar-check/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    return select_federal(raw, year)


def easter(year: int) -> dt.date:
    """Anonymous Gregorian algorithm，用来识别 Good Friday。"""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return dt.date(year, month, day)


def is_weekend(iso: str) -> bool:
    return dt.date.fromisoformat(iso).weekday() > 4


def classify(year: int, federal: list[dict], cal: dict) -> dict:
    closed = set(cal["closed"])
    fed_by_date = {str(r["date"]): str(r.get("name") or r.get("localName") or "")
                   for r in federal}
    good_friday = (easter(year) - dt.timedelta(days=2)).isoformat()

    both = sorted(d for d in fed_by_date if d in closed)
    nyse_only = sorted(d for d in closed
                       if d.startswith(str(year)) and d not in fed_by_date
                       and not is_weekend(d))
    federal_only = sorted(d for d in fed_by_date
                          if d not in closed and not is_weekend(d)
                          and cal["coverage_from"] <= d <= cal["coverage_to"])

    # NYSE 休市但联邦日历没给：除 Good Friday 外，通常只是「接口没返回完整」，
    # 不代表已复核日历有误。列为信息性 feed_gaps，不触发人工复核告警。
    known_good_friday, feed_gaps = [], []
    for d in nyse_only:
        (known_good_friday if d == good_friday else feed_gaps).append(
            {"date": d, "kind": "NYSE 休市但联邦接口未给出（通常为接口数据不全）"})

    needs_review, known_trading, unknown_trading = [], [], []
    for d in federal_only:
        name = fed_by_date[d].lower()
        if any(k in name for k in FEDERAL_ONLY_TRADING):
            known_trading.append({"date": d, "name": fed_by_date[d],
                                  "kind": "联邦放假但 NYSE 照常开市 / Rule 7.2"})
        else:
            unknown_trading.append({"date": d, "name": fed_by_date[d],
                                    "kind": "联邦放假且 NYSE 日历未休市，需复核"})
    needs_review.extend(unknown_trading)

    return {
        "year": year,
        "federal_public_holidays": len(fed_by_date),
        "both_closed": both,
        "good_friday_expected": good_friday,
        "known_divergence": {
            "good_friday_nyse_closed": known_good_friday,
            "federal_only_but_nyse_open": known_trading,
        },
        "feed_gaps": feed_gaps,
        "needs_review": needs_review,
        "early_close_dates": sorted(d for d in cal["early_close"]
                                    if d.startswith(str(year))),
    }


def build_report(years: list[int], cal: dict, fetch=fetch_year) -> dict:
    per_year, fetch_errors = [], []
    for year in years:
        try:
            per_year.append(classify(year, fetch(year), cal))
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            fetch_errors.append({"year": year, "error": str(exc)[:160]})
    needs = [x for y in per_year for x in y["needs_review"]]
    feed_gaps = [x for y in per_year for x in y["feed_gaps"]]
    last_year = max(years)
    # 硬失败：已复核日历连本次核对的年份都没盖住，必须人工补证据。
    # NYSE 通常只公布未来 2~3 年，不能要求覆盖到「最大年份 + 1 年」。
    expired = cal["coverage_to"] < "%d-12-31" % last_year
    # 软提醒：距离覆盖终点不足 400 天时提示准备下一版日历，但不判失败。
    days_left = (dt.date.fromisoformat(cal["coverage_to"]) - dt.date.today()).days
    coverage_warning = days_left < 400
    if fetch_errors:
        status = "api_unavailable"
    elif expired:
        status = "coverage_expiring"
    elif needs:
        status = "review"
    else:
        status = "ok"
    return {
        "checked_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": API,
        "role": "只做年度漂移核对；生产判定只用已复核 NYSE 日历",
        "nyse_calendar_sha256": tw.fingerprint(cal),
        "coverage": {"from": cal["coverage_from"], "to": cal["coverage_to"],
                     "expired": expired, "days_left": days_left,
                     "warning": coverage_warning},
        "years": per_year,
        "fetch_errors": fetch_errors,
        "feed_gaps": feed_gaps,
        "needs_review": needs,
        "early_close_note": "半日交易（13:00 ET）不在公共假日接口里，须人工核对 NYSE 官方公告",
        "status": status,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    now = dt.datetime.now(dt.timezone.utc).year
    p.add_argument("--years", type=int, nargs="*", default=[now, now + 1, now + 2])
    p.add_argument("--output", help="把报告另存为 JSON")
    args = p.parse_args()
    cal = tw.load_calendar()
    report = build_report(sorted(set(args.years)), cal)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    return {"ok": 0}.get(report["status"], 2 if report["status"] == "api_unavailable" else 1)


if __name__ == "__main__":
    sys.exit(main())
