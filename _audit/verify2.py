# -*- coding: utf-8 -*-
"""Phase 2: 开关是否进入回测 / 2026 敞口路径 / 账户口径"""
import json, os
import pandas as pd

os.chdir(r"C:\Users\Administrator\Desktop\量化")
cv = json.load(open("docs/data/curve.json", encoding="utf-8"))
sh = json.load(open("docs/data/strategy_history.json", encoding="utf-8"))
eq = json.load(open("docs/data/equity.json", encoding="utf-8"))
sig = json.load(open("docs/data/signal.json", encoding="utf-8"))

# 1) 逐年 ret 是否恒等于满仓 sum(w*ret_year)
print("[A] 逐年收益 vs 满仓加权（若开关生效，二者不应完全相等）")
bad = 0
for y in sh["years"]:
    s = sum(p["weight"] * (p["ret_year"] or 0) for p in y["holdings"])
    d = y["ret"] - s
    if abs(d) > 5e-5: bad += 1
    print("   %d  ret=%+.4f  sum(w*r)=%+.4f  diff=%+.5f" % (y["year"], y["ret"], s, d))
print("   years with mismatch > 0.005pp:", bad, "/", len(sh["years"]))

# 2) 用站点自己的标普月线数开关被触发的月数
b = {p["d"]: p["b"] for p in cv["points"]}
eqm = {}
for p in eq["points"]:
    eqm[p["d"][:6]] = p["b"]
months = sorted(b.keys())
spy = {m: b[m] for m in months}
for m, v in eqm.items():
    spy[m] = v  # 2026 月末 b 覆盖
ms = sorted(spy.keys())
below = []
for i, m in enumerate(ms):
    if i < 9:
        continue
    win = ms[i - 9:i + 1]
    ma = sum(spy[x] for x in win) / 10
    if spy[m] < ma:
        below.append(m)
print("\n[B] 标普收盘 < 10 月均线 的月份（站点自身月线，共 %d 个月）：" % len(ms))
print("   count=%d -> %s" % (len(below), ", ".join(below)))
lb = [m for m in below if m >= "2026-01"]
print("   其中 2026 年: %d 个 -> %s" % (len(lb), ", ".join(lb)))
print("   2026 最新一月：close=%.4f ma10=%.4f -> %s"
      % (spy[ms[-1]], sum(spy[x] for x in ms[-10:]) / 10,
         "满仓" if spy[ms[-1]] >= sum(spy[x] for x in ms[-10:]) / 10 else "半仓"))

# 3) 当期曲线：回撤与端点
pts = eq["points"]
peak, mdd, mdd_at = 0, 0, None
for p in pts:
    peak = max(peak, p["s"])
    v = p["s"] / peak - 1
    if v < mdd:
        mdd, mdd_at = v, p["d"]
print("\n[C] 当期曲线 %d 点 %s..%s | ret=%+.6f bench=%+.6f excess=%+.6f | mdd=%.4f at %s"
      % (len(pts), pts[0]["d"], pts[-1]["d"], pts[-1]["s"] - 1, pts[-1]["b"] - 1,
         pts[-1]["s"] - pts[-1]["b"], mdd, mdd_at))
print("   base_date=%s base_s=%.4f base_b=%.4f" % (eq["base_date"], pts[0]["s"], pts[0]["b"]))
print("   exposure=%s | signal.exposure=%s | position last_date=%s"
      % (eq["exposure"], sig["exposure"], eq["position"][0]["last_date"]))

# 4) 理论账户复算（10 万）
amt = 100000.0
tot_mv = 0.0
for p in eq["position"]:
    budget = amt * p["weight"] * eq["exposure"]
    sh_ = budget / p["entry"]
    mv = sh_ * p["last"]
    tot_mv += mv
    print("   %-5s budget=%9.2f shares=%9.4f mv=%10.2f pnl=%+10.2f ret=%+7.2f%%"
          % (p["ticker"], budget, sh_, mv, mv - budget, (mv / budget - 1) * 100))
cash = amt * (1 - eq["exposure"])
print("   TOTAL mv=%.2f cash=%.2f pnl=%+.2f pct=%+.6f" % (tot_mv + cash, cash, tot_mv + cash - amt, (tot_mv + cash) / amt - 1))
print("   stats.strategy=%+.6f  diff=%.6f pp" % (eq["stats"]["strategy"], ((tot_mv + cash) / amt - 1 - eq["stats"]["strategy"]) * 100))

# 5) 今日口径
q = json.load(open("docs/data/quotes.json", encoding="utf-8"))
QS = q["quotes"]
w = sum(x["weight"] for x in sig["official"])
chg = sum(x["weight"] * QS[x["ticker"]]["chg"] for x in sig["official"])
pct = chg / w
print("\n[D] 今日加权 chg=%+.4f%%  w=%.2f  label=今日  (账户另有 exposure=%s 未参与)"
      % (pct * 100, w, eq["exposure"]))
print("   盘中金额(账户页公式) = %.2f | 全额口径 = %.2f"
      % (pct * (tot_mv + cash) / (1 + pct), pct * (tot_mv + cash)))
# 最新价重算的当期收益（含今日）
live = sum(x["weight"] * (QS[x["ticker"]]["p"] / p["entry"]) for x, p in zip(sig["official"], eq["position"]))
print("   equity 用 %s 收盘 -> %+.2f%% | 用实时 p -> %+.2f%% | 差 %+.2f pp"
      % (eq["position"][0]["last_date"], (eq["stats"]["strategy"]) * 100, (live - 1) * 100,
         (live - 1 - eq["stats"]["strategy"]) * 100))
print("   quotes.ts=%s session=%s ts_local=%s" % (q["ts"], q["session"], q["ts_local"]))

# 6) 累计收益的"倍"与百分比
print("\n[E] 全周期 total=%.4f -> 显示 %s ; bench_total=%.4f -> %s"
      % (cv["stats"]["total"], "{:+,.0f}%".format(cv["stats"]["total"] * 100),
         cv["stats"]["bench_total"], "{:+,.0f}%".format(cv["stats"]["bench_total"] * 100)))
print("   curve cagr=%.4f -> %.2f%% | bench_cagr=%.4f | mdd=%.4f" % (cv["stats"]["cagr"], cv["stats"]["cagr"] * 100, cv["stats"]["bench_cagr"], cv["stats"]["mdd"]))
print("   summary total=%.4f (差 %.4f) summary mdd=%.4f vs curve mdd=%.4f"
      % (sh["summary"]["total"], cv["stats"]["total"] - sh["summary"]["total"], sh["summary"]["mdd"], cv["stats"]["mdd"]))
print("   curve start/end=%s..%s months=%d" % (cv["stats"]["start"], cv["stats"]["end"], cv["stats"]["months"]))
print("   2026 是否进入全周期回测: curve end=%s, equity start=%s" % (cv["stats"]["end"], pts[0]["d"]))
