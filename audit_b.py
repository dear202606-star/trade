# -*- coding: utf-8 -*-
"""口径 B 的独立审计（不复用 variant_b.py 的函数，防止同一错误互相掩盖）。

检查项：
  1. 每年持仓 3 只、权重和 = 1、权重形状为等权或 30/30/40
  2. 单行业 ≤60%，同行业 ≤2 只
  3. 每只持仓对应证券在上一年连续在册，已核验同股更名使用年末代码
  4. 每只持仓确实是合格池里 ret12 的前列（不应有更靠前且行业允许的票被跳过）
  5. 年收益 = 各票 Dec(Y-1)→Dec(Y) 加权；累计 = 逐年复利
  6. 曲线末点 ≈ 1 + total；基准 = 逐年 bench 复利
用法：python audit_b.py [--json]
"""
import json
import datetime as dt
import os
import sys

import pandas as pd
import nominal_prices
import holding_paths
import security_membership
import sys as _sys
try:                                    # Windows cp936 下避免 emoji 打印崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(BASE, "docs", "data", "b", "strategy_history.json")
CURVE = os.path.join(BASE, "docs", "data", "b", "curve.json")

PROBLEMS = []
CHECKS = [0]


def ck(cond, name, detail=""):
    CHECKS[0] += 1
    if not cond:
        PROBLEMS.append("%s %s" % (name, detail))
    return cond


def full_year_member(frame,aliases,ticker,year):
    """Independent day-by-day check, separate from the production interval merge."""
    relevant=[a for a in aliases if int(a['effective_date'][:4])==year
              and ticker in (a['old'],a['current'])]
    if len(relevant)>1:raise RuntimeError('Ambiguous same-year ticker chain')
    periods=[(r.ticker,str(r.start_date)[:10],str(r.end_date)[:10]) for r in frame.itertuples()]
    if relevant:
        alias=relevant[0]
        if ticker==alias['old']:return False
        day=dt.date(year,1,1)
        while day.year==year:
            text=str(day);code=alias['old'] if text<alias['effective_date'] else alias['current']
            if not any(s==code and a<=text<=b for s,a,b in periods):return False
            day+=dt.timedelta(days=1)
        return True
    return any(s==ticker and a<='%d-01-01'%year and b>='%d-12-31'%year for s,a,b in periods)


def main():
    import db_ref
    members=db_ref.load_members()
    mem=pd.DataFrame([{'ticker':s,'start_date':a,'end_date':b}
                      for s,periods in members.items() for a,b in periods])
    aliases=security_membership.load_aliases()
    # 必须与被审计对象同源：variant_b 读在线库，审计也读在线库（DB 或 REST）
    bars = None
    try:
        import db_prices
        _all = db_prices.load_all()
        if _all:
            bars = {t: [[m, v[0], v[1]] for m, v in sorted(d.items())] for t, d in _all.items()}
            print("审计数据源：在线库 %d 只" % len(bars), flush=True)
    except Exception as e:
        print("⚠ 在线库不可用：%s" % str(e)[:70], flush=True)
    if not bars:raise RuntimeError('Online audit prices unavailable; local fallback is prohibited')
    sect = db_ref.load_sectors()
    hist = json.load(open(HIST, encoding="utf-8"))
    curve = json.load(open(CURVE, encoding="utf-8"))

    bmap = {t: {x[0]: (x[1], x[2]) for x in rows} for t, rows in bars.items()}
    years = hist["years"]

    def member_full(t, y0):
        return full_year_member(mem,aliases,t,y0)

    def ret12(t, y0):
        b = bmap.get(t) or {}
        a_m, b_m = "%d-12" % (y0 - 1), "%d-12" % y0
        b=holding_paths.momentum_bars(t,b,a_m,b_m)
        if a_m not in b or b_m not in b or not b[a_m][0]:
            return None
        return b[b_m][0] / b[a_m][0] - 1

    prod = 1.0
    for r in years:
        y = r["year"]
        y0 = y - 1
        hs = r["holdings"]
        ck(len(hs) == 3, "第 %d 年持仓 3 只" % y, "实际 %d" % len(hs))
        wsum = sum(h["weight"] for h in hs)
        ck(abs(wsum - 1) < 1e-6, "第 %d 年权重和=1" % y, "%.4f" % wsum)

        shapes = sorted(round(h["weight"], 3) for h in hs)
        ck(shapes in ([0.333, 0.333, 0.334], [0.333, 0.334, 0.333],
                      [0.334, 0.333, 0.333], [0.3, 0.3, 0.4], [0.3, 0.4, 0.3],
                      [0.4, 0.3, 0.3], [0.333, 0.333, 0.333]),
           "第 %d 年权重形状合法" % y, str(shapes))

        sec = {}
        for h in hs:
            sec[h["sector"]] = sec.get(h["sector"], 0) + h["weight"]
            ck(member_full(h["ticker"], y0),
               "第 %d 年 %s 上一年整年在册" % (y, h["ticker"]),
               "不满足全年成分规则")
        ck(max(sec.values()) <= 0.6001, "第 %d 年单行业 ≤60%%" % y,
           "最高 %.0f%%" % (max(sec.values()) * 100))
        ck(all(v <= 2 + 1e-9 for v in [sum(1 for h in hs if h["sector"] == k)
                                       for k in sec]), "第 %d 年同行业 ≤2 只" % y)

        # 排名：合格池里不应有「行业允许但涨幅更高」的票被漏掉
        pool = []
        for t in mem['ticker'].unique():
            if not member_full(t,y0):continue
            rr = ret12(t, y0)
            if rr is None:
                continue
            b = bmap.get(t) or {}
            raw = nominal_prices.price(t, "%d-12" % y0)
            if not raw or raw < 5:
                continue
            if abs(rr) > 5.0:          # 价格异常（伪拆股/拼接）
                continue
            pool.append((t, rr, sect.get(t) or "Unknown"))
        pool.sort(key=lambda x: -x[1])
        chosen = {h["ticker"] for h in hs}
        cnt, skipped, picked = {}, [], 0
        for t, rr, ss in pool:
            if picked >= 3:
                break
            key = "__unk__" if ss == "Unknown" else ss
            cap = 1 if ss == "Unknown" else 2
            if cnt.get(key, 0) >= cap:
                continue
            if t not in chosen:
                skipped.append((t, rr))
                break
            cnt[key] = cnt.get(key, 0) + 1
            picked += 1
        ck(not skipped, "第 %d 年选中的是合格池最优" % y,
           "被跳过的更优票：%s" % (skipped[:1],))

        # 收益
        calc = 0.0
        for h in hs:
            path=holding_paths.values(h['ticker'],y,lambda symbol:bmap.get(symbol) or {})
            calc+=h['weight']*(path[-1]-1)
        ck(abs(calc - r["ret"]) < 0.005, "第 %d 年收益可复算" % y,
           "记录 %.4f / 复算 %.4f" % (r["ret"], calc))
        ck(r.get("dd") is not None and r["dd"] <= 0, "第 %d 年回撤非正" % y,
           str(r.get("dd")))
        prod *= (1 + r["ret"])

    ck(abs(prod - (1 + hist["summary"]["total"])) < 0.02, "累计 = 逐年复利",
       "复利 %.2f / 记录 %.2f" % (prod, 1 + hist["summary"]["total"]))
    pts = curve["points"]
    ck(abs(pts[-1]["s"] - prod) < 0.02, "曲线末点 = 累计",
       "%.2f vs %.2f" % (pts[-1]["s"], prod))
    bench = 1.0
    for r in years:
        bench *= (1 + (r.get("bench") or 0))
    ck(abs(bench - (1 + hist["summary"]["benchmark"])) < 0.02, "基准 = 逐年复利",
       "%.2f vs %.2f" % (bench, 1 + hist["summary"]["benchmark"]))
    ck(len(pts) >= 150, "曲线点数充足", str(len(pts)))

    print("\n口径 B 审计：检查 %d 项，问题 %d 项" % (CHECKS[0], len(PROBLEMS)))
    for p in PROBLEMS:
        print("  ❌ " + p)
    if not PROBLEMS:
        print("  ✅ 全部通过")
        print("  累计 %.2f 倍 ｜ 年化 %.1f%% ｜ 最深回撤 %.1f%% ｜ 基准 %.2f 倍" % (
            prod, hist["summary"]["cagr"] * 100,
            hist["summary"]["worst_intra"] * 100, bench))
    return 0 if not PROBLEMS else 1


if __name__ == "__main__":
    sys.exit(main())
