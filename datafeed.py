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
_SPLITS = None


def _splits():
    """载入入库的权威拆股表，保证本地与 CI 用同一张表"""
    global _SPLITS
    if _SPLITS is None:
        _SPLITS = {}
        for p in (os.path.join(BASE, "output", "splits.json"),):
            if os.path.exists(p):
                try:
                    _SPLITS = json.load(open(p, encoding="utf-8"))
                except Exception:
                    _SPLITS = {}
    return _SPLITS


def _reapply(ticker, bars):
    """用权威拆股表重算复权价：adj = raw × 拆股累计比例（剔除伪拆股）"""
    raw_map = {k: (v[1] if isinstance(v, (tuple, list)) else v)
               for k, v in (bars or {}).items()}
    sp = _valid_splits(ticker, raw_map)
    if not sp or not bars:
        return bars
    out = {}
    for ym, (adj, raw) in bars.items():
        try:
            import pandas as pd
            me = pd.Timestamp(ym + "-01") + pd.offsets.MonthEnd(0)
            ts = int(me.timestamp()) + 86399
            f = 1.0
            for sd, ratio in sp:
                if int(sd) <= ts:
                    f *= float(ratio)
            out[ym] = (raw * f, raw)
        except Exception:
            out[ym] = (adj, raw)
    return out
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


def _valid_splits(ticker, bars):
    """返回 [(ts, factor)]：factor 直接乘在「拆股日及之后」的原始价上，
    使序列连续。方向由原始价的实际跳变决定（见模块 docstring）。"""
    sp = _splits().get(ticker)
    if not sp or not bars:
        return []

    def _px(v):
        if isinstance(v, (tuple, list)):
            for x in reversed(v):
                if isinstance(x, (int, float)) and x > 0:
                    return float(x)
            return None
        return float(v) if isinstance(v, (int, float)) and v > 0 else None

    import datetime as _dt
    days = sorted(bars)
    keep = []
    for ts, ratio in sp:
        try:
            md = _dt.datetime.utcfromtimestamp(int(ts)).strftime("%Y%m%d")
            r = float(ratio)
        except Exception:
            continue
        if r <= 0:
            continue
        if r < 1:
            r = 1.0 / r
        if r <= 1.0001:
            continue
        before = [x for x in (_px(bars[d]) for d in days if d < md) if x][-20:]
        after = [x for x in (_px(bars[d]) for d in days if d >= md) if x][:20]
        if not before or not after:
            continue
        b = sorted(before)[len(before) // 2]
        a = sorted(after)[len(after) // 2]
        if not b or b <= 0 or not a:
            continue
        jump = a / b
        # 真跌：a ≈ b/r；真涨：a ≈ b*r；容差 25%
        if abs(jump - 1.0 / r) <= 0.25 * (1.0 / r):
            keep.append((ts, r))          # post × r  → 回到拆股前水平
        elif abs(jump - r) <= 0.25 * r:
            keep.append((ts, 1.0 / r))    # post ÷ r  → 回到拆股前水平
        # 其它情况：不是拆股（或数据已损坏）→ 不处理
    return keep


def adjust_daily(ticker, bars):
    """把日线 {yyyymmdd: close} 用 splits.json 复权。

    约定与 _reapply 一致：拆股日（含）之后的价格乘以比例，
    使跨拆股的价格序列保持连续。返回 {yyyymmdd: adj_close}。
    """
    sp = _valid_splits(ticker, bars)
    if not sp or not bars:
        return bars
    import datetime as _dt
    marks = []
    for ts, ratio in sp:
        try:
            marks.append((_dt.datetime.utcfromtimestamp(int(ts)).strftime("%Y%m%d"), float(ratio)))
        except Exception:
            continue
    if not marks:
        return bars
    out = {}
    for d, px in bars.items():
        f = 1.0
        for md, ratio in marks:
            if md <= d:
                f *= ratio
        out[d] = px * f
    return out


_ALL_BARS = None


def _db_all():
    """在线库（DB 或 REST）一次性载入；失败返回 None"""
    global _ALL_BARS
    if _ALL_BARS is None:
        try:
            import db_prices
            _ALL_BARS = db_prices.load_all()
        except Exception as e:
            print("⚠ datafeed 在线库不可用：%s" % str(e)[:60], flush=True)
            _ALL_BARS = {}
    return _ALL_BARS


def load_bars(ticker):
    """Read selected online-database prices; never use file or provider fallbacks."""
    bars = _db_all().get(ticker)
    if not bars:
        raise RuntimeError("Online database has no selected prices: %s" % ticker)
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


def load_members(max_age_days=7):
    """Online reference data; retain the argument for existing callers."""
    import pandas as pd
    import db_ref
    members = db_ref.load_members()
    return pd.DataFrame([{"ticker": ticker, "start_date": start, "end_date": end}
                         for ticker, periods in members.items() for start, end in periods])


def load_sector():
    """Read sector constraints online; absent company names display tickers."""
    import db_ref
    sectors = db_ref.load_sectors()
    return sectors, {ticker: ticker for ticker in sectors}


def prefetch(tickers, workers=8):
    """Warm one online snapshot; retain workers for existing callers."""
    requested = set(tickers)
    if not requested:
        return 0
    bars = _db_all()
    if not bars:
        raise RuntimeError('Online prefetch returned no selected prices')
    return sum(bool(bars.get(ticker)) for ticker in requested)
