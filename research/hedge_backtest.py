# -*- coding: utf-8 -*-
"""hedge_backtest.py — 对冲策略回测：做多前3名组合 vs 做空标普500

问题：三分策略（前3名）本身带 ~1.1 的 beta，涨的时候靠大盘。
      如果做空 SPY 对冲掉 beta，剩下的 alpha 还在吗？净值曲线会更稳吗？

做法（严格对齐年度策略，不做月度再平衡）：
  1. 每年 12 月末按 12 个月动量选前 3 名（PIT 成分股 + 行业上限）
  2. 全年持有，按月计算组合收益
  3. 同时取 SPY 同月收益
  4. 三种口径：
       纯多头   port
       多空对冲 port − SPY − 对冲成本
       半对冲   port − 0.5 × SPY − 0.5 × 成本
  5. 对冲成本：做空 SPY 需支付分红 ≈ 1.3%/年，借券费 ≈ 0.3%/年，合计 1.6%/年

产物：docs/data/hedge.json（月度序列 + 统计）
"""
import json
import os
import sys

import pandas as pd

import datafeed

OUT = os.path.join("docs", "data", "hedge.json")
HEDGE_COST_Y = 0.016          # 年化：SPY 分红 1.3% + 借券 0.3%

FINAL_N = 3
MIN_PRICE = 5.0
SECTOR_CAP = 0.60


def month_index(ym):
    return int(ym[:4]) * 12 + int(ym[5:7])


def ym_add(ym, k):
    i = month_index(ym) + k
    y, m = divmod(i - 1, 12)
    return f"{y:04d}-{m+1:02d}"


def ret_12m(bars, ym):
    base = ym_add(ym, -12)
    seq = [ym_add(ym, -12 + i) for i in range(13)]
    if any(s not in bars for s in seq):
        return None
    adj0, adj1 = bars[base][0], bars[ym][0]
    if not adj0 or adj0 <= 0 or not adj1:
        return None
    prev = adj0
    for s in seq:
        cur = bars[s][0]
        if prev > 0 and (cur / prev > 3 or cur / prev < 1 / 3):
            return None
        prev = cur
    r = adj1 / adj0 - 1
    return r if -0.99 < r < 10 else None


def main():
    m = datafeed.load_members()
    if m is None:
        print("无法获取成分表", file=sys.stderr)
        return 1
    m["start_date"] = m["start_date"].astype(str)
    m["end_date"] = m["end_date"].fillna("2099-12-31").astype(str)
    sector, _names = datafeed.load_sector()

    hist = {}
    if os.path.exists("output/signal_history.json"):
        pass

    # 预热
    tickers = sorted(set(m["ticker"].dropna().astype(str)))
    print(f"预取 {len(tickers)} 只…", flush=True)
    datafeed.prefetch(tickers + ["SPY"], workers=10)

    spy = datafeed.load_bars("SPY")
    if not spy:
        print("缺 SPY", file=sys.stderr)
        return 1

    rows = []
    for y in range(2010, 2027):
        rank_ym = f"{y-1}-12"
        d = f"{y}-01-01"
        members = set(m[(m.start_date <= d) & (m.end_date >= d)]["ticker"])
        cand = []
        for t in members:
            bars = datafeed.load_bars(t)
            if not bars or rank_ym not in bars:
                continue
            if bars[rank_ym][1] < MIN_PRICE:
                continue
            r = ret_12m(bars, rank_ym)
            if r is None:
                continue
            cand.append((t, r))
        cand.sort(key=lambda x: -x[1])

        picks, sec_cnt = [], {}
        for t, r in cand:
            if len(picks) >= FINAL_N:
                break
            s = sector.get(t, "Unknown")
            if sec_cnt.get(s, 0) >= 2:
                continue
            picks.append((t, r, s))
            sec_cnt[s] = sec_cnt.get(s, 0) + 1
        if len(picks) < FINAL_N:
            continue

        # 权重：同行业 2 只时该行业 60%，否则等权
        secs = [p[2] for p in picks]
        dup = [s for s, c in pd.Series(secs).value_counts().items() if c >= 2]
        if dup and len(picks) > 2:
            cap, rest = SECTOR_CAP / 2, 1 - SECTOR_CAP
            w = [cap if p[2] in dup else rest / max(1, len(picks) - 2) for p in picks]
            tot = sum(w)
            w = [x / tot for x in w]
        else:
            w = [1 / len(picks)] * len(picks)

        # 逐月收益
        for k in range(1, 13):
            ym = f"{y}-{k:02d}"
            prev = ym_add(ym, -1)
            if ym not in spy or prev not in spy:
                continue
            pr, ok = 0.0, True
            for (t, _r, _s), wi in zip(picks, w):
                bars = datafeed.load_bars(t)
                if not bars or ym not in bars or prev not in bars:
                    ok = False
                    break
                a0, a1 = bars[prev][0], bars[ym][0]
                if not a0 or a0 <= 0:
                    ok = False
                    break
                pr += wi * (a1 / a0 - 1)
            if not ok:
                continue
            sr = spy[ym][0] / spy[prev][0] - 1
            rows.append({"ym": ym, "port": round(pr, 6), "spy": round(sr, 6),
                         "year": y, "hold": [p[0] for p in picks]})

    if not rows:
        print("无有效月份", file=sys.stderr)
        return 1

    df = pd.DataFrame(rows).drop_duplicates("ym").sort_values("ym").reset_index(drop=True)
    c = HEDGE_COST_Y / 12
    beta_est = df["port"].cov(df["spy"]) / df["spy"].var() if df["spy"].var() else 1.0
    df["ls"] = df["port"] - df["spy"] - c                 # 1:1 美元对冲
    df["half"] = df["port"] - 0.5 * df["spy"] - 0.5 * c    # 半对冲
    df["lsb"] = df["port"] - beta_est * (df["spy"] + c)   # 按 beta 对冲
    # 波动率目标：把多头缩放到目标年化波动（含加杠杆成本）
    tgt = 0.20
    vol_m = df["port"].std()
    k = min(2.0, tgt / (12 ** 0.5) / vol_m) if vol_m > 0 else 1.0
    lev_cost = 0.005 * max(0, k - 1) / 12
    df["vt"] = k * df["port"] - lev_cost

    def stats(col):
        s = df[col]
        eq = (1 + s).cumprod()
        n = len(s)
        yrs = n / 12
        cagr = eq.iloc[-1] ** (1 / yrs) - 1 if yrs > 0 else 0
        vol = s.std() * (12 ** 0.5)
        sharpe = (s.mean() * 12) / vol if vol > 0 else 0
        peak = eq.cummax()
        dd = (eq / peak - 1).min()
        return {"cagr": round(cagr, 4), "vol": round(vol, 4), "sharpe": round(sharpe, 3),
                "mdd": round(dd, 4), "total": round(eq.iloc[-1] - 1, 4),
                "win": round((s > 0).mean(), 4), "worst": round(s.min(), 4),
                "best": round(s.max(), 4), "n": n}

    st = {"long": stats("port"), "bench": stats("spy"),
          "ls": stats("ls"), "half": stats("half"),
          "lsb": stats("lsb"), "vt": stats("vt")}
    st["beta_used"] = round(beta_est, 3)
    st["vt_k"] = round(k, 3)

    # beta / alpha（月度回归）
    cov = df["port"].cov(df["spy"])
    var = df["spy"].var()
    beta = cov / var if var else 0
    alpha_m = df["port"].mean() - beta * df["spy"].mean()
    corr = df["port"].corr(df["spy"])

    ls_cov = df["ls"].cov(df["spy"])
    st["beta"] = round(beta, 3)
    st["alpha_y"] = round(alpha_m * 12, 4)
    st["corr"] = round(corr, 3)
    st["ls_beta"] = round(ls_cov / var if var else 0, 3)
    st["months"] = len(df)
    st["period"] = f"{df['ym'].iloc[0]} ~ {df['ym'].iloc[-1]}"
    st["hedge_cost_y"] = HEDGE_COST_Y

    # 逐年对比
    by_year = []
    for y, g in df.groupby("year"):
        by_year.append({
            "year": int(y),
            "long": round((1 + g["port"]).prod() - 1, 4),
            "spy": round((1 + g["spy"]).prod() - 1, 4),
            "ls": round((1 + g["ls"]).prod() - 1, 4),
            "lsb": round((1 + g["lsb"]).prod() - 1, 4),
        })

    out = {"stats": st, "years": by_year,
           "monthly": [{"ym": r["ym"], "p": r["port"], "s": r["spy"],
                        "l": round(r["ls"], 6), "h": round(r["half"], 6)}
                       for _, r in df.iterrows()],
           "updated": pd.Timestamp.today().strftime("%Y-%m-%d")}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    print(f"\n区间 {st['period']} 共 {st['months']} 个月")
    print(f"{'':10s}{'年化':>9s}{'波动':>9s}{'夏普':>8s}{'最大回撤':>10s}{'胜率':>8s}")
    for k, label in (("long", "纯多头"), ("bench", "标普500"), ("half", "半对冲"),
                     ("ls", "1:1对冲"), ("lsb", "β对冲"), ("vt", "波动率目标")):
        s = st[k]
        print(f"{label:10s}{s['cagr']*100:8.1f}%{s['vol']*100:8.1f}%{s['sharpe']:8.2f}"
              f"{s['mdd']*100:9.1f}%{s['win']*100:7.1f}%")
    print(f"\n纯多头 beta={st['beta']} alpha={st['alpha_y']*100:.1f}%/年 相关性={st['corr']}")
    print(f"对冲后 beta={st['ls_beta']}（应接近 0）")
    print(f"已写入 {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
