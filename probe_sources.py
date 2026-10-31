# -*- coding: utf-8 -*-
"""probe_sources.py — 并发探测各国主流免费数据源（对美股，含退市票）

目标：找出能否拿到「美股历史价（最好全收益）+ 退市票」的免费源。
所有探测只读、短超时、结果打成一张表。
"""
import concurrent.futures as cf
import io
import json
import sys
import time
import urllib.parse
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
      "Accept": "application/json,text/html,*/*"}
TIMEOUT = 15

# (名称, 国家, URL 模板, 期望特征)
CASES = [
    # ---- 美国 ----
    ("stockanalysis", "US", "https://stockanalysis.com/api/symbol/s/{s}/history?range=10Y&period=Monthly", "a"),
    ("nasdaq", "US", "https://api.nasdaq.com/api/quote/{S}/historical?assetclass=stocks&fromdate=2010-01-01&todate=2026-10-31&limit=9999", "tradesTable"),
    ("stooq", "US/PL", "https://stooq.com/q/d/l/?s={sl}.us&i=m", "Date"),
    ("marketwatch", "US", "https://www.marketwatch.com/investing/stock/{sl}/downloaddatapartial?startdate=01/01/2010&enddate=10/31/2026&daterange=d30&frequency=p1m&csvdownload=true", "Date"),
    ("morningstar", "US", "https://api-global.morningstar.com/sal-service/v1/stock/price/v2/{S}/history?startDate=2010-01-01&endDate=2026-10-31", "history"),
    ("wsj", "US", "https://www.wsj.com/market-data/quotes/{S}/historical-prices/download?MOD=mw_quote&startDate=01/01/2010&endDate=10/31/2026", "Date"),
    # ---- 欧洲 ----
    ("finanzen.net", "DE", "https://www.finanzen.net/api/historical/price?instrument={S}&start=2010-01-01&end=2026-10-31", ""),
    ("onvista", "DE", "https://api.onvista.de/api/v1/instruments/{S}/eod_history?idNotation=&startDate=2010-01-01&endDate=2026-10-31&granularity=1M", ""),
    ("ariva", "DE", "https://www.ariva.de/{sl}-aktie/historische_kurse?boerse_id=1", ""),
    ("marketscreener", "FR", "https://www.marketscreener.com/quote/stock/{S}/history/", ""),
    ("boursorama", "FR", "https://www.boursorama.com/cours/historique/{S}", ""),
    ("investing", "EU", "https://api.investing.com/api/financialdata/historical/6408?start-date=2010-01-01&end-date=2026-10-31&time-frame=Monthly", ""),
    # ---- 日本 ----
    ("yahoo-jp", "JP", "https://finance.yahoo.co.jp/quote/{S}.T/history", ""),
    ("minkabu", "JP", "https://minkabu.jp/stock/{S}/daily_bar", ""),
    ("kabutan", "JP", "https://kabutan.jp/stock/chart?code={S}", ""),
    # ---- 韩国 ----
    ("naver", "KR", "https://finance.naver.com/world/worldDayListJson.naver?symbol={S}&freq=M", ""),
    ("daum", "KR", "https://finance.daum.net/api/quotes/US/{S}/days?page=1&perPage=2000", ""),
    # ---- 中国 ----
    ("eastmoney", "CN", "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=105.{S}&klt=103&fqt=1&beg=20100101&end=20500101&fields1=f1,f2,f3&fields2=f51,f53", ""),
    ("xueqiu", "CN", "https://stock.xueqiu.com/v5/stock/chart/kline.json?symbol={S}&begin={ms}&period=month&type=before&count=-260", ""),
]

TICKERS_ACTIVE = ["AAPL"]
TICKERS_DELISTED = ["ATVI"]


def probe(name, country, tpl, feat, sym):
    url = (tpl.replace("{S}", sym).replace("{sl}", sym.lower())
              .replace("{ms}", str(int(time.time() * 1000))))
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read(4000).decode("utf-8", "replace")
        ok = (feat in body) if feat else (len(body) > 200 and "<html" not in body[:200].lower())
        return (name, country, sym, r.status, len(body), "OK" if ok else "no-match",
                round(time.time() - t0, 1))
    except Exception as e:
        return (name, country, sym, "-", 0, str(e)[:42], round(time.time() - t0, 1))


def main():
    jobs = [(n, c, t, f, s) for (n, c, t, f) in CASES
            for s in TICKERS_ACTIVE + TICKERS_DELISTED]
    rows = []
    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        for r in ex.map(lambda a: probe(*a), jobs):
            rows.append(r)
    rows.sort(key=lambda x: (x[0], x[2]))
    print("%-14s %-6s %-6s %-5s %7s %-12s %s" % ("source", "国家", "标的", "HTTP", "字节", "判定", "耗时"))
    for r in rows:
        print("%-14s %-6s %-6s %-5s %7d %-12s %ss" % r)


if __name__ == "__main__":
    main()
