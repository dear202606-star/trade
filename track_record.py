# -*- coding: utf-8 -*-
"""track_record.py — 实盘跟踪账本：记录每期正式持仓，计算已实现收益与回测偏差"""
import json
import os

PATH = "output/track_record.json"
# 回测基准：前3名+趋势过滤，17 年年化 29.4%（见「策略模型研究与行动步骤」）
BACKTEST_CAGR = 0.294


def load():
    if os.path.exists(PATH):
        try:
            return json.load(open(PATH, encoding="utf-8"))
        except Exception:
            pass
    return {"start": None, "equity": 1.0, "records": []}


def save(tr):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(tr, f, ensure_ascii=False, indent=2)


def update(official, prices, spy_now, latest, today):
    """official: [(row, weight), ...]；prices: {ticker: 最新收盘}"""
    tr = load()
    if tr.get("start") is None:
        tr["start"] = latest

    recs = tr["records"]
    # ---- 结算上一期 ----
    if recs and recs[-1].get("ret") is None and recs[-1]["month"] != latest:
        last = recs[-1]
        vals, ws = [], []
        for p in last["picks"]:
            t = p["ticker"]
            if t in prices and p.get("px") and p["px"] > 0:
                vals.append(prices[t] / p["px"] - 1)
                ws.append(p["weight"])
        if ws and sum(ws) > 0:
            port = sum(v * w for v, w in zip(vals, ws)) / sum(ws)
            last["ret"] = round(port * last["exposure"], 6)
            last["spy_ret"] = round(spy_now / last["spy_px"] - 1, 6) if last.get("spy_px") else None

    # ---- 记录本期 ----
    if not recs or recs[-1]["month"] != latest:
        recs.append({
            "date": today, "month": latest,
            "picks": [{"ticker": p["ticker"], "weight": round(float(w), 4),
                       "px": round(float(prices.get(p["ticker"], 0)), 4)} for p, w in official],
            "exposure": 1.0, "spy_px": round(float(spy_now), 4),
            "ret": None, "spy_ret": None,
        })
    else:                                   # 同月重跑：刷新价格与权重
        recs[-1].update({
            "date": today,
            "picks": [{"ticker": p["ticker"], "weight": round(float(w), 4),
                       "px": round(float(prices.get(p["ticker"], 0)), 4)} for p, w in official],
        })

    # ---- 累计 ----
    eq, spy_eq, n = 1.0, 1.0, 0
    for r in recs:
        if r.get("ret") is not None:
            eq *= 1 + r["ret"]
            if r.get("spy_ret") is not None:
                spy_eq *= 1 + r["spy_ret"]
            n += 1
    tr["equity"] = round(eq, 6)
    tr["spy_equity"] = round(spy_eq, 6)
    tr["months"] = n

    since = tr.get("start") or latest
    exp = (1 + BACKTEST_CAGR) ** (max(n, 1) / 12) - 1
    tr["summary"] = {
        "start": since, "months": n,
        "strategy": round(eq - 1, 6), "spy": round(spy_eq - 1, 6),
        "expected": round(exp, 6),
        "excess_vs_spy": round((eq - 1) - (spy_eq - 1), 6),
        "gap_vs_backtest": round((eq - 1) - exp, 6),
    }
    save(tr)
    return tr


if __name__ == "__main__":
    print(json.dumps(load().get("summary", {}), ensure_ascii=False, indent=2))
