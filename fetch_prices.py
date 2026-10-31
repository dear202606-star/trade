# -*- coding: utf-8 -*-
"""fetch_prices.py — Alpha Vantage 月度复权行情回填（异步、断点续传）

设计要点
  · 数据源：Alpha Vantage TIME_SERIES_MONTHLY_ADJUSTED（拆股+分红复权，覆盖退市票）
  · 免费额度 25 次/天 → 每次只补 N 只（默认 24），历史数据异步补齐（约 40 天跑完 1209 只）
  · 断点续传：已存在且覆盖足够新的标的跳过
  · 产物：static/prices/<TICKER>.json，提交入库 → 回测完全可复现
  · 同时记录 raw close，便于校验复权比例

用法
    python fetch_prices.py                 # 补 24 只
    python fetch_prices.py --limit 5       # 补 5 只（测试）
    python fetch_prices.py --ticker NVDA   # 指定标的
"""
import argparse
import io
import json
import os
import sys
import time
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
PRICES = os.path.join(BASE, "static", "prices")
MANIFEST = os.path.join(BASE, "static", "prices_manifest.json")
MEMBERS = os.path.join(BASE, "static", "members_frozen.csv")
KEY_FILE = os.path.join(BASE, "output", "_api_keys.local.txt")
API = ("https://www.alphavantage.co/query?function=TIME_SERIES_MONTHLY_ADJUSTED"
       "&symbol=%s&apikey=%s")
UA = {"User-Agent": "Mozilla/5.0"}


def api_key():
    k = os.environ.get("ALPHAVANTAGE_API_KEY")
    if k:
        return k.strip()
    if os.path.exists(KEY_FILE):
        for line in io.open(KEY_FILE, encoding="utf-8"):
            if line.startswith("alphavantage_key="):
                return line.split("=", 1)[1].strip()
    return ""


def need_tickers():
    """需要回填的标的：冻结成分表里 2009 年后仍在指数内的全部"""
    import pandas as pd
    m = pd.read_csv(MEMBERS, dtype=str).fillna("2099-12-31")
    m = m[m["end_date"] >= "2009-01-01"]
    return sorted(set(m["ticker"].tolist()))


def fetch(ticker, key, retries=3):
    url = API % (ticker.replace(".", "-"), key)
    for i in range(retries):
        try:
            raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                         timeout=45).read().decode()
            d = json.loads(raw)
            if "Monthly Adjusted Time Series" in d:
                return d["Monthly Adjusted Time Series"]
            msg = d.get("Note") or d.get("Information") or d.get("Error Message") or str(d)[:120]
            if "rate limit" in str(msg).lower() or "premium" in str(msg).lower():
                raise RuntimeError("RATE_LIMIT: %s" % msg)
            return None
        except RuntimeError:
            raise
        except Exception as e:
            if i == retries - 1:
                print("  ! %s 失败：%s" % (ticker, str(e)[:90]))
                return None
            time.sleep(3 * (i + 1))
    return None


def save(ticker, series):
    rows = []
    for day in sorted(series):
        v = series[day]
        adj = v.get("5. adjusted close")
        raw = v.get("4. close")
        if adj is None:
            continue
        rows.append([day[:7], round(float(adj), 4),
                     round(float(raw), 4) if raw else None])
    if not rows:
        return False
    os.makedirs(PRICES, exist_ok=True)
    p = os.path.join(PRICES, ticker + ".json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"ticker": ticker, "source": "alphavantage_monthly_adjusted",
                   "bars": rows}, f, ensure_ascii=False, separators=(",", ":"))
    return True


def covered(ticker, min_month):
    p = os.path.join(PRICES, ticker + ".json")
    if not os.path.exists(p):
        return False
    try:
        d = json.load(open(p, encoding="utf-8"))
        bars = d.get("bars") or []
        return bool(bars) and bars[0][0] <= "2010-01" and bars[-1][0] >= min_month
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=24)
    ap.add_argument("--ticker", default="")
    args = ap.parse_args()

    key = api_key()
    if not key:
        print("✗ 缺少 API key：设置环境变量 ALPHAVANTAGE_API_KEY，或写入 output/_api_keys.local.txt")
        return 2

    tickers = [args.ticker] if args.ticker else need_tickers()
    latest = time.strftime("%Y-%m")
    todo = [t for t in tickers if not covered(t, latest)]
    print("待回填 %d / 共 %d 只（额度上限 %d）" % (len(todo), len(tickers), args.limit), flush=True)

    done, failed = 0, []
    for t in todo[:args.limit]:
        series = fetch(t, key)
        if series is None:
            failed.append(t)
            continue
        if save(t, series):
            done += 1
            d = json.load(open(os.path.join(PRICES, t + ".json"), encoding="utf-8"))
            print("  ✓ %-6s %d 个月  %s → %s" % (
                t, len(d["bars"]), d["bars"][0][0], d["bars"][-1][0]), flush=True)
        else:
            failed.append(t)
        time.sleep(1.2)          # 温和限速

    have = len([f for f in os.listdir(PRICES) if f.endswith(".json")]) if os.path.isdir(PRICES) else 0
    man = {"source": "alphavantage_monthly_adjusted", "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "symbols": have, "expected": len(tickers), "latest_month": latest,
           "note": "免费额度 25 次/天，异步回填；文件提交入库后可离线复现"}
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=2)
    print("本次成功 %d，失败 %d，累计覆盖 %d/%d（%.0f%%）" % (
        done, len(failed), have, len(tickers), 100.0 * have / max(1, len(tickers))))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
