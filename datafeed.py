# -*- coding: utf-8 -*-
"""datafeed.py — 统一数据入口，保证「本地有的，CI 也一定有」

问题：data/ 目录被 .gitignore 排除，CI 里没有本地缓存，
      依赖本地缓存的脚本在 CI 会直接失败或产出空数据。

三级取数，任何环境都能跑：
  1. 本地缓存 data/sina2/{ticker}.json        （最快，本地）
  2. 同一次运行的临时面板 output/.panel.json   （monthly_pick 顺手导出，CI 复用）
  3. 实时抓新浪日线                            （兜底，任何环境）
"""
import json
import os
import re
import time
from io import StringIO

import requests

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "data", "sina2")
PANEL = os.path.join(BASE, "output", ".panel.json")
MEMBERS = os.path.join(BASE, "static", "sp500_members.csv")
SP500 = os.path.join(BASE, "data", "sp500.csv")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
PAT = re.compile(r"\(\s*(\[.*\])\s*\)", re.S)

_mem = {}
_panel = None


def _load_panel():
    global _panel
    if _panel is None:
        _panel = {}
        if os.path.exists(PANEL):
            try:
                _panel = json.load(open(PANEL, encoding="utf-8"))
            except Exception:
                _panel = {}
    return _panel


def fetch_sina_bars(ticker):
    """实时抓：返回 {ym: (adj, raw)}，用市场数据层同一套拆股逻辑"""
    try:
        import market_data as md
        rows, _src = md.get_history(ticker, dual=False, yahoo_ok=False)
        if rows:
            return {r[0]: (r[1], r[2]) for r in rows}
    except Exception:
        pass
    return None


def load_bars(ticker):
    """统一入口：本地缓存 → 运行面板 → 实时抓取"""
    if ticker in _mem:
        return _mem[ticker]
    bars = None

    p = os.path.join(CACHE, ticker + ".json")
    if os.environ.get("DSH_NO_CACHE"):
        p = ""
    if p and os.path.exists(p):
        try:
            d = json.load(open(p, encoding="utf-8"))
            b = d.get("bars") or []
            if b:
                bars = {x[0]: (x[1], x[2]) for x in b}
        except Exception:
            bars = None

    if bars is None:
        pn = _load_panel().get(ticker)
        if pn:
            bars = {x[0]: (x[1], x[2]) for x in pn}

    if bars is None:
        bars = fetch_sina_bars(ticker)

    _mem[ticker] = bars
    return bars


def save_panel(panel_dict):
    """monthly_pick 调用：把已抓好的面板写盘，供同一次运行的其它脚本复用"""
    try:
        os.makedirs(os.path.dirname(PANEL), exist_ok=True)
        out = {}
        for tk, df in panel_dict.items():
            rows = [[str(i), float(r["adj"]), float(r["raw"])] for i, r in df.iterrows()]
            out[tk] = rows
        with open(PANEL, "w", encoding="utf-8") as f:
            json.dump(out, f, separators=(",", ":"))
        return os.path.getsize(PANEL)
    except Exception:
        return 0


def load_members():
    """标普 500 时点成分表：本地优先，CI 从 GitHub 取"""
    import pandas as pd
    for p in (MEMBERS, os.path.join(BASE, "data", "sp500_ticker_start_end.csv")):
        if os.path.exists(p):
            try:
                return pd.read_csv(p)
            except Exception:
                pass
    import pandas as pd
    for url in ("https://raw.githubusercontent.com/fja05680/sp500/master/"
                "sp500_ticker_start_end.csv",
                "https://raw.githubusercontent.com/fja05680/sp500/master/sp500.csv"):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
            if r.status_code == 200:
                df = pd.read_csv(StringIO(r.text))
                if "ticker" in df.columns and "start_date" in df.columns:
                    return df
        except Exception:
            continue
    return None


def load_sector():
    """行业分类：本地优先，CI 从 GitHub 取；都拿不到就返回空（按等权处理）"""
    import pandas as pd
    try:
        if os.path.exists(SP500):
            sp = pd.read_csv(SP500)
        else:
            r = requests.get("https://raw.githubusercontent.com/fja05680/sp500/master/sp500.csv",
                             headers={"User-Agent": UA}, timeout=30)
            sp = pd.read_csv(StringIO(r.text))
        return dict(zip(sp["Symbol"], sp["GICS Sector"])), dict(zip(sp["Symbol"], sp["Security"]))
    except Exception:
        return {}, {}
