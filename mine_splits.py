# -*- coding: utf-8 -*-
"""G1：从日线自动挖掘拆股事件并登记到 splits.json（唯一权威表）。

规则：单日跌幅 ≤ -30% 且随后 5 个交易日内未回到前收的 85% 以上，
且跌幅接近常见拆股比例（2/3/4/5/10 或其倒数），则登记为拆股。
只新增，不覆盖；`source: auto`。
"""
import json
import os

import requests

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}
SPLITS = "output/splits.json"
COMMON = [2, 3, 4, 5, 10, 1.5, 2.5, 1.25, 1.3311]
TOL = 0.06


def daily(ticker):
    sym = ticker.replace(".", "$")
    url = ("https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/x/"
           f"US_MinKService.getDailyK?symbol={sym}")
    import re
    pat = re.compile(r"\((\[.*\])\)", re.S)
    for _ in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=25)
            m = pat.search(r.text)
            if not m:
                return {}
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
            continue
    return {}


def detect(bars):
    days = sorted(bars)
    found = []
    for i in range(1, len(days)):
        prev, cur = bars[days[i - 1]], bars[days[i]]
        if prev <= 0:
            continue
        drop = cur / prev
        if drop > 0.70:
            continue
        ratio = prev / cur                      # 例如 2.0 表示 2:1
        hit = None
        for c in COMMON:
            if abs(ratio - c) / c <= TOL:
                hit = c
                break
        if not hit and abs(ratio - 1.3311) / 1.3311 <= TOL:
            hit = 1.3311
        if not hit:
            continue
        # 确认：随后 5 日未回到前收 85% 以上（排除真实暴跌后的 V 型反弹）
        after = [bars[d] for d in days[i:i + 6]]
        if after and max(after) > prev * 0.85:
            continue
        found.append((days[i], round(hit, 4)))
    return found


def main():
    sp = {}
    if os.path.exists(SPLITS):
        try:
            sp = json.load(open(SPLITS, encoding="utf-8"))
        except Exception:
            sp = {}
    tickers = []
    try:
        import datafeed
        sec, _ = datafeed.load_sector()
        tickers = sorted(sec.keys())
    except Exception:
        pass

    added = 0
    for t in tickers:
        bars = daily(t)
        if len(bars) < 60:
            continue
        evs = detect(bars)
        if not evs:
            continue
        have = {x[0] for x in sp.get(t, [])}
        for d, ratio in evs:
            import datetime as dt
            ts = int(dt.datetime.strptime(d, "%Y%m%d").timestamp())
            if any(abs(ts - h) < 86400 for h in have):
                continue
            sp.setdefault(t, []).append([ts, ratio])
            sp[t].sort()
            added += 1
            print("  登记 %-6s %s ×%.4f" % (t, d, ratio))

    os.makedirs("output", exist_ok=True)
    json.dump(sp, open(SPLITS, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("splits.json 新增 %d 条，现有 %d 只股票共 %d 条" %
          (added, len(sp), sum(len(v) for v in sp.values())))


if __name__ == "__main__":
    main()
