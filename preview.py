# -*- coding: utf-8 -*-
"""2027 年持仓预览：年初至今涨幅前 3 的标普 500 成分股（实时）。

口径与年度选股一致：
  · 股票池：标普 500 成分股（当前名单）
  · 排序：从上年 12 月末收盘 → 最新价（复权）的涨幅
  · 约束：股价 ≥ $5；同行业最多 2 只、该行业合计 ≤60%
  · 权重：与正式规则同（2 只同行业时该行业 60%，其余 40%）

仅用于「预先知道明年大概是哪三只」，不是换仓依据。
"""
import datetime as dt
import json
import os
import math

import datafeed
import nominal_prices
import live_quotes
import db_ref

MIN_PRICE = 5.0
TOPN = 3
OUT = os.path.join("docs", "data", "preview.json")


def split_factor_after(ticker, base_ym, as_of):
    """Share multiplier after the base month, through the quote session only."""
    end = dt.date.fromisoformat(as_of)
    if end.isoformat()[:7] <= base_ym:
        raise ValueError('Preview session must be after its base month')
    sp = datafeed._splits().get(ticker) or []
    f = 1.0
    for ts, ratio in sp:
        date = dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).date()
        if date.isoformat()[:7] > base_ym and date <= end:
            ratio = float(ratio)
            if not math.isfinite(ratio) or ratio <= 0:
                raise ValueError('Invalid split multiplier')
            f *= ratio
    if not math.isfinite(f) or f <= 0:
        raise ValueError('Invalid cumulative split multiplier')
    return f


def price_ratio(ticker, current, base_ym, session):
    """Value all successor split shares against the verified as-traded cost."""
    base = nominal_prices.price(ticker, base_ym)
    if not all(math.isfinite(x) and x > 0 for x in (current, base)):
        raise ValueError('Invalid preview price')
    ratio = current * split_factor_after(ticker, base_ym, session) / base
    if not math.isfinite(ratio) or ratio <= 0:
        raise ValueError('Invalid preview price return')
    return ratio


def main():
    q = live_quotes.load_snapshot()
    quotes = q.get("quotes") or {}
    today = dt.date.fromisoformat(q['session']).isoformat()
    base_ym = '%d-12' % (int(today[:4])-1)
    official_month = '%s-12' % today[:4]
    live,sector,reference_version=db_ref.load_market_reference(today)
    db_ref.require_unquarantined_candidates(live)
    name_map={t:quotes.get(t,{}).get('name') or t for t in live}

    missing=sorted(t for t in live if t not in quotes)
    if missing:raise RuntimeError('预览缺少候选报价，禁止缩小排名池：'+', '.join(missing))

    rows = []
    for t in sorted(live):
        qq = quotes.get(t)
        if not isinstance(qq,dict) or type(qq.get('p')) not in (int,float) or not math.isfinite(qq['p']) or qq['p']<=0:
            raise RuntimeError('预览候选报价无效：'+t)
        cur = float(qq["p"])
        if cur < MIN_PRICE:
            continue
        if not sector.get(t) or sector[t]=='Unknown':
            raise RuntimeError('预览缺少候选行业：'+t)
        ratio = price_ratio(t, cur, base_ym, q['session'])
        # An unresolved outlier must stop publication, not shrink the pool.
        if ratio > 8 or ratio < 1 / 8:
            raise RuntimeError('Preview return outside reviewed range: '+t)
        rows.append({
            "ticker": t,
            "name": name_map.get(t, t),
            "sector": sector.get(t) or "Unknown",
            "price": round(cur, 2),
            "ytd": ratio - 1,
        })

    rows.sort(key=lambda r: -r["ytd"])

    # 行业约束：同行业最多 2 只
    picks, cnt = [], {}
    for r in rows:
        if len(picks) >= TOPN:
            break
        s = r["sector"]
        if cnt.get(s, 0) >= 2:
            continue
        picks.append(r)
        cnt[s] = cnt.get(s, 0) + 1

    if len(picks)!=TOPN:raise RuntimeError('预览未形成完整三只持仓，禁止发布')
    # 权重：有 2 只同行业 → 该行业 60%，其余 40%
    dup = [s for s, c in cnt.items() if c >= 2]
    if dup:
        w = [(0.60 / 2) if p["sector"] in dup else (0.40 / max(1, len(picks) - 2)) for p in picks]
    else:
        w = [1.0 / len(picks)] * len(picks)
    tot = sum(w) or 1.0
    w = [x / tot for x in w]
    w[-1] += 1.0 - sum(w)          # 消除浮点尾差，保证合计恰为 1
    tot = 1.0
    _ws = [round(x / tot, 4) for x in w]
    _ws[-1] = round(1.0 - sum(_ws[:-1]), 4)   # 四舍五入后仍保证合计为 1
    for p, ww in zip(picks, _ws):
        p["weight"] = ww
        p["ytd"] = round(p["ytd"], 4)
        p["rank"] = picks.index(p) + 1

    out = {
        "final": False,
        "as_of": q.get("ts_local") or "",
        "as_of_ts": q.get("ts"),
        "session": q.get("session"),
        "base_month": base_ym,
        "universe": len(live),
        "ranked": len(rows),
        "excluded": [],
        "excluded_n": 0,
        "hold_year": int(base_ym[:4]) + 2,
        "official_month": official_month,
        "quote_version": q['version'],
        "reference_version": reference_version,
        "top": picks,
        "near": [{"ticker": r["ticker"], "sector": r["sector"], "ytd": round(r["ytd"], 4)}
                 for r in rows[:12] if r["ticker"] not in {p["ticker"] for p in picks}][:5],
        "note": "年初至今涨幅排名，年末才最终确定；同行业最多 2 只、该行业≤60%",
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("预览：%s → %s" % (
        " / ".join("%s %+.0f%%" % (p["ticker"], p["ytd"] * 100) for p in picks),
        "排名池 %d 只" % len(rows)))


if __name__ == "__main__":
    main()
