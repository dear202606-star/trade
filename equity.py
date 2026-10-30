# -*- coding: utf-8 -*-
"""equity.py — 计算策略组合的每日净值曲线

数据来源：新浪美股日线（与回测同源）
口径：按 signal_history 里的正式持仓权重，从上一年 12 月最后一个交易日建仓，
      每日按收盘价计算组合净值；同时算标普 500 基准。

产物：docs/data/equity.json
  {base_date, updated, weights:[{ticker,weight}], points:[{d,s,b}], stats:{...}}
"""
import json
from datetime import datetime, timedelta, timezone
import os
import re
import sys
import time

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
PAT = re.compile(r"\(\s*(\[.*\])\s*\)", re.S)
OUT = os.path.join("docs", "data", "equity.json")


def settled_cutoff():
    """返回最后一个「已收盘」交易日的 YYYYMMDD（美东时间 16:10 后算当日已收盘）"""
    try:
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        now_et = datetime.now(timezone.utc) - timedelta(hours=4)
    d = now_et.date()
    if (now_et.hour, now_et.minute) < (16, 10):
        d = d - timedelta(days=1)
    # 跳过周末
    while d.weekday() >= 5:
        d = d - timedelta(days=1)
    return d.strftime("%Y%m%d")


def daily_closes(ticker):
    """返回 {yyyymmdd: close}，未复权，仅用于同口径比例计算"""
    sym = ticker.replace(".", "$")
    url = ("https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/x/"
           f"US_MinKService.getDailyK?symbol={sym}")
    for _ in range(3):
        try:
            r = requests.get(url, headers={"User-Agent": UA,
                                           "Referer": "https://finance.sina.com.cn"}, timeout=25)
            m = PAT.search(r.text)
            if not m:
                return None
            arr = json.loads(m.group(1))
            out = {}
            for x in arr:
                try:
                    d, c = x["d"], float(x["c"])
                    if c > 0 and d[:4].isdigit():
                        out[d.replace("-", "")] = c
                except Exception:
                    continue
            return out
        except Exception:
            time.sleep(1)
    return None


def main():
    if not os.path.exists("output/signal_history.json"):
        print("缺少 signal_history.json", file=sys.stderr)
        return 1
    hist = json.load(open("output/signal_history.json", encoding="utf-8"))
    rec = hist[0]
    official = rec.get("official") or rec.get("picks") or []
    exposure = rec.get("exposure", 1.0)
    if not official:
        print("没有正式持仓", file=sys.stderr)
        return 1

    # 建仓日：上一年 12 月最后一个交易日
    om = rec.get("official_month") or "2025-12"
    base_ym = om.replace("-", "")

    tickers = [p["ticker"] for p in official] + ["SPY"]
    data = {}
    raw_data = {}
    for t in tickers:
        d = daily_closes(t)
        if d:
            raw_data[t] = d
            # I3：收益序列用复权价（拆股不至于画出断崖）；展示仍用原始成交价
            try:
                import datafeed as _df
                data[t] = _df.adjust_daily(t, d)
            except Exception:
                data[t] = d
    missing = [t for t in tickers if t not in data]
    if missing:
        print(f"WARN: 缺日线 {missing}", file=sys.stderr)
    if "SPY" not in data:
        print("ERROR: 没有 SPY 日线", file=sys.stderr)
        return 1

    # 交易日序列：以「持仓」为准（SPY 可能晚一天更新）
    cutoff = settled_cutoff()
    _cal = set()
    for _t in [p["ticker"] for p in official]:
        if _t in data:
            _cal |= set(data[_t].keys())
    if not _cal:
        _cal = set(data["SPY"].keys())
    days = sorted(k for k in _cal if base_ym + "01" <= k <= cutoff)
    # 找建仓日：base_ym 内最后一个交易日（同样以持仓为准）
    base_days = [d for d in sorted(_cal) if d[:6] == base_ym]
    if not base_days:
        print("ERROR: 建仓月无交易日", file=sys.stderr)
        return 1
    base_day = base_days[-1]
    days = [d for d in days if d >= base_day]

    base_px = {}
    for t in data:
        # 基准价：建仓日或之前最近一个交易日
        cand = [d for d in sorted(data[t]) if d <= base_day]
        if not cand:
            print(f"WARN: {t} 建仓日无价，跳过", file=sys.stderr)
            continue
        base_px[t] = (cand[-1], data[t][cand[-1]])

    pts = []
    for d in days:
        s_val = 0.0
        ok = True
        for p in official:
            t = p["ticker"]
            if t not in base_px:
                ok = False
                break
            bd, bp = base_px[t]
            px = data[t].get(d)
            if px is None:
                # 用上一个有效价
                prev = [k for k in sorted(data[t]) if k <= d]
                px = data[t][prev[-1]] if prev else None
            if px is None:
                ok = False
                break
            s_val += p["weight"] * (px / bp)
        if not ok:
            continue
        # 净值口径 = 满仓持有（与回测一致）。敞口只作「建议仓位」展示，
        # 不参与净值计算，避免开关翻转时历史收益被改写。
        bd, bp = base_px["SPY"]
        bpx = data["SPY"].get(d)
        if bpx is None:
            _cand = [k for k in sorted(data["SPY"]) if k <= d]
            bpx = data["SPY"][_cand[-1]] if _cand else None
        if bpx is None:
            continue
        b_val = bpx / bp
        pts.append({"d": d, "s": round(s_val, 6), "b": round(b_val, 6)})

    if not pts:
        print("ERROR: 净值为空", file=sys.stderr)
        return 1

    # 统计
    last = pts[-1]
    peak, mdd = 0.0, 0.0
    for p in pts:
        peak = max(peak, p["s"])
        mdd = min(mdd, p["s"] / peak - 1)
    ret = last["s"] - 1
    bret = last["b"] - 1

    out = {
        "last_session": days[-1] if days else None,
        "base_date": base_day,
        "updated": (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M"),
        "updated_tz": "Asia/Shanghai",
        "exposure": exposure,
        "weights": [{"ticker": p["ticker"], "weight": p["weight"]} for p in official],
        "benchmark": "SPY",
        "points": pts,
        # 理论账户：建仓价 = 基准日收盘价，现价 = 最新交易日收盘价
        # 页面按「本金 × 权重 ÷ 建仓价」换算股数，与净值曲线同一口径
        "position": [],
        "stats": {
            "strategy": round(ret, 6),
            "benchmark": round(bret, 6),
            "excess": round(ret - bret, 6),
            "mdd": round(mdd, 6),
            "days": len(pts),
        },
    }

    last_day = pts[-1]["d"]
    for p in official:
        t = p["ticker"]
        raw = raw_data.get(t) or data[t]
        adj = data[t]
        # 复权价：用于股数与市值（拆股后仍连续）
        cadj = [k for k in sorted(adj) if k <= base_day]
        bd = cadj[-1] if cadj else None
        bp_adj = adj[bd] if bd else None
        px_adj = adj.get(last_day)
        if px_adj is None:
            c2 = [k for k in sorted(adj) if k <= last_day]
            px_adj = adj[c2[-1]] if c2 else None
        # 原始价：用于展示（用户实际成交/看到的价格）
        craw = [k for k in sorted(raw) if k <= base_day]
        bp_raw = raw[craw[-1]] if craw else None
        px_raw = raw.get(last_day)
        if px_raw is None:
            c3 = [k for k in sorted(raw) if k <= last_day]
            px_raw = raw[c3[-1]] if c3 else None
        out["position"].append({
            "ticker": t,
            "weight": p["weight"],
            "entry_date": bd,
            "entry": round(bp_adj, 4) if bp_adj else None,
            "entry_raw": round(bp_raw, 4) if bp_raw else None,
            "last_raw": round(px_raw, 4) if px_raw else None,
            "entry": round(bp_adj, 4) if bp_adj else None,
            "last_date": last_day,
            "last": round(px_adj, 4) if px_adj else None,
        })
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    print(f"净值 {len(pts)} 个交易日（{base_day} 起）｜策略 {ret*100:+.2f}% ｜"
          f"标普 {bret*100:+.2f}% ｜ 最大回撤 {mdd*100:.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
