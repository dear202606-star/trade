# -*- coding: utf-8 -*-
"""fetch_stockanalysis.py — 用 stockanalysis.com 补退市票的全收益历史

为什么它重要（2026-11-01 实测）
  · 字段 `a` = 拆股+分红复权，与库内 yahoo/AV 的全收益价**逐值相同**
    （DUK / NVDA / KO 120 个月比值全部 1.0000，月收益一致到 0.01pp）
  · **对退市票同样可用**（ATVI 实测有数据），而 Yahoo 对退市票一律 404
  · 免费、无需 key、月度数据可得

限制
  · 免费层单标的最多 10 年（range=10Y），退市票的窗口是「退市前 10 年」
  · 因此它能把 2016 年后的覆盖补满，2010–2015 仍需 Alpha Vantage

用法
  python fetch_stockanalysis.py --symbols 50          # 补前 50 只缺全收益的
  python fetch_stockanalysis.py --ticker ATVI         # 单只
"""
import argparse
import io
import json
import os
import sys
import time
import urllib.request

import psycopg2

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
REF = "fnvahsyrzmmxwovbsqpc"
HOST = "aws-0-ap-northeast-1.pooler.supabase.com"
UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "Chrome/130 Safari/537.36")}
API = "https://stockanalysis.com/api/symbol/s/%s/history?range=10Y&period=Monthly"


def db():
    pw = os.environ.get("SUPABASE_DB_PASSWORD", "")
    if not pw:
        p = os.path.join(BASE, "output", "_supabase_dbpass.txt")
        if os.path.exists(p):
            pw = io.open(p, encoding="utf-8").read().strip()
    c = psycopg2.connect(host=HOST, port=6543, user="postgres." + REF, password=pw,
                         dbname="postgres", connect_timeout=20, sslmode="require")
    c.autocommit = True
    return c


def fetch(sym, tries=3):
    url = API % sym.lower().replace(".", "-")
    for attempt in range(tries):
        try:
            raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) \
                .read().decode("utf-8", "replace")
            d = json.loads(raw)
            data = d.get("data")
            rows = data.get("data") if isinstance(data, dict) else data
            if not rows:
                return {}
            out = {}
            for x in rows:
                t = (x.get("t") or "")[:7]
                a = x.get("a") or x.get("c")
                c = x.get("c") or a
                if len(t) == 7 and a:
                    out[t] = (float(a), float(c) if c else float(a))
            return out
        except Exception as e:
            if attempt == tries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=int, default=0)
    ap.add_argument("--ticker", default="")
    a = ap.parse_args()

    import ingest_validate as IV
    import db_prices as DB

    targets = []
    if a.ticker:
        targets = [a.ticker]
    else:
        c = db()
        with c.cursor() as cur:
            cur.execute("""select symbol from prices_monthly group by 1
                           having count(*) filter (where basis='total_return') = 0
                           order by symbol""")
            targets = [r[0] for r in cur.fetchall()]
        c.close()
        if a.symbols:
            targets = targets[:a.symbols]

    print("目标 %d 只（缺全收益）" % len(targets), flush=True)
    ok = bad = 0
    batch = []
    t0 = time.time()
    for i, sym in enumerate(targets, 1):
        try:
            bars = fetch(sym)
        except Exception as e:
            bad += 1
            print("  ✗ %-6s 抓取失败 %s" % (sym, str(e)[:50]), flush=True)
            continue
        if not bars:
            bad += 1
            continue
        good, why, _ = IV.check_payload(sym, bars, "total_return", allow_stale=True)
        if not good:
            bad += 1
            print("  ✗ %-6s %s" % (sym, why), flush=True)
            continue
        for m, (adj, raw) in bars.items():
            batch.append((sym, m, adj, raw, "stockanalysis", "ok", "total_return"))
        ok += 1
        if len(batch) >= 4000:
            DB.write_rows(batch)
            batch = []
        if i % 20 == 0:
            print("  %d/%d 成功%d 失败%d %.0fs" % (i, len(targets), ok, bad, time.time() - t0), flush=True)
        time.sleep(1.0)
    if batch:
        DB.write_rows(batch)
    print("完成：成功 %d / 失败 %d / 用时 %.0fs" % (ok, bad, time.time() - t0))
    try:
        DB.log_run("stockanalysis", len(targets), ok, failed=bad,
                   duration_s=time.time() - t0, note="10Y monthly, total-return")
    except Exception:
        pass


if __name__ == "__main__":
    main()
