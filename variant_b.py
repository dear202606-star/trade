# -*- coding: utf-8 -*-
"""口径 B：排名池只保留「上一年整年都在成分股里」的股票。

对比：
  口径 A（现有）排名日时点成分  —— 含年中新纳入的票（如 LITE 2026-03 才入指）
  口径 B（本脚本）全年成分       —— 要求 start_date ≤ 上年 1/1 且 end_date ≥ 上年 12/31

产出（与 A 同结构，前端可原样渲染）：
  docs/data/b/strategy_history.json   逐年回测
  docs/data/b/curve.json              全周期曲线
  docs/data/b/signal.json             当年正式持仓
  docs/data/b/equity.json             当年净值与持仓明细
  docs/data/b/preview.json            次年预览
"""
import datetime as dt
import json
import os

import datafeed

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "docs", "data", "b")
START_YEAR = 2010
TOPN = 3
SECTOR_CAP = 0.60
MIN_PRICE = 5.0


def members_full_year(m, year):
    """该年整年都在成分股里的股票集合"""
    a = "%d-01-01" % year
    b = "%d-12-31" % year
    return set(m[(m.start_date <= a) & (m.end_date >= b)]["ticker"].unique())


def ret12(bars, rank_month, base_month):
    if not bars or rank_month not in bars or base_month not in bars:
        return None
    a0 = bars[base_month][0]
    a1 = bars[rank_month][0]
    if not a0 or a0 <= 0 or not a1:
        return None
    return a1 / a0 - 1


def pick_year(m, sector, year):
    """选出「hold_year = year」的一年前三（口径 B）"""
    y0 = year - 1
    pool = members_full_year(m, y0)
    rank_m, base_m = "%d-12" % y0, "%d-12" % (y0 - 1)
    rows = []
    for t in pool:
        bars = datafeed.load_bars(t)
        r = ret12(bars, rank_m, base_m)
        if r is None:
            continue
        raw = bars.get(rank_m, (None, None))[1]
        if not raw or raw < MIN_PRICE:
            continue
        rows.append({"ticker": t, "ret12": r,
                     "sector": sector.get(t) or "Unknown",
                     "name": t})
    rows.sort(key=lambda x: -x["ret12"])
    picks, cnt = [], {}
    for r in rows:
        if len(picks) >= TOPN:
            break
        s = r["sector"]
        if s == "Unknown" or cnt.get(s, 0) >= 2:
            continue
        picks.append(r)
        cnt[s] = cnt.get(s, 0) + 1

    dup = [s for s, c in cnt.items() if c >= 2]
    if dup:
        w = [(SECTOR_CAP / 2) if p["sector"] in dup else
             ((1 - SECTOR_CAP) / max(1, len(picks) - 2)) for p in picks]
    else:
        w = [1.0 / len(picks)] * len(picks)
    tot = sum(w) or 1
    ws = [round(x / tot, 4) for x in w]
    if ws:
        ws[-1] = round(1.0 - sum(ws[:-1]), 4)
    for p, ww in zip(picks, ws):
        p["weight"] = ww
    return picks, len(rows)


def year_return(picks, year):
    """该年持仓的收益（用月线复权，年末比年初）"""
    y0, y1 = "%d-12" % (year - 1), "%d-12" % year
    ret, covered = 0.0, 0.0
    for p in picks:
        bars = datafeed.load_bars(p["ticker"])
        r = ret12(bars, y1, y0)
        if r is None:
            continue
        ret += p["weight"] * r
        covered += p["weight"]
    return (ret / covered) if covered > 0 else 0.0


def main():
    os.makedirs(OUT, exist_ok=True)
    m = datafeed.load_members()
    m["start_date"] = m["start_date"].astype(str)
    m["end_date"] = m["end_date"].fillna("2099-12-31").astype(str)
    sector, _ = datafeed.load_sector()

    today_et = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)
    end_year = today_et.year - 1          # 最后一个完整年份

    years = []
    for y in range(START_YEAR, end_year + 1):
        picks, pool_n = pick_year(m, sector, y)
        ret = year_return(picks, y)
        years.append({
            "year": y,
            "holdings": [{"ticker": p["ticker"], "name": p["name"],
                          "sector": p["sector"], "weight": p["weight"],
                          "ret12": round(p["ret12"], 4)} for p in picks],
            "ret": round(ret, 4),
            "pool": pool_n,
        })
        print("  %d  %+7.2f%%  %s  （全年成分池 %d）" % (
            y, ret * 100, " / ".join(p["ticker"] for p in picks), pool_n))

    # 曲线（年末复利）
    eq, pts = 1.0, []
    for r in years:
        eq *= (1 + r["ret"])
        pts.append({"d": "%d-12" % r["year"], "s": round(eq, 6), "b": None})
    total = eq - 1
    n = len(years)
    cagr = (eq ** (1 / n) - 1) if n and eq > 0 else 0
    wins = sum(1 for r in years if r["ret"] > 0)
    peak, mdd = 1.0, 0.0
    for v in [1] + [p["s"] for p in pts]:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)

    hist = {
        "years": years,
        "summary": {"start": START_YEAR, "end": end_year, "years": n,
                    "total": round(total, 4), "cagr": round(cagr, 4),
                    "mdd": round(mdd, 4), "worst_intra": round(mdd, 4),
                    "wins": wins, "losses": n - wins,
                    "note": "口径 B：上一年整年都在成分股里"},
        "variant": "B",
        "rule": "排名池 = 上一年 1/1–12/31 全程在标普 500 内的股票",
    }
    json.dump(hist, open(os.path.join(OUT, "strategy_history.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    json.dump({"points": pts, "years": years,
               "stats": {"total": round(total, 4), "cagr": round(cagr, 4),
                         "mdd": round(mdd, 4)},
               "variant": "B"},
              open(os.path.join(OUT, "curve.json"), "w", encoding="utf-8"), ensure_ascii=False)

    # 当年持仓（下一年）
    hold_year = end_year + 1
    picks, pool_n = pick_year(m, sector, hold_year)
    print("\n%d 年持仓（口径 B）：%s" % (hold_year, " / ".join(
        "%s %.0f%%" % (p["ticker"], p["weight"] * 100) for p in picks)))
    sig = {
        "variant": "B",
        "month": "%s" % dt.datetime.now().strftime("%Y-%m"),
        "official_month": "%d-12" % end_year,
        "official_year": hold_year,
        "valid_from": "%d-01-01" % hold_year,
        "valid_to": "%d-12-31" % hold_year,
        "rebalance_hint": "%d-12 最后一个交易日" % hold_year,
        "trend": "满仓",
        "exposure": 1.0,
        "official": [{"rank": i + 1, "ticker": p["ticker"], "name": p["name"],
                      "sector": p["sector"], "ret12": round(p["ret12"], 4),
                      "weight": p["weight"]} for i, p in enumerate(picks)],
        "picks": [],
        "pool": pool_n,
        "rule": hist["rule"],
    }
    json.dump(sig, open(os.path.join(OUT, "signal.json"), "w", encoding="utf-8"),
              ensure_ascii=False)

    # ---- 次年预览（口径 B：今年以来全程在成分股里）----
    try:
        q = json.load(open(os.path.join(BASE, "docs", "data", "quotes.json"), encoding="utf-8"))
        quotes = q.get("quotes") or {}
        a = "%d-01-01" % hold_year
        today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
        live = set(m[(m.start_date <= a) & (m.end_date >= today)]["ticker"].unique())
        base_m = "%d-12" % (hold_year - 1)
        rows2 = []
        for t in live:
            qq = quotes.get(t) or {}
            cur = qq.get("p")
            bars = datafeed.load_bars(t)
            if not cur or not bars or base_m not in bars:
                continue
            b0 = bars[base_m][1]
            if not b0 or b0 <= 0 or cur < MIN_PRICE:
                continue
            rows2.append({"ticker": t, "sector": sector.get(t) or "Unknown",
                          "ytd": cur / b0 - 1})
        rows2.sort(key=lambda x: -x["ytd"])
        picks2, cnt2 = [], {}
        for r in rows2:
            if len(picks2) >= TOPN:
                break
            s2 = r["sector"]
            if s2 == "Unknown" or cnt2.get(s2, 0) >= 2:
                continue
            picks2.append(r)
            cnt2[s2] = cnt2.get(s2, 0) + 1
        dup2 = [k for k, c in cnt2.items() if c >= 2]
        if dup2:
            w2 = [(SECTOR_CAP / 2) if p["sector"] in dup2 else
                  ((1 - SECTOR_CAP) / max(1, len(picks2) - 2)) for p in picks2]
        else:
            w2 = [1.0 / len(picks2)] * len(picks2)
        tot2 = sum(w2) or 1
        ws2 = [round(x / tot2, 4) for x in w2]
        if ws2:
            ws2[-1] = round(1.0 - sum(ws2[:-1]), 4)
        for i, (p, ww) in enumerate(zip(picks2, ws2)):
            p["weight"] = ww
            p["rank"] = i + 1
            p["ytd"] = round(p["ytd"], 4)
        json.dump({"variant": "B", "as_of": q.get("ts_local"), "base_month": base_m,
                   "hold_year": hold_year + 1,
                   "official_month": "%d-12" % hold_year,
                   "top": picks2, "final": False, "ranked": len(rows2),
                   "note": "口径 B：今年以来全程在成分股里"},
                  open(os.path.join(OUT, "preview.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
        print("次年预览（B）：%s" % " / ".join(
            "%s %+.0f%%" % (p["ticker"], p["ytd"] * 100) for p in picks2))
    except Exception as e:
        print("预览生成失败：%s" % e)

    # 供 equity.py 复用（EQ_SIGNAL 指向它）
    json.dump([{"official_month": sig["official_month"],
                "official": sig["official"],
                "exposure": 1.0, "trend": sig["trend"]}],
              open("output/signal_history_b.json", "w", encoding="utf-8"),
              ensure_ascii=False)
    print("已写入:", OUT)


if __name__ == "__main__":
    main()
