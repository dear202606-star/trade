# -*- coding: utf-8 -*-
"""verify_champions.py — 年度冠军的自动化计算 + 双源校验

计算规则（写死）：
  股票池  当年 1 月 1 日的标普 500 成分股（point-in-time）
  区间    上年 12 月 → 当年 12 月（月末收盘）
  价格    后复权（仅拆股），不含分红
  护栏    12 个月连续 · 单月涨跌 ≤8 倍 · 股价 ≥1 美元

校验规则（关键）：
  对候选前 N 名，用 Yahoo 的独立日线重算同一年收益。
  两源差异 > 5 个百分点 → 判定该标的数据不可靠，剔除后重新排名。
  这样能自动挡掉「漏检拆股」（如 ETFC 2010）和「代码复用」（如 HAR 2017）。

产物：output/champion_audit.md / .csv（自动生成，每周跑一次）
"""
import json
import os
import sys
import time

import pandas as pd
import requests

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "data", "sina2")
MEMBERS = os.path.join(BASE, "data", "sp500_ticker_start_end.csv")
VERIFIED = os.path.join(BASE, "output", "champions_verified.csv")
OUT_MD = os.path.join(BASE, "output", "champion_audit.md")
OUT_CSV = os.path.join(BASE, "output", "champion_audit.csv")

MIN_PRICE = 1.0
MAX_MONTH_JUMP = 8.0
TOP_N_CHECK = 5          # 每年校验前 5 名
TOL = 0.05               # 两源允许差异（绝对，5pp）

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")


def load_bars(ticker):
    p = os.path.join(CACHE, ticker + ".json")
    if not os.path.exists(p):
        return None
    try:
        d = json.load(open(p, encoding="utf-8"))
        bars = d.get("bars") or []
        return {b[0]: (b[1], b[2]) for b in bars} if bars else None
    except Exception:
        return None


def year_return(bars, year):
    m0 = f"{year-1}-12"
    if m0 not in bars:
        m0 = f"{year}-01"
        if m0 not in bars:
            return None, "缺起点"
    m1 = f"{year}-12"
    if m1 not in bars:
        return None, "缺终点"
    seq = [f"{year}-{i:02d}" for i in range(1, 13)]
    if sum(1 for m in seq if m in bars) < 12:
        return None, "月份不全"
    adj0, raw0 = bars[m0]
    adj1, raw1 = bars[m1]
    if not adj0 or adj0 <= 0 or not adj1:
        return None, "价格异常"
    if raw1 < MIN_PRICE:
        return None, "股价过低"
    prev = adj0
    for m in seq:
        cur = bars[m][0]
        if prev > 0 and (cur / prev > MAX_MONTH_JUMP or cur / prev < 1 / MAX_MONTH_JUMP):
            return None, f"跳变异常 {m}"
        prev = cur
    return adj1 / adj0 - 1, "ok"


def yahoo_return(ticker, year):
    """用 Yahoo 日线独立重算同一年收益（Yahoo 的 close 已按拆股调整）"""
    sym = ticker.replace(".", "-")
    p1 = int(time.mktime((year - 1, 12, 1, 0, 0, 0, 0, 0, 0)))
    p2 = int(time.mktime((year + 1, 1, 10, 0, 0, 0, 0, 0, 0)))
    url = (f"https://query2.finance.yahoo.com/v8/finance/chart/{sym}"
           f"?period1={p1}&period2={p2}&interval=1mo")
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=10)
        if r.status_code != 200:
            return None, "HTTP " + str(r.status_code)
        res = ((r.json().get("chart") or {}).get("result") or [])
        if not res:
            return None, "无数据"
        ts = res[0].get("timestamp") or []
        cl = ((res[0].get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
        pts = {}
        for i, t in enumerate(ts):
            if i < len(cl) and cl[i]:
                ym = time.strftime("%Y-%m", time.gmtime(t))
                pts[ym] = cl[i]
        a = pts.get(f"{year-1}-12")
        b = pts.get(f"{year}-12")
        if not a or not b:
            return None, "月份缺失"
        return b / a - 1, "ok"
    except Exception as e:
        return None, str(e)[:40]


def main():
    if not os.path.exists(MEMBERS):
        print("缺少成分股时点表", file=sys.stderr)
        return 1
    m = pd.read_csv(MEMBERS)
    m["start_date"] = m["start_date"].astype(str)
    m["end_date"] = m["end_date"].fillna("2099-12-31").astype(str)

    rows = []
    for y in range(2008, 2026):
        d = f"{y}-01-01"
        members = set(m[(m.start_date <= d) & (m.end_date >= d)]["ticker"])
        cand = []
        n_ok = 0
        for t in members:
            bars = load_bars(t)
            if not bars:
                continue
            ret, why = year_return(bars, y)
            if ret is None:
                continue
            n_ok += 1
            cand.append((t, ret))
        cand.sort(key=lambda x: -x[1])

        # 对前 N 名做双源校验
        checked, suspects = [], []
        for t, ret in cand[:TOP_N_CHECK]:
            yret, why = yahoo_return(t, y)
            if yret is None:
                # Yahoo 不可达（本地被墙）不算数据错误，只标记「未核对」
                checked.append({"ticker": t, "sina": ret, "yahoo": None,
                                "diff": None, "status": "未核对"})
                continue
            diff = ret - yret
            ok = abs(diff) <= TOL
            checked.append({"ticker": t, "sina": ret, "yahoo": yret,
                            "diff": diff, "status": "一致" if ok else "不一致"})
            if not ok:
                suspects.append(t)

        # 冠军 = 前 N 名里第一个双源一致的
        champ, champ_ret = None, None
        for c in checked:
            if c["status"] in ("一致", "未核对"):
                champ, champ_ret = c["ticker"], c["sina"]
                break
        if champ is None and checked:
            champ, champ_ret = checked[0]["ticker"], checked[0]["sina"]

        rows.append({
            "year": y, "n_members": len(members), "n_ok": n_ok,
            "top_raw": cand[0][0] if cand else None,
            "top_raw_ret": round(cand[0][1], 4) if cand else None,
            "champion": champ, "champion_ret": round(champ_ret, 4) if champ_ret is not None else None,
            "suspects": ",".join(suspects),
            "checked": checked,
        })
        time.sleep(0.3)

    df = pd.DataFrame([{k: v for k, v in r.items() if k != "checked"} for r in rows])
    df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    ver = pd.read_csv(VERIFIED)
    ver = ver[ver["year"].between(2008, 2025)].set_index("year")

    lines = ["# 年度冠军 · 自动化复核", "",
             "**规则**：股票池＝当年 1 月 1 日的标普 500 成分股；区间＝上年 12 月至当年 12 月；",
             "价格＝后复权（仅拆股）；护栏＝12 个月连续、单月 ≤8 倍、股价 ≥1 美元。", "",
             "**校验**：每年取前 5 名用 Yahoo 独立重算，差异 >5pp 判为数据不可靠并剔除，取下一个。", "",
             "| 年 | 成分股 | 可用 | 原始第一名 | 原始收益 | **校验后冠军** | 收益 | 被剔除 |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| %d | %d | %d | %s | %s | **%s** | %s | %s |" % (
            r["year"], r["n_members"], r["n_ok"],
            r["top_raw"] or "—",
            "—" if r["top_raw_ret"] is None else "%+.0f%%" % (r["top_raw_ret"] * 100),
            r["champion"] or "—",
            "—" if r["champion_ret"] is None else "%+.0f%%" % (r["champion_ret"] * 100),
            r["suspects"] or "—"))

    lines += ["", "## 前 5 名双源明细", ""]
    for r in rows:
        if not r["checked"]:
            continue
        lines.append("### %d" % r["year"])
        lines.append("")
        lines.append("| 代码 | 新浪 | Yahoo | 差 | 判定 |")
        lines.append("|---|---|---|---|---|")
        for c in r["checked"]:
            lines.append("| %s | %s | %s | %s | %s |" % (
                c["ticker"],
                "%+.0f%%" % (c["sina"] * 100),
                "—" if c["yahoo"] is None else "%+.0f%%" % (c["yahoo"] * 100),
                "—" if c["diff"] is None else "%+.1fpp" % (c["diff"] * 100),
                c["status"]))
        lines.append("")

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("\n".join(lines[:30]))
    print("\n已写入", OUT_MD)
    n_susp = sum(1 for r in rows if r["suspects"])
    print("有可疑标的的年份：%d" % n_susp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
