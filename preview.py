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

import datafeed

BASE_YM = "2025-12"          # 建仓基准：上年 12 月末
MIN_PRICE = 5.0
TOPN = 3
OUT = os.path.join("docs", "data", "preview.json")


def split_factor_after(ticker, base_ym):
    """base_ym 之后发生的拆股累计比例（用于把现价换算到基准口径）"""
    sp = datafeed._splits().get(ticker) or []
    f = 1.0
    for ts, ratio in sp:
        try:
            ym = dt.datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m")
        except Exception:
            continue
        if ym > base_ym:
            f *= float(ratio)
    return f


def main():
    q = json.load(open(os.path.join("docs", "data", "quotes.json"), encoding="utf-8"))
    quotes = q.get("quotes") or {}
    m = datafeed.load_members()
    m["start_date"] = m["start_date"].astype(str)
    m["end_date"] = m["end_date"].fillna("2099-12-31").astype(str)
    today = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)).strftime("%Y-%m-%d")
    live = set(m[(m.start_date <= today) & (m.end_date >= today)]["ticker"])
    sector, name_map = datafeed.load_sector()

    rows = []
    bad = []
    for t in sorted(live):
        qq = quotes.get(t)
        if not qq or not qq.get("p"):
            continue
        cur = float(qq["p"])
        if cur < MIN_PRICE:
            continue
        bars = datafeed.load_bars(t)
        if not bars or BASE_YM not in bars:
            continue
        base_raw = bars[BASE_YM][1] if len(bars[BASE_YM]) > 1 else bars[BASE_YM][0]
        if not base_raw or base_raw <= 0:
            continue
        cur_adj = cur * split_factor_after(t, BASE_YM)
        # 异常过滤：单月涨跌超过 8 倍且无拆股登记 → 源数据异常，剔除
        ratio = cur_adj / base_raw
        if ratio > 8 or ratio < 1 / 8:
            bad.append(t)
            continue
        if abs(ratio - 1) > 8:      # 十个月累计超过 800% 也视为异常
            bad.append(t)
            continue
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
        if s == "Unknown":
            continue                      # 未知行业不参与（与正式规则一致）
        if cnt.get(s, 0) >= 2:
            continue
        picks.append(r)
        cnt[s] = cnt.get(s, 0) + 1

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
        "as_of": q.get("ts_local") or "",
        "as_of_ts": q.get("ts"),
        "session": q.get("session"),
        "base_month": BASE_YM,
        "universe": len(live),
        "ranked": len(rows),
        "excluded": sorted(bad)[:20],
        "excluded_n": len(bad),
        "hold_year": int(today[:4]) + 1,
        "official_month": f"{today[:4]}-12",
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
