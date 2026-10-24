# -*- coding: utf-8 -*-
"""market_data.py — 多源行情获取 + 交叉校验

优先级：Yahoo Finance（国外，含部分退市股）→ Sina（国内，全历史）
校验：月份连续、价格序列无异常跳变、最新数据不过期、≥13 个月
返回统一结构：[[ym, adj_close, raw_close, month_volume], ...]
"""
import json
import os
import time
import re
import datetime as dt

import requests
import pandas as pd

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
NICE = [1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 25, 30, 40, 50, 100]
PAT = re.compile(r"\(\s*(\[.*\])\s*\)", re.S)

STATS = {"yahoo": 0, "sina": 0, "fail": 0}


def _nice(k, tol=0.04):
    best = min(NICE, key=lambda z: abs(z - k))
    return best if abs(best - k) / k <= tol else None


# ---------------------------------------------------------------- Yahoo
def fetch_yahoo(tk, years=26):
    now = int(time.time())
    start = now - int(years * 365.25 * 86400)
    url = (f"https://query2.finance.yahoo.com/v8/finance/chart/{tk}"
           f"?period1={start}&period2={now}&interval=1mo&events=div%2Csplit")
    r = requests.get(url, headers={"User-Agent": UA}, timeout=8)
    if r.status_code != 200:
        return None
    j = r.json()
    res = (j.get("chart") or {}).get("result") or []
    if not res:
        return None
    res = res[0]
    ts = res.get("timestamp") or []
    ind = res.get("indicators") or {}
    q = (ind.get("quote") or [{}])[0]
    adj = ((ind.get("adjclose") or [{}])[0]).get("adjclose") or q.get("close") or []
    close = q.get("close") or []
    vol = q.get("volume") or []
    if len(ts) < 13:
        return None
    out = []
    for i, t in enumerate(ts):
        try:
            c = close[i]
            a = adj[i] if i < len(adj) and adj[i] is not None else c
            v = vol[i] if i < len(vol) and vol[i] is not None else 0
        except Exception:
            continue
        if c is None or a is None or c <= 0:
            continue
        d = dt.datetime.utcfromtimestamp(t)
        ym = d.strftime("%Y-%m")
        out.append([ym, float(a), float(c), float(v)])
    # 同月取最后一个交易日
    ded = {}
    for row in out:
        ded[row[0]] = row
    return [ded[k] for k in sorted(ded)]


# ---------------------------------------------------------------- Sina
def fetch_sina(tk):
    url = ("https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/x/"
           f"US_MinKService.getDailyK?symbol={tk}")
    r = requests.get(url, headers={"User-Agent": UA,
                                   "Referer": "https://finance.sina.com.cn"}, timeout=30)
    m = PAT.search(r.text)
    if not m:
        return None
    arr = json.loads(m.group(1))
    if not arr:
        return None
    out, factor = [], 1.0
    prev_c = prev_v = None
    cur_ym = cur_adj = cur_raw = None
    cur_v = 0.0
    for x in arr:
        try:
            d, c = x["d"], float(x["c"])
            v = float(x.get("v") or 0)
            o = float(x.get("o") or 0)
        except Exception:
            continue
        if c <= 0 or not d[:4].isdigit():
            continue
        if prev_c and prev_c > 0:
            K = None
            if o > 0:
                k1 = prev_c / o
                if k1 >= 1.45 or k1 <= 0.69:
                    nk = _nice(k1 if k1 >= 1 else 1 / k1)
                    if nk:
                        K = nk if k1 >= 1 else 1.0 / nk
            if K is None:
                k2 = prev_c / c
                if k2 >= 1.45 or k2 <= 0.69:
                    nk = _nice(k2 if k2 >= 1 else 1 / k2, tol=0.025)
                    if nk:
                        K = nk if k2 >= 1 else 1.0 / nk
            if K is not None and prev_v and prev_v > 0:
                if 0.25 <= (v / prev_v) / K <= 4.0:
                    factor *= K
        ym = d[:7]
        if ym != cur_ym:
            if cur_ym:
                out.append([cur_ym, cur_adj, cur_raw, cur_v])
            cur_ym, cur_v = ym, 0.0
        cur_adj, cur_raw = c * factor, c
        cur_v += v
        prev_c, prev_v = c, v
    if cur_ym:
        out.append([cur_ym, cur_adj, cur_raw, cur_v])
    return out


# ---------------------------------------------------------------- 校验
def validate(rows, max_age_days=10):
    if not rows or len(rows) < 13:
        return False, "月份不足"
    ym = [r[0] for r in rows]
    if len(set(ym)) != len(ym):
        return False, "月份重复"
    for r in rows[-12:]:
        if not (r[1] and r[1] > 0 and r[2] and r[2] > 0):
            return False, "价格非正"
    last = pd.Timestamp(ym[-1] + "-01") + pd.offsets.MonthEnd(0)
    today = pd.Timestamp(dt.date.today())
    if (today - last).days > max_age_days + 31:
        return False, f"数据过期 {ym[-1]}"
    tail = [r[1] for r in rows[-13:]]
    for a, b in zip(tail, tail[1:]):
        if b / a > 8 or b / a < 1 / 8:
            return False, "价格异常跳变"
    return True, "ok"


_YAHOO_OK = None


def yahoo_available():
    """探测 Yahoo 是否可达（国内网络会被墙），只探一次"""
    global _YAHOO_OK
    if _YAHOO_OK is None:
        try:
            _YAHOO_OK = fetch_yahoo("AAPL") is not None
        except Exception:
            _YAHOO_OK = False
        print(f"      Yahoo 可用: {_YAHOO_OK}", flush=True)
    return _YAHOO_OK


def get_history(tk, prefer=None):
    """按优先级取数，返回 (rows, source)"""
    if prefer is None:
        # 主源固定新浪：回测（29.4% 那套数字）就是新浪数据算的，
        # 实盘信号必须同源，否则同一策略换个数据源会给出不同答案。
        # Yahoo 只做兜底（新浪失败时）与退市股补充。
        prefer = ("sina", "yahoo") if yahoo_available() else ("sina",)
    for src in prefer:
        try:
            rows = fetch_yahoo(tk) if src == "yahoo" else fetch_sina(tk)
        except Exception:
            rows = None
        if rows:
            ok, why = validate(rows)
            if ok:
                STATS[src] = STATS.get(src, 0) + 1
                return rows, src
    STATS["fail"] += 1
    return None, None


def reset_stats():
    for k in STATS:
        STATS[k] = 0
