# -*- coding: utf-8 -*-
"""probe_all_sources.py — 穷举式探测：退市票到底还能从哪拿到合格数据

判定「合格」两条：① 覆盖 2010-2025；② 有全收益口径（或能由分红推出）。
本脚本只做探测与打分，不写库。

覆盖的候选（含冷门）：
  A. 免费 API 家族：FMP / TwelveData / Polygon / Tiingo / Marketstack / Finnhub /
     EODHD / AlphaVantage / stockanalysis / Nasdaq / Stooq / Yahoo
  B. 存档与镜像：Wayback Machine（Yahoo/Google Finance 历史页快照）、
     Google Finance、investing 镜像、Barchart、WSJ、FT、Morningstar
  C. 数据集仓库：GitHub 上的 S&P500 历史数据仓库、HuggingFace datasets
  D. 学术/公共：SEC EDGAR（无价）、FRED（无个股）、Nasdaq Trader（无价）
"""
import json
import sys
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
      "Accept": "application/json,text/html,*/*", "Accept-Language": "en-US,en;q=0.9"}

DELISTED = ["ATVI", "ETFC", "XL", "AABA", "CERN"]
ACTIVE = ["AAPL"]


def get(url, timeout=25, headers=None):
    h = dict(UA)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def probe(name, url, sym, parser=None, headers=None):
    """返回 (状态, 说明)"""
    try:
        st, body = get(url, headers=headers)
    except urllib.error.HTTPError as e:
        return name, "HTTP %d" % e.code
    except Exception as e:
        return name, "ERR " + str(e)[:40]
    if parser:
        try:
            return name, parser(body)
        except Exception as e:
            return name, "PARSE " + str(e)[:30]
    return name, "HTTP %d (%d B)" % (st, len(body))


def p_json_keys(*keys):
    def f(body):
        d = json.loads(body)
        for k in keys:
            d = d.get(k) if isinstance(d, dict) else None
            if d is None:
                return "无字段 " + k
        return "ok %d 行" % (len(d) if hasattr(d, "__len__") else 0)
    return f


def p_count(sub):
    def f(body):
        t = body.decode("utf-8", "replace")
        return "ok 含 %r ×%d (%d B)" % (sub, t.count(sub), len(t))
    return f


def main():
    for sym in DELISTED[:3] + ACTIVE:
        print("\n===== %s =====" % sym)
        cands = [
            # A 免费 API
            ("fmp-demo", "https://financialmodelingprep.com/api/v3/historical-price-full/%s?apikey=demo" % sym,
             p_json_keys("historical")),
            ("twelvedata", "https://api.twelvedata.com/time_series?symbol=%s&interval=1month&outputsize=5000&apikey=demo" % sym,
             p_json_keys("values")),
            ("polygon", "https://api.polygon.io/v2/aggs/ticker/%s/range/1/month/2010-01-01/2025-12-31?apiKey=" % sym,
             p_json_keys("results")),
            ("eodhd-demo", "https://eodhd.com/api/eod/%s.US?api_token=demo&fmt=json&period=m" % sym, p_count("date")),
            ("stockanalysis", "https://stockanalysis.com/stocks/%s/history/" % sym.lower(), p_count("Dividend")),
            ("stooq", "https://stooq.com/q/d/l/?s=%s.us&i=m" % sym.lower(), p_count(",")),
            ("nasdaq", "https://api.nasdaq.com/api/quote/%s/historical?assetclass=stocks&fromdate=2010-01-01&todate=2025-12-31&limit=9999" % sym,
             p_count("date")),
            # B 存档与镜像
            ("wayback-yahoo", "http://archive.org/wayback/available?url=finance.yahoo.com/quote/%s/history" % sym,
             p_count("closest")),
            ("wayback-cdx", "http://web.archive.org/cdx/search/cdx?url=finance.yahoo.com/quote/%s/history*&output=json&limit=5" % sym,
             p_count("timestamp")),
            ("barchart", "https://www.barchart.com/stocks/quotes/%s/price-history/monthly" % sym, p_count("Price")),
            ("wsj", "https://www.wsj.com/market-data/quotes/%s/historical-prices/download?MOD=mw_quote&startDate=01/01/2010&endDate=12/31/2025" % sym,
             p_count(",")),
            ("morningstar", "https://tools.morningstar.com/api/v2/rest/security/quote/%s" % sym, p_count("price")),
            # C 数据集仓库
            ("gh-datasets", "https://raw.githubusercontent.com/datasets/s-and-p-500/main/data/data.csv", p_count(",")),
            ("hf-datasets", "https://huggingface.co/api/datasets?search=sp500", p_count("id")),
        ]
        for name, url, parser in cands:
            n, msg = probe(name, url, sym, parser)
            print("  %-16s %s" % (n, msg))


if __name__ == "__main__":
    main()
