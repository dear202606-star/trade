# -*- coding: utf-8 -*-
"""stoploss_backtest.py — 日线级移动止损，检验能否把每年做成正收益

三分策略（上年前 3 名等权、全年持有）在 2010–2026 只有 2011 亏（约 -26%），
但月度趋势过滤救不回来——月末信号出来时已经跌掉大半。
本脚本用**日线**做移动止损，看能否把这种年份拉回正收益。

规则（无未来函数）：
  每年初按上年 12 月动量选定 3 只，全年持有
  单只从「持有期最高点」回撤 X% → 清仓该只，剩余时间持现金（年化 2%）
  止损后不回补，次年重新建仓

产物：docs/data/stoploss.json
"""
import datetime as dt
import json
import os
import re
import sys
import time

import requests

BASE = os.path.dirname(os.path.abspath(__file__))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
PAT = re.compile(r"\(\s*(\[.*\])\s*\)", re.S)
CACHE = os.path.join(BASE, "data", "daily")
OUT = os.path.join(BASE, "docs", "data", "stoploss.json")
CASH_Y = 0.02
THRESHOLDS = [0.15, 0.20, 0.25, 0.30]


def load_splits():
    try:
        return json.load(open(os.path.join(BASE, "output", "splits.json"), encoding="utf-8"))
    except Exception:
        return {}


def fetch_daily(ticker):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, ticker + ".json")
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    url = ("https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/x/"
           f"US_MinKService.getDailyK?symbol={ticker.replace('.', '$')}")
    raw = {}
    for _ in range(3):
        try:
            r = requests.get(url, headers={"User-Agent": UA,
                                           "Referer": "https://finance.sina.com.cn"}, timeout=25)
            m = PAT.search(r.text)
            if not m:
                time.sleep(1)
                continue
            for x in json.loads(m.group(1)):
                d, c = x.get("d"), x.get("c")
                try:
                    c = float(c)
                except Exception:
                    continue
                if d and c > 0:
                    raw[d] = c
            break
        except Exception:
            time.sleep(1)
    if not raw:
        return None
    sp = load_splits().get(ticker) or []
    out = {}
    for d, c in raw.items():
        ts = time.mktime(time.strptime(d, "%Y-%m-%d"))
        f = 1.0
        for sd, ratio in sp:
            if int(sd) <= ts:
                f *= float(ratio)
        out[d] = c * f
    try:
        json.dump(out, open(p, "w", encoding="utf-8"), separators=(",", ":"))
    except Exception:
        pass
    return out


def simulate(holdings, daily, thresh, year, reentry=False):
    """年度日线模拟 → (年收益, 是否有票被止损)"""
    start, end = f"{year}-01-01", f"{year}-12-31"
    series = {}
    for t in holdings:
        s = [(d, p) for d, p in sorted(daily[t].items()) if start <= d <= end]
        if len(s) >= 20:
            series[t] = s
    if len(series) < 2:
        return None, False

    n = len(holdings)
    w = 1.0 / n
    base = {t: series[t][0][1] for t in series}
    peaks = dict(base)
    holding = {t: True for t in series}
    idx = {t: 0 for t in series}
    dates = sorted({d for s in series.values() for d, _ in s})
    stop_px = {}
    last_month = {}

    for day in dates:
        ym = day[:7]
        for t, s in series.items():
            while idx[t] < len(s) and s[idx[t]][0] <= day:
                px = s[idx[t]][1]
                idx[t] += 1
                if px > peaks[t]:
                    peaks[t] = px
                if holding[t] and px / peaks[t] - 1 <= -thresh:
                    holding[t] = False
                    stop_px[t] = px
                elif (reentry and (not holding[t]) and t in stop_px
                      and last_month.get(t) not in (None, ym) and px > stop_px[t]):
                    # 新月份开始、且已站回止损价 → 重新入场
                    holding[t] = True
                    peaks[t] = px
                    stop_px.pop(t, None)
                if last_month.get(t) != ym:
                    last_month[t] = ym

    final = 0.0
    for t in holdings:
        if t not in series:
            continue
        final += w * ((series[t][-1][1] / base[t]) if holding[t] else (1 + CASH_Y * 0.5))
    return final - 1, any(not v for v in holding.values())


def main():
    hp = os.path.join(BASE, "docs", "data", "strategy_history.json")
    if not os.path.exists(hp):
        print("缺 strategy_history.json", file=sys.stderr)
        return 1
    years = (json.load(open(hp, encoding="utf-8")).get("years") or [])
    if not years:
        print("逐年记录为空", file=sys.stderr)
        return 1

    tickers = sorted({p["ticker"] for r in years for p in r["holdings"]})
    print(f"抓取 {len(tickers)} 只日线…", flush=True)
    daily = {}
    for i, t in enumerate(tickers, 1):
        d = fetch_daily(t)
        if d:
            daily[t] = d
        if i % 20 == 0:
            print(f"  {i}/{len(tickers)}", flush=True)
    print(f"  成功 {len(daily)}/{len(tickers)}", flush=True)

    res = {}
    variants = [(None, False)] + [(t, False) for t in THRESHOLDS] + [(t, True) for t in THRESHOLDS]
    for th, reent in variants:
        rows = []
        for r in years:
            y = r["year"]
            hold = [p["ticker"] for p in r["holdings"] if p["ticker"] in daily]
            if len(hold) < 2:
                continue
            ret, hit = simulate(hold, daily, 999 if th is None else th, y, reent)
            if ret is None:
                continue
            rows.append({"year": y, "ret": round(ret, 4), "stopped": hit})
        if not rows:
            continue
        eq = 1.0
        for x in rows:
            eq *= 1 + x["ret"]
        key = ("none" if th is None else str(int(th * 100))) + ("R" if reent else "")
        res[key] = {
            "label": ("不止损" if th is None else f"止损 {int(th*100)}%" + (" + 再入场" if reent else "")),
            "years": rows,
            "neg": [x["year"] for x in rows if x["ret"] < 0],
            "total": round(eq - 1, 4),
            "cagr": round(eq ** (1 / max(len(rows), 1)) - 1, 4),
            "wins": sum(1 for x in rows if x["ret"] > 0),
            "n": len(rows),
        }

    out = {"results": res, "thresholds": THRESHOLDS,
           "cash_rate": CASH_Y, "updated": dt.date.today().isoformat()}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    print(f"\n{'方案':16s}{'年化':>8s}{'累计':>9s}{'亏损年':>7s}  亏损年份")
    for k in ["none"] + [str(int(t * 100)) for t in THRESHOLDS] + [str(int(t * 100)) + "R" for t in THRESHOLDS]:
        if k not in res:
            continue
        r = res[k]
        print(f"{r['label']:16s}{r['cagr']*100:7.1f}%{r['total']*100:8.0f}%"
              f"{len(r['neg']):7d}  {r['neg']}")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
