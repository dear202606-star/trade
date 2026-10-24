# -*- coding: utf-8 -*-
"""Step 16: 严谨版排名策略回测引擎
- PIT 成分股 / 12-1 动量 / N=1..10 / 年频与季频 / 成本 / 波动率目标 / 趋势过滤
- 输出主表、稳健性网格、统计检验
"""
import os, json, math
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "output")

PX_MIN, PX_MAX = 5.0, 5000.0
LIQ_MIN = 20e6
MAX_JUMP, MAX_FACTOR = 8.0, 2000.0
COST = 0.002          # 单边 0.2%
START_FULL = "2009-01"
END_FULL = "2025-12"


def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def norm_ppf(p):
    # Acklam 近似
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549728961997582e+00, -3.003371367793025e+00, -1.479586844945042e-01]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    pl, ph = 0.02425, 1 - 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > ph:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


class Engine:
    def __init__(self):
        p = pd.read_pickle(os.path.join(DATA, "panel2.pkl")).sort_values(["ticker", "ym"])
        p = p.copy()
        p["factor"] = p["adj"] / p["raw"]
        g = p.groupby("ticker")
        fmax, fmin = g["factor"].max(), g["factor"].min()
        p["ar"] = p["adj"] / g["adj"].shift(1)
        jmax, jmin = p.groupby("ticker")["ar"].max(), p.groupby("ticker")["ar"].min()
        pmax = g["raw"].max()
        bad = set(fmax[(fmax > MAX_FACTOR) | (fmin < 1 / MAX_FACTOR)].index)
        bad |= set(jmax[jmax > MAX_JUMP].index) | set(jmin[jmin < 1 / MAX_JUMP].index)
        bad |= set(pmax[pmax > PX_MAX].index)
        p = p[~p.ticker.isin(bad)]
        self.adj = p.pivot(index="ym", columns="ticker", values="adj").sort_index()
        self.raw = p.pivot(index="ym", columns="ticker", values="raw").sort_index()
        self.dv = p.pivot(index="ym", columns="ticker", values="dv").sort_index()
        self.mret = self.adj.pct_change()
        self.months = list(self.adj.index)
        self.mpos = {m: i for i, m in enumerate(self.months)}

        m = pd.read_csv(os.path.join(DATA, "sp500_ticker_start_end.csv"))
        m["start_date"] = m["start_date"].astype(str)
        m["end_date"] = m["end_date"].fillna("2099-12-31").astype(str)
        self.mem = m

        spy = json.load(open(os.path.join(DATA, "sina2", "SPY.json"), encoding="utf-8"))
        self.spy = {b[0]: b[1] for b in spy["bars"]}

    # ---------- helpers ----------
    def members(self, ym, mode="pit"):
        d = ym + "-28"
        if mode == "pit":
            return set(self.mem[(self.mem.start_date <= d) & (self.mem.end_date >= d)]["ticker"])
        y = ym[:4]
        return set(self.mem[(self.mem.start_date <= f"{y}-12-31") &
                            (self.mem.end_date >= f"{y}-01-01")]["ticker"])

    def shift_ym(self, ym, k):
        i = self.mpos.get(ym)
        if i is None or i - k < 0:
            return None
        return self.months[i - k]

    def score(self, ym, lookback=12, skip=0):
        """lookback 个月动量，skip 表示跳过最近 skip 个月"""
        end = self.shift_ym(ym, skip)
        start = self.shift_ym(ym, lookback + skip)
        if end is None or start is None:
            return None
        return self.adj.loc[end] / self.adj.loc[start] - 1

    def eligible(self, ym, mode="pit"):
        mem = self.members(ym, mode)
        px = self.raw.loc[ym]
        i = self.mpos[ym]
        dv = self.dv.iloc[i - 11:i + 1].mean() if i >= 11 else None
        ok = px[px.between(PX_MIN, PX_MAX)].index
        if dv is not None:
            ok = ok.intersection(dv[dv >= LIQ_MIN].index)
        return ok.intersection(pd.Index(sorted(mem)))

    def rebalance_months(self, freq):
        if freq == "A":
            return [m for m in self.months if m.endswith("-12") and "2008-12" <= m <= "2025-12"]
        return [m for m in self.months if m[5:] in ("03", "06", "09", "12")
                and "2009-03" <= m <= "2025-12"]

    # ---------- backtest ----------
    def run(self, N=2, lookback=12, skip=0, freq="A", weight="eq", mode="pit",
            cost=0.0, inv_vol=False, trend=0.0, vt=None, topn_exact=True):
        reb = self.rebalance_months(freq)
        ret_by_month = {}
        holdings = {}
        for i, r in enumerate(reb):
            s = self.score(r, lookback, skip)
            if s is None:
                continue
            elig = self.eligible(r, mode)
            s = s[s.index.isin(elig)].dropna().sort_values(ascending=False)
            if s.empty:
                continue
            picks = list(s.head(N).index)
            holdings[r] = picks
            # 权重
            nxt = self.months[self.mpos[r] + 1]
            j = self.months.index(nxt)
            k = self.months.index(reb[i + 1]) + 1 if i + 1 < len(reb) else len(self.months)
            k = min(k, len(self.months))
            seg = self.months[j:k]
            if not seg:
                continue
            if inv_vol:
                vol = self.mret.loc[:r, picks].iloc[-12:].std()
                w = (1 / vol.replace(0, np.nan)).fillna(0)
                w = w / w.sum() if w.sum() > 0 else pd.Series(1 / len(picks), index=picks)
            else:
                w = pd.Series(1 / len(picks), index=picks)
            for m in seg:
                rr = self.mret.loc[m, picks]
                rv = (rr.fillna(0) * w).sum()
                ret_by_month[m] = rv
        months = [m for m in self.months if m in ret_by_month]
        ret = pd.Series([ret_by_month[m] for m in months], index=months)

        # 交易成本（按换手）
        if cost > 0:
            prev = None
            for i, r in enumerate(reb):
                if r not in holdings:
                    continue
                cur = set(holdings[r])
                turn = 1.0 if prev is None else len(cur - prev) / max(len(cur), 1)
                nxt = self.months[self.mpos[r] + 1]
                if nxt in ret.index:
                    ret.loc[nxt] -= cost * turn * 2
                prev = cur

        # 趋势过滤（SPY 10 月均线，用截至上月的数据决定本月仓位）
        if trend:
            expo = []
            for m in months:
                i = self.mpos[m]
                if i < 11:
                    expo.append(1.0)
                    continue
                px = [self.spy.get(x) for x in self.months[i - 10:i]]
                px = [x for x in px if x]
                ma = np.mean(px) if len(px) == 10 else None
                last = self.spy.get(self.months[i - 1])
                expo.append(trend if (ma and last and last < ma) else 1.0)
            ret = ret * pd.Series(expo, index=months)

        # 波动率目标
        if vt:
            vol12 = ret.rolling(12).std().shift(1) * math.sqrt(12)
            lev = (vt / vol12).clip(0, 1.5).fillna(1.0)
            ret = ret * lev
        return ret[ret.index >= START_FULL]


def stats(r, name=""):
    r = pd.Series(r).dropna()
    if len(r) < 24:
        return None
    eq = (1 + r).cumprod()
    yrs = len(r) / 12
    peak = eq.cummax()
    mdd = float((eq / peak - 1).min())
    ann = eq.iloc[-1] ** (1 / yrs) - 1
    vol = r.std(ddof=1) * math.sqrt(12)
    return {"策略": name, "累计": eq.iloc[-1], "年化": ann, "波动": vol, "回撤": mdd,
            "夏普": ann / vol if vol else np.nan, "Calmar": ann / abs(mdd) if mdd else np.nan,
            "月数": len(r)}


def alpha_beta(r, mkt):
    r = pd.Series(r).dropna()
    mkt = mkt.reindex(r.index).dropna()
    r = r.reindex(mkt.index)
    X = np.column_stack([np.ones(len(mkt)), mkt.values])
    b, *_ = np.linalg.lstsq(X, r.values, rcond=None)
    res = r.values - X @ b
    s2 = res @ res / (len(r) - 2)
    se = np.sqrt(np.diag(s2 * np.linalg.inv(X.T @ X)))
    return b[0], b[0] / se[0], b[1], 1 - (res @ res) / ((r - r.mean()) ** 2).sum()


def block_boot(r, n=3000, block=6, seed=7):
    rng = np.random.default_rng(seed)
    r = np.asarray(pd.Series(r).dropna())
    N = len(r)
    out = []
    for _ in range(n):
        idx = []
        while len(idx) < N:
            s = rng.integers(0, N)
            idx.extend((s + np.arange(block)) % N)
        x = r[np.array(idx[:N])]
        out.append(np.prod(1 + x) ** (12 / N) - 1)
    return np.percentile(out, [2.5, 50, 97.5])


def main():
    E = Engine()
    mkt = E.mret["SPY"] if "SPY" in E.mret.columns else None
    spy_ret = pd.Series({m: E.spy[m] / E.spy[E.months[E.mpos[m] - 1]] - 1
                         for m in E.months
                         if E.mpos[m] > 0 and m in E.spy and E.months[E.mpos[m] - 1] in E.spy})

    res = {}
    # ---- 主实验：N 网格（PIT, 年频, 等权, 含成本）----
    rows = []
    for skip, tag in [(0, "12-0"), (1, "12-1")]:
        for N in (1, 2, 3, 5, 10):
            r = E.run(N=N, skip=skip, cost=COST)
            res[f"{tag}_N{N}"] = r
            st = stats(r, f"{tag} Top{N}")
            if st:
                a, t, b, r2 = alpha_beta(r, spy_ret)
                lo, med, hi = block_boot(r)
                st.update({"alpha": (1 + a) ** 12 - 1, "alpha_t": t, "beta": b, "R2": r2,
                           "CI_lo": lo, "CI_hi": hi})
                rows.append(st)
    main_tb = pd.DataFrame(rows)
    main_tb.to_csv(os.path.join(OUT, "m_main.csv"), index=False, encoding="utf-8-sig")
    print("== 主表 ==")
    print(main_tb.round(3).to_string(index=False))

    # ---- 基准 ----
    spy_st = stats(spy_ret[spy_ret.index >= START_FULL], "SPY")
    print("\nSPY:", {k: round(v, 3) for k, v in spy_st.items() if isinstance(v, float)})

    # ---- 稳健性网格 ----
    grid = []
    for mode in ("pit", "any"):
        for freq in ("A", "Q"):
            for skip in (0, 1):
                for N in (1, 2, 3, 5, 10):
                    r = E.run(N=N, skip=skip, freq=freq, mode=mode, cost=COST)
                    st = stats(r)
                    if st:
                        grid.append({"成员口径": mode, "调仓": freq, "动量": "12-0" if skip == 0 else "12-1",
                                     "N": N, "年化": st["年化"], "回撤": st["回撤"], "夏普": st["夏普"]})
    gdf = pd.DataFrame(grid)
    gdf.to_csv(os.path.join(OUT, "m_grid.csv"), index=False, encoding="utf-8-sig")
    print("\n== 稳健性网格统计 ==")
    print(gdf.groupby(["成员口径", "调仓"])["年化"].agg(["mean", "min", "max"]).round(3).to_string())

    # ---- 风险改进 ----
    imp = []
    base = E.run(N=2, skip=1, cost=COST)
    for name, kw in [("基准(Top2)", {}),
                     ("+反波动权重", {"inv_vol": True}),
                     ("+趋势过滤(跌破MA半年=50%仓)", {"trend": 0.5}),
                     ("+波动率目标15%", {"vt": 0.15}),
                     ("+三者叠加", {"inv_vol": True, "trend": 0.5, "vt": 0.15})]:
        r = E.run(N=2, skip=1, cost=COST, **kw)
        st = stats(r, name)
        if st:
            imp.append(st)
    idf = pd.DataFrame(imp)
    idf.to_csv(os.path.join(OUT, "m_improve.csv"), index=False, encoding="utf-8-sig")
    print("\n== 风险改进（Top2, 12-1）==")
    print(idf.round(3).to_string(index=False))

    # ---- 成本敏感性 ----
    costtb = []
    for c in (0.0, 0.001, 0.002, 0.005):
        for N in (1, 2, 3):
            r = E.run(N=N, skip=1, cost=c)
            st = stats(r)
            if st:
                costtb.append({"单边成本": c, "N": N, "年化": st["年化"], "回撤": st["回撤"]})
    pd.DataFrame(costtb).to_csv(os.path.join(OUT, "m_cost.csv"), index=False, encoding="utf-8-sig")
    print("\n== 成本敏感性 ==")
    print(pd.DataFrame(costtb).pivot(index="单边成本", columns="N", values="年化").round(3).to_string())

    # ---- DSR ----
    srs = [g["夏普"] for g in grid if np.isfinite(g["夏普"])]
    sr = main_tb.loc[main_tb["策略"] == "12-1 Top2", "夏普"].iloc[0]
    sr_m = sr / math.sqrt(12)
    T = 204
    g3 = float(pd.Series(base).skew())
    g4 = float(pd.Series(base).kurtosis() + 3)
    v = np.var(srs) / 12
    K = len(srs)
    sr0 = math.sqrt(v) * ((1 - 0.5772) * norm_ppf(1 - 1 / K) + 0.5772 * norm_ppf(1 - 1 / (K * math.e)))
    dsr = norm_cdf((sr_m - sr0) * math.sqrt(T - 1) /
                   math.sqrt(1 - g3 * sr_m + (g4 - 1) / 4 * sr_m ** 2))
    print(f"\nDSR: SR={sr:.3f} SR0={sr0*math.sqrt(12):.3f} trials={K} DSR={dsr:.3f}")

    json.dump({"spy": spy_st, "dsr": dsr, "sr": sr, "sr0": sr0 * math.sqrt(12),
               "trials": K, "n_months": int(len(base))},
              open(os.path.join(OUT, "m_meta.json"), "w"), indent=2)
    for k, v_ in res.items():
        if k in ("12-1_N1", "12-1_N2", "12-1_N3", "12-0_N2"):
            pd.Series(v_).to_csv(os.path.join(OUT, f"m_ret_{k}.csv"))


if __name__ == "__main__":
    main()
