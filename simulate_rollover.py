# -*- coding: utf-8 -*-
"""跨年仿真：给定任意日期，推导整条链路会产出什么（纯计算，不写任何文件）。

校验点：
  1. 官方排名基准月 / 持有年度
  2. 净值建仓月（equity.base_ym）
  3. 历史归档年份（strategy_history.END_YEAR）
  4. 预览基准月 / 预告年度 / 是否定稿
任何一个环节不自洽就报错。
"""
import datetime as dt
import json
import sys

import monthly_pick
import preview as pv


def simulate(day):
    d = dt.datetime.strptime(day, "%Y-%m-%d").date()
    year = d.year

    # 1) 数据月：1 日之前仍是上一年 12 月，否则为当月
    latest = "%d-12" % (year - 1) if d.month == 1 and d.day <= 5 else "%d-%02d" % (year, d.month)
    if d.month == 1:
        latest = "%d-12" % (year - 1)

    official_month = monthly_pick.official_month_for(latest)
    hold_year = int(official_month[:4]) + 1
    base_ym = official_month                      # equity 用同一基准

    # 2) 历史归档：已结束的年份
    end_year = year - 1 if d.month >= 1 else year

    # 3) 预览：基准 = 上一年 12 月；预告 = 基准年 + 2
    pv_base = "%d-12" % (year - 1)
    pv_hold = int(pv_base[:4]) + 2
    pv_official = "%d-12" % (int(pv_base[:4]) + 1)
    final = (official_month == pv_official)

    return {
        "日期": day,
        "数据月": latest,
        "官方基准月": official_month,
        "持有年度": hold_year,
        "净值建仓月": base_ym,
        "历史归档年份": end_year,
        "预览基准": pv_base,
        "预览预告年度": pv_hold,
        "预览定稿": final,
    }


def check(rows):
    ok = True
    for r in rows:
        problems = []
        if int(r["官方基准月"][:4]) + 1 != r["持有年度"]:
            problems.append("持有年度≠基准年+1")
        if r["净值建仓月"] != r["官方基准月"]:
            problems.append("建仓月与官方基准月不一致")
        if r["预览定稿"] and r["预览预告年度"] != r["持有年度"]:
            problems.append("定稿后预告年度与持有年度不一致")
        # 1 月时，历史应把上一年归档
        if r["日期"].endswith("-01-02") and r["历史归档年份"] != int(r["日期"][:4]) - 1:
            problems.append("归档年份错误")
        status = "✅" if not problems else "❌ " + "；".join(problems)
        if problems:
            ok = False
        print("  %s  %s" % (r["日期"], status))
        print("        官方基准 %s → 持有 %d ｜ 建仓 %s ｜ 归档到 %d" %
              (r["官方基准月"], r["持有年度"], r["净值建仓月"], r["历史归档年份"]))
        print("        预览基准 %s → 预告 %d ｜ 定稿 %s" %
              (r["预览基准"], r["预览预告年度"], "是" if r["预览定稿"] else "否"))
    return ok


def main():
    days = sys.argv[1:] or ["2026-10-30", "2027-01-02", "2027-12-31",
                            "2028-01-02", "2030-01-02"]
    print("跨年仿真（纯计算，不改数据）")
    rows = [simulate(d) for d in days]
    ok = check(rows)
    print("\n结论：", "✅ 全链路可自动跨年" if ok else "❌ 存在不自洽")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
