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
    """用权威拆股表重算复权价：adj = raw × 拆股累计比例"""
    sp = _splits().get(ticker)
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


def adjust_daily(ticker, bars):
    """把日线 {yyyymmdd: close} 用 splits.json 复权。

    约定与 _reapply 一致：拆股日（含）之后的价格乘以比例，
    使跨拆股的价格序列保持连续。返回 {yyyymmdd: adj_close}。
    """
    sp = _splits().get(ticker)
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
    if bars:
        bars = _reapply(ticker, bars)

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


def load_members(max_age_days=7):
    """标普 500 时点成分表：本地有过期机制，超过 max_age_days 就从网络刷新并回写

    成分股每年约 20-25 次调整（每月 1-2 次），所以快照不能永久冻结。
    """
    import pandas as pd
    for p in (MEMBERS, os.path.join(BASE, "data", "sp500_ticker_start_end.csv")):
        if os.path.exists(p):
            try:
                age = (time.time() - os.path.getmtime(p)) / 86400.0
                if age <= max_age_days:
                    return pd.read_csv(p)
                # 过期：尝试刷新，成功则覆盖本地快照（下次提交即生效）
                for url in ("https://raw.githubusercontent.com/fja05680/sp500/master/"
                            "sp500_ticker_start_end.csv",):
                    try:
                        r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
                        if r.status_code == 200:
                            df = pd.read_csv(StringIO(r.text))
                            if "ticker" in df.columns and "start_date" in df.columns:
                                try:
                                    df.to_csv(p, index=False)
                                except Exception:
                                    pass
                                print("      成分表已刷新（本地快照 %.1f 天，刷新后回写）" % age)
                                return df
                    except Exception:
                        continue
                print("      成分表快照已 %.1f 天，但刷新失败，沿用本地" % age, file=sys.stderr)
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
        sec = dict(zip(sp["Symbol"], sp["GICS Sector"]))
        # 补上已退市/被并购的历史成分股行业（否则行业约束对其失效）
        try:
            ov = json.load(open(os.path.join(BASE, "static", "sector_overrides.json"), encoding="utf-8"))
            for k, v in ov.items():
                if not k.startswith("_"):
                    sec.setdefault(k, v)
        except Exception:
            pass
        return sec, dict(zip(sp["Symbol"], sp["Security"]))
    except Exception:
        return {}, {}


def prefetch(tickers, workers=8):
    """并行预热：CI 里没有本地缓存时，把 500 只票的抓取从串行 5 分钟压到 ~40 秒"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    todo = [t for t in tickers if t not in _mem]
    if not todo:
        return 0
    n = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(load_bars, t): t for t in todo}
        for fu in as_completed(futs):
            try:
                if fu.result():
                    n += 1
            except Exception:
                pass
    return n
