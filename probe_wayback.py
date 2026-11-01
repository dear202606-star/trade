# -*- coding: utf-8 -*-
"""probe_wayback.py — 用互联网档案馆（Wayback）找退市票的历史行情快照

思路
  Yahoo / Google Finance / Investing 的历史行情页在 2010-2023 被大量存档。
  退市票的当前页面 404，但**存档副本仍在**，页面上有月度或日度价格表（部分含 Adj Close）。
  这是唯一不依赖 API key、也不依赖站点当前是否存活的路径。

本脚本
  1. 用 CDX 接口列出某票所有存档快照
  2. 取最新/最全的一个，解析出价格行数
  3. 报告是否含「Adj Close」列
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

UA = {"User-Agent": "Mozilla/5.0 (compatible; research/1.0)"}
TICKERS = ["ATVI", "ETFC", "XL", "AABA", "CERN", "AAPL"]


def get(url, timeout=45, retries=3):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            last = "HTTP %d" % e.code
            if e.code == 429:
                time.sleep(8 * (i + 1))
                continue
            return e.code, b""
        except Exception as e:
            last = str(e)[:40]
            time.sleep(4)
    return None, str(last).encode()


def cdx(ticker, host="finance.yahoo.com"):
    url = ("http://web.archive.org/cdx/search/cdx?url=%s/quote/%s/history*"
           "&output=json&limit=40&collapse=timestamp:6&fl=timestamp,original,statuscode"
           % (host, ticker))
    st, body = get(url)
    if st != 200 or not body:
        return st, []
    try:
        rows = json.loads(body)
        return st, rows[1:] if rows else []
    except Exception:
        return st, []


def inspect(snapshot_url):
    st, body = get(snapshot_url)
    if st != 200 or not body:
        return st, 0, False, 0
    t = body.decode("utf-8", "replace")
    rows = len(re.findall(r"\d{4}-\d{2}-\d{2}", t))
    has_adj = ("Adj Close" in t) or ("Adj. Close" in t) or ("adjclose" in t.lower())
    divs = t.count("Dividend")
    return st, rows, has_adj, divs


def main():
    for t in TICKERS:
        print("\n===== %s =====" % t)
        for host in ("finance.yahoo.com", "www.google.com/finance"):
            st, rows = cdx(t, host)
            print("  CDX %-22s 状态=%s 快照=%d" % (host, st, len(rows)))
            if rows:
                for r in rows[-3:]:
                    ts, orig = r[0], r[1]
                    snap = "http://web.archive.org/web/%s/%s" % (ts, orig)
                    s2, n, adj, div = inspect(snap)
                    print("    %s  %s  行=%d AdjClose=%s Dividend=%d"
                          % (ts, orig[:52], n, adj, div))
                    time.sleep(2)
                break
        time.sleep(3)


if __name__ == "__main__":
    main()
