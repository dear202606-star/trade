# -*- coding: utf-8 -*-
"""Step 15: 专业诊断 -- bootstrap 置信区间、市场模型 alpha/beta、收益集中度"""
import os, json
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "output")

rng = np.random.default_rng(42)


def load():
    curves = np.load(os.path.join(OUT, "curves.npy"), allow_pickle=True).item()
    months = json.load(open(os.path.join(OUT, "months.json")))["months"]
    cut = [i for i, x in enumerate(months) if x <= "2025-12"]
    return {k: np.array(v)[cut] for k, v in curves.items()}, months[:len(cut)]


def cagr(r):
    eq = np.cumprod(1 + r)
    return eq[-1] ** (12 / len(r)) - 1


def block_bootstrap(r, n_boot=4000, block=6):
    n = len(r)
    out = []
    for _ in range(n_boot):
        idx = []
        while len(idx) < n:
            s = rng.integers(0, n)
            idx.extend((s + np.arange(block)) % n)
        out.append(cagr(r[np.array(idx[:n])]))
    return np.percentile(out, [2.5, 50, 97.5])


def ols(y, X):
    X1 = np.column_stack([np.ones(len(X)), X])
    b, *_ = np.linalg.lstsq(X1, y, rcond=None)
    resid = y - X1 @ b
    dof = len(y) - X1.shape[1]
    s2 = resid @ resid / dof
    cov = s2 * np.linalg.inv(X1.T @ X1)
    se = np.sqrt(np.diag(cov))
    ss_tot = ((y - y.mean()) ** 2).sum()
    r2 = 1 - (resid @ resid) / ss_tot
    return b, se, r2


def main():
    C, months = load()
    mkt = C["SPY"]
    rows = []
    for k, r in C.items():
        lo, med, hi = block_bootstrap(r)
        b, se, r2 = ols(r, mkt)
        rows.append({
            "模型": k, "年化": cagr(r),
            "bootstrap_2.5": lo, "bootstrap_97.5": hi, "过0概率": float((lo <= 0)),
            "alpha年化": (1 + b[0]) ** 12 - 1, "alpha_t": b[0] / se[0],
            "beta": b[1], "beta_t": b[1] / se[1], "R2": r2,
        })
    df = pd.DataFrame(rows).sort_values("年化", ascending=False)
    df.to_csv(os.path.join(OUT, "diagnostics.csv"), index=False, encoding="utf-8-sig")
    print("=== Bootstrap 95% CI + 市场模型归因（2009-2025 月度）===")
    print((df * 1).round(3).to_string(index=False))

    # 集中度：去掉最好的 1/2/3 年
    print("\n=== 收益集中度（去掉最好的年份后的年化）===")
    annual = pd.read_csv(os.path.join(OUT, "compare_annual.csv"))
    out = []
    for k in ["M1 只买第1名", "M4 第1+第2", "M5 第1+第2+第3", "M6 前10名等权"]:
        s = annual.set_index("year")[k].dropna()
        full = (1 + s).prod() ** (1 / len(s)) - 1
        drop1 = (1 + s.drop(s.idxmax())).prod() ** (1 / (len(s) - 1)) - 1
        drop2 = (1 + s.drop(s.nlargest(2).index)).prod() ** (1 / (len(s) - 2)) - 1
        drop3 = (1 + s.drop(s.nlargest(3).index)).prod() ** (1 / (len(s) - 3)) - 1
        top3 = s.nlargest(3)
        share = np.prod(1 + top3) ** 1
        out.append({"模型": k, "全样本": full, "去掉最好1年": drop1,
                    "去掉最好2年": drop2, "去掉最好3年": drop3,
                    "最好3年": ",".join(str(int(i)) for i in top3.index)})
    c = pd.DataFrame(out)
    c.to_csv(os.path.join(OUT, "concentration.csv"), index=False, encoding="utf-8-sig")
    print(c.round(3).to_string(index=False))

    # 相对标普的超额是否有显著性
    print("\n=== 超额收益 t 检验（月度超额，Newey-West 近似用普通 t）===")
    for k in ["M1 只买第1名", "M4 第1+第2", "M5 第1+第2+第3", "M6 前10名等权"]:
        d = C[k] - mkt
        t = d.mean() / (d.std(ddof=1) / np.sqrt(len(d)))
        print(f"  {k:16s} 月均超额 {d.mean()*100:5.2f}%  t={t:5.2f}  年化超额 {(1+d.mean())**12-1:6.1%}")


if __name__ == "__main__":
    main()
