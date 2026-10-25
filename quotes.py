# -*- coding: utf-8 -*-
"""quotes.py — 行情快照采集（服务端批量，供静态站同源读取）

为什么不用浏览器直连：免费行情源普遍不开 CORS，浏览器会被拦。
这里在 GitHub Actions 里批量抓，写成同源 JSON，前端直接 fetch，零 key、零成本。

覆盖范围：
  - 标普 500 全部成分股（用户可能持有任意一只）
  - 策略正式持仓 + 当月前 60 名
  - 常见 ETF / 大盘指数

产物：docs/data/quotes.json  {ts, fx, quotes:{TICKER:{p,pc,chg,vol,name}}, stale:[...]}
"""
import json
import os
import re
import sys
import time
import datetime as dt
from io import StringIO

import requests
import pandas as pd

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
SINA_HQ = "https://hq.sinajs.cn/list="
BATCH = 60
OUT = os.path.join("docs", "data", "quotes.json")
LINE = re.compile(r'hq_str_(\w+)="([^"]*)"')

EXTRA = ["SPY", "QQQ", "DIA", "IWM", "VTI", "GLD", "TLT", "AAPL", "MSFT", "NVDA",
         "GOOGL", "AMZN", "META", "TSLA", "BRK.B", "AVGO", "JPM", "V", "UNH", "XOM"]


def sina_batch(symbols):
    url = SINA_HQ + ",".join(symbols)
    r = requests.get(url, headers={"User-Agent": UA,
                                   "Referer": "https://finance.sina.com.cn"}, timeout=15)
    r.encoding = "gbk"
    out = {}
    for m in LINE.finditer(r.text):
        if m.group(2):
            out[m.group(1)] = m.group(2).split(",")
    return out


def parse_us(f):
    """新浪美股：名称,现价,涨跌幅%,时间,涨跌额,开盘,最高,最低,52周高,52周低,成交量,..."""
    try:
        price = float(f[1])
        chg = float(f[2])
        if price <= 0:
            return None
        vol = float(f[10]) if len(f) > 10 and f[10] else 0
        prev = price / (1 + chg / 100) if chg != -100 else price
        return {"p": round(price, 4), "pc": round(prev, 4),
                "chg": round(chg / 100, 6), "vol": vol, "name": f[0]}
    except Exception:
        return None


def fetch_quotes(tickers):
    quotes = {}
    syms = [("gb_" + t.replace(".", "$").lower(), t) for t in tickers]
    for i in range(0, len(syms), BATCH):
        chunk = syms[i:i + BATCH]
        try:
            raw = sina_batch([c[0] for c in chunk])
        except Exception:
            raw = {}
        for sym, tk in chunk:
            if sym in raw:
                q = parse_us(raw[sym])
                if q:
                    quotes[tk] = q
        time.sleep(0.2)
    stale = [t for t in tickers if t not in quotes]
    return quotes, stale


def fetch_fx():
    try:
        raw = sina_batch(["fx_susdcny"])
        f = raw.get("fx_susdcny")
        if f:
            for v in f:
                try:
                    x = float(v)
                    if 5 < x < 10:
                        return round(x, 4)
                except Exception:
                    continue
    except Exception:
        pass
    return None


def universe():
    """标普 500 全成分 + 额外常见标的"""
    tk = set()
    try:
        r = requests.get("https://raw.githubusercontent.com/fja05680/sp500/master/sp500.csv",
                         headers={"User-Agent": UA}, timeout=25)
        df = pd.read_csv(StringIO(r.text))
        tk |= {str(t).strip() for t in df["Symbol"]}
    except Exception as e:
        print("WARN: 成分股列表获取失败", e, file=sys.stderr)

    # 本地有就再补一份（离线兜底）
    for p in ("data/sp500.csv", "output/current_picks_full.csv"):
        if os.path.exists(p):
            try:
                df = pd.read_csv(p)
                col = "Symbol" if "Symbol" in df.columns else (
                    "ticker" if "ticker" in df.columns else df.columns[0])
                tk |= {str(t).strip() for t in df[col]}
            except Exception:
                pass

    # 策略正式持仓（可能不在当月前 60 名里）
    if os.path.exists("output/signal_history.json"):
        try:
            h = json.load(open("output/signal_history.json", encoding="utf-8"))
            for p in (h[0].get("official") or []) + (h[0].get("picks") or []):
                tk.add(p["ticker"])
            for p in h[0].get("bench", []):
                tk.add(p["ticker"])
        except Exception:
            pass

    tk |= set(EXTRA)
    return sorted(t for t in tk if t)


def last_session(now_utc=None):
    """美东时间最后一个已收盘的交易日（YYYY-MM-DD）

    行情是「最新一笔」，收盘后到下一个开盘前，它反映的是上一交易日的涨跌。
    界面必须写清楚是哪一天，否则用户会以为那是「今天」。
    """
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    et = now_utc - dt.timedelta(hours=4)          # 夏令时 EDT=UTC-4（ approximations ）
    d = et.date()
    # 未到美东 16:00 收盘 → 用前一交易日
    if et.hour < 16:
        d -= dt.timedelta(days=1)
    while d.weekday() >= 5:                        # 跳过周末
        d -= dt.timedelta(days=1)
    return d.isoformat()


def main():
    tickers = universe()
    quotes, stale = fetch_quotes(tickers)
    fx = fetch_fx()
    payload = {
        "ts": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ts_local": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "session": last_session(),
        "fx": fx,
        "count": len(quotes),
        "universe": len(tickers),
        "coverage": round(len(quotes) / max(len(tickers), 1), 4),
        "ok": len(quotes) >= 0.98 * max(len(tickers), 1),
        "stale": stale[:30],
        "quotes": quotes,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(f"行情 {len(quotes)} / {len(tickers)}，汇率 {fx}，缺失 {len(stale)}")
    if len(quotes) < 0.8 * len(tickers):
        print("WARN: 行情覆盖率过低", file=sys.stderr)


if __name__ == "__main__":
    main()
