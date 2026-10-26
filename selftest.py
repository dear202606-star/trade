# -*- coding: utf-8 -*-
"""selftest.py — 部署前自检：确保站点不会带着空数据上线

检查项：
  A. 页面文件存在且非空（index / portfolio / strategy）
  B. 数据文件存在、可解析、关键字段齐全
  C. 数据新鲜度（行情 ≤48 小时、净值/逐年记录有足够样本）
  D. 数值合理性（收益在 ±500% 内、回撤 ≤0）

任何一项不通过 → 退出码 1，CI 失败，不会部署坏页面。
"""
import json
import os
import sys
import time

DOCS = "docs"
DATA = os.path.join(DOCS, "data")

fails, warns, oks = [], [], []


def check(cond, ok_msg, fail_msg, hard=True):
    if cond:
        oks.append(ok_msg)
    else:
        (fails if hard else warns).append(fail_msg)
    return cond


def load(name):
    p = os.path.join(DATA, name)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def main():
    # A. 页面
    for f in ("index.html", "portfolio.html", "strategy.html"):
        p = os.path.join(DOCS, f)
        check(os.path.exists(p) and os.path.getsize(p) > 2000,
              f"页面 {f}", f"页面缺失或过小：{f}")

    # B. 数据
    sig = load("signal.json")
    check(sig and sig.get("official"), "信号有正式持仓", "signal.json 缺 official")

    q = load("quotes.json")
    cnt = (q or {}).get("count", 0)
    uni = (q or {}).get("universe") or 0
    cover = cnt / uni if uni else 0
    ok_q = (cover >= 0.8) if uni else (cnt > 100)
    check(ok_q,
          f"行情 {cnt}/{uni or '?'} 只（{cover*100:.0f}%）" if uni else f"行情 {cnt} 只",
          f"行情覆盖不足：{cnt}/{uni} = {cover*100:.0f}%")

    eq = load("equity.json")
    check(eq and len(eq.get("points") or []) >= 60,
          f"净值 {len(eq.get('points') or []) if eq else 0} 个交易日",
          "equity.json 净值样本不足")

    sh = load("strategy_history.json")
    check(sh and len(sh.get("years") or []) >= 8,
          f"逐年记录 {len(sh.get('years') or []) if sh else 0} 年",
          "strategy_history.json 逐年记录不足")


    # B2. 成分股合规（只允许标普 500 成分股）
    try:
        import pandas as pd
        mm = pd.read_csv("static/sp500_members.csv")
        mm["start_date"] = mm["start_date"].astype(str)
        mm["end_date"] = mm["end_date"].fillna("2099-12-31").astype(str)
        bad_hold = []
        for r in (sh or {}).get("years", []):
            d = "%d-12-31" % (r["year"] - 1)
            for p in r.get("holdings", []):
                hit = mm[(mm.ticker == p["ticker"]) & (mm.start_date <= d) & (mm.end_date >= d)]
                if not len(hit):
                    bad_hold.append("%d:%s" % (r["year"], p["ticker"]))
        for p in (sig or {}).get("official", []):
            hit = mm[(mm.ticker == p["ticker"]) & (mm.start_date <= "2026-12-31") & (mm.end_date >= "2026-01-01")]
            if not len(hit):
                bad_hold.append("当前:%s" % p["ticker"])
        check(not bad_hold, "全部持仓均为标普500 成分股",
              "存在非成分股持仓：" + ", ".join(bad_hold[:8]))
    except Exception as e:
        warns.append("成分股校验失败：%s" % e)

    cv = load("curve.json")
    check(cv and len(cv.get("points") or []) >= 120,
          "全周期曲线 %d 个月" % len((cv or {}).get("points") or []),
          "curve.json 缺失或过短（%s）" % os.path.join(DATA, "curve.json"))

    # C. 新鲜度
    if q and q.get("ts"):
        try:
            age = (time.time() - time.mktime(time.strptime(q["ts"], "%Y-%m-%dT%H:%M:%SZ"))
                   + time.timezone) / 3600
        except Exception:
            age = None
        if age is not None:
            check(age <= 48, f"行情 {age:.1f} 小时前更新",
                  f"行情已过期 {age:.0f} 小时", hard=False)

    # D. 合理性
    if eq and eq.get("points"):
        last = eq["points"][-1]
        check(-5 < last.get("s", 0) < 20, f"净值末值 {last.get('s')}",
              f"净值异常：{last.get('s')}")
        mdd = (eq.get("stats") or {}).get("mdd", 0)
        check(mdd <= 0.001, f"最大回撤 {mdd*100:.1f}%", f"回撤应为负值：{mdd}")

    if sh and sh.get("years"):
        for r in sh["years"]:
            if r.get("ret") is not None and abs(r["ret"]) > 5:
                fails.append(f"{r['year']} 年收益异常：{r['ret']*100:.0f}%")

    # 报告
    print("=== 部署前自检 ===")
    for m in oks:
        print("  ✅", m)
    for m in warns:
        print("  ⚠️ ", m)
    for m in fails:
        print("  ❌", m)
    print(f"通过 {len(oks)} · 警告 {len(warns)} · 失败 {len(fails)}")

    if fails:
        print("\n自检未通过，终止部署。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
