# -*- coding: utf-8 -*-
"""对抗性审计 · 复算"""
import json, os, sys, calendar, pickle
import pandas as pd

os.chdir(r"C:\Users\Administrator\Desktop\量化")
MIN_PRICE, MIN_DV, FINAL_N, SEC_CAP = 5.0, 2e7, 3, 0.60

P = pickle.load(open("data/panel.pkl", "rb"))
P = P[P["ym"].str.match(r"^\d{4}-\d{2}$")]
G = {tk: df.set_index("ym").sort_index() for tk, df in P.groupby("ticker")}
print("tickers:", len(G))

mem = pd.read_csv("data/sp500_ticker_start_end.csv", dtype=str)
mem["end_date"] = mem["end_date"].fillna("2099-12-31")

def members_at(ym):
    y, m = int(ym[:4]), int(ym[5:7])
    day = "%04d-%02d-%02d" % (y, m, calendar.monthrange(y, m)[1])
    return set(mem[(mem.start_date <= day) & (mem.end_date >= day)].ticker)

def shift(ym, k):
    y, m = int(ym[:4]), int(ym[5:7]) + k
    while m <= 0: m += 12; y -= 1
    while m > 12: m -= 12; y += 1
    return "%04d-%02d" % (y, m)

def rank_at(ym, universe, topn=10):
    base = shift(ym, -12)
    months = [shift(base, i) for i in range(13)]
    rows = []
    for tk in universe:
        df = G.get(tk)
        if df is None: continue
        if not all(m in df.index for m in months): continue
        w = df.loc[months]
        if len(w) != 13: continue
        ratio = (w["adj"] / w["adj"].shift(1)).dropna()
        if len(ratio) < 12 or ratio.max() > 8 or ratio.min() < 1/8: continue
        px = float(w["raw"].iloc[-1])
        if px < MIN_PRICE: continue
        seg = w.iloc[:-1]
        dv = float((seg["raw"] * seg["vol"]).mean())
        if dv < MIN_DV: continue
        rows.append({"ticker": tk, "ret12": float(w["adj"].iloc[-1] / w["adj"].iloc[0] - 1), "px": px})
    df = pd.DataFrame(rows).sort_values("ret12", ascending=False).reset_index(drop=True)
    df["rank"] = df.index + 1
    return df.head(topn), len(df)

u_pit = sorted(members_at("2025-12"))
u_now = sorted(members_at("2026-10"))
print("universe 2025-12: %d | 2026-10: %d | LITE in 2025-12: %s | HOOD: %s | WDC: %s"
      % (len(u_pit), len(u_now), "LITE" in u_pit, "HOOD" in u_pit, "WDC" in u_pit))

print("\n=== rank @ 2025-12 · POINT-IN-TIME universe (rule-compliant) ===")
d1, n1 = rank_at("2025-12", u_pit)
print(d1.to_string(index=False), "\n pool n =", n1)

print("\n=== rank @ 2025-12 · CURRENT (2026-10) universe ===")
d2, n2 = rank_at("2025-12", u_now)
print(d2.to_string(index=False), "\n pool n =", n2)

sig = json.load(open("docs/data/signal.json", encoding="utf-8"))
print("\nofficial published:", [(p["ticker"], p["rank"], round(p["ret12"], 4), p["weight"]) for p in sig["official"]])
print("bench published   :", [(p["ticker"], p["rank"], round(p["ret12"], 4)) for p in sig["bench"]])

print("\n=== rank @ 2026-10 · CURRENT universe (reproduce bench) ===")
d3, n3 = rank_at("2026-10", u_now)
print(d3.to_string(index=False), "\n pool n =", n3)

# single-name ret12 at 2025-12
for tk in ("LITE", "WDC", "HOOD"):
    df = G[tk]
    b = shift("2025-12", -12)
    if b in df.index and "2025-12" in df.index:
        print("  %s ret12@2025-12 = %+.4f" % (tk, df.loc["2025-12", "adj"] / df.loc[b, "adj"] - 1))

# equity / stats
eq = json.load(open("docs/data/equity.json", encoding="utf-8"))
pos = eq["position"]
s_re = sum(p["weight"] * (p["last"] / p["entry"]) for p in pos)
print("\n[stats] published=%.6f recompute=%.6f diff=%.6f pp" %
      (eq["stats"]["strategy"], s_re - 1, (s_re - 1 - eq["stats"]["strategy"]) * 100))
for p in pos:
    print("   %-5s w=%.2f entry=%.2f last=%.2f ret=%+7.2f%% contrib=%+6.2f pp"
          % (p["ticker"], p["weight"], p["entry"], p["last"],
             (p["last"]/p["entry"]-1)*100, p["weight"]*(p["last"]/p["entry"]-1)*100))

q = json.load(open("docs/data/quotes.json", encoding="utf-8"))
QS = q["quotes"]
wsum = sum(p["weight"] for p in sig["official"])
chg = sum(p["weight"] * QS[p["ticker"]]["chg"] for p in sig["official"])
print("\n[today] weighted chg = %+.4f%% (w=%.2f) ts=%s session=%s ts_local=%s"
      % (chg/wsum*100, wsum, q["ts"], q["session"], q["ts_local"]))
for p in sig["official"]:
    x = QS[p["ticker"]]
    print("   %-5s p=%.2f pc=%.2f chg=%+.4f%% recompute=%+.4f%%"
          % (p["ticker"], x["p"], x["pc"], x["chg"]*100, (x["p"]/x["pc"]-1)*100))
s_live = sum(p["weight"] * (QS[p["ticker"]]["p"] / p["entry"]) for p in pos)
print("   equity_last_s=%.6f  live_s(from quote p)=%.6f  gap=%+.2f pp"
      % (eq["points"][-1]["s"], s_live, (s_live - eq["points"][-1]["s"])*100))

# SPY 10M MA
S = G["SPY"]
print("\n[SPY 10M MA] close vs mean(last10 monthly closes, incl. current):")
ms = [m for m in S.index if "2024-06" <= m <= "2026-10"]
for i, m in enumerate(ms):
    win = ms[max(0, i-9):i+1]
    if len(win) < 10: continue
    ma = S.loc[win, "raw"].mean(); px = S.loc[m, "raw"]
    print("   %s close=%8.2f ma10=%8.2f %s" % (m, px, ma, "满仓" if px >= ma else "半仓 <<<"))

# curve vs strategy_history
cv = json.load(open("docs/data/curve.json", encoding="utf-8"))
sh = json.load(open("docs/data/strategy_history.json", encoding="utf-8"))
print("\n[curve] total=%.4f cagr=%.4f mdd=%.4f months=%d | [history.summary] total=%.4f mdd=%.4f worst_intra=%.4f"
      % (cv["stats"]["total"], cv["stats"]["cagr"], cv["stats"]["mdd"], cv["stats"]["months"],
         sh["summary"]["total"], sh["summary"]["mdd"], sh["summary"]["worst_intra"]))
prod = 1.0
for y in sh["years"]: prod *= (1 + y["ret"])
print("   prod(1+year.ret)=%.4f  curve_last_s=%.4f" % (prod, cv["points"][-1]["s"]))

print("\n[year ret vs sum(w*ret_year) = full-exposure test]")
for y in sh["years"]:
    s = sum(p["weight"] * (p["ret_year"] or 0) for p in y["holdings"])
    print("   %d ret=%+.4f sum(w*r)=%+.4f diff=%+.5f  %s"
          % (y["year"], y["ret"], s, y["ret"]-s, ",".join(p["ticker"] for p in y["holdings"])))
