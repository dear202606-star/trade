# -*- coding: utf-8 -*-
"""yahoo_fetch.py — Yahoo 多中转轮换抓取（含冷却与退避）

背景：直接访问被地区封锁；translate.goog 中转可用但会 429。
做法：维护 3 条通道，按顺序轮换；某通道 429 则冷却 90s 并切下一条。
输出与 relay_source.fetch_yahoo_relay 一致：{YYYY-MM: (adj, raw)}
"""
import datetime as dt
import json
import re
import time
import urllib.request

UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "Chrome/130 Safari/537.36")}

CHANNELS = [
    ("translate", "https://query1-finance-yahoo-com.translate.goog/v8/finance/chart/%s"),
    ("jina", "https://r.jina.ai/https://query1.finance.yahoo.com/v8/finance/chart/%s"),
    ("codetabs", "https://api.codetabs.com/v1/proxy?quest=https://query1.finance.yahoo.com/v8/finance/chart/%s"),
    ("allorigins", "https://api.allorigins.win/raw?url=https://query1.finance.yahoo.com/v8/finance/chart/%s"),
    ("direct", "https://query1.finance.yahoo.com/v8/finance/chart/%s"),
]
_cooldown = {}
_idx = {"i": 0}


def _q(sym):
    return ("?period1=1262304000&period2=%d&interval=1mo&events=div%%2Csplit" % int(time.time()))


def _parse(text, channel):
    """返回 {YYYY-MM: (adj, raw)}；jina 通道会包一层 markdown，需要截取 JSON"""
    if channel == "jina":
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return {}
        text = m.group(0)
    d = json.loads(text)["chart"]["result"][0]
    ts = d.get("timestamp") or []
    adj = (d["indicators"].get("adjclose") or [{}])[0].get("adjclose") or []
    close = (d["indicators"].get("quote") or [{}])[0].get("close") or []
    out = {}
    for i, x in enumerate(ts):
        ym = dt.datetime.fromtimestamp(x, dt.timezone.utc).strftime("%Y-%m")
        a = adj[i] if i < len(adj) else None
        c = close[i] if i < len(close) else None
        if a:
            out[ym] = (float(a), float(c) if c else None)
    return out


def fetch(sym, tries=4):
    """按通道轮换抓取；全部失败返回 {}"""
    tail = _q(sym)
    for attempt in range(tries):
        name, tpl = CHANNELS[_idx["i"] % len(CHANNELS)]
        _idx["i"] += 1
        if _cooldown.get(name, 0) > time.time():
            continue
        qs = tail[1:] if tail.startswith("?") else tail
        if "?quest=" in tpl or "?url=" in tpl:
            import urllib.parse as _up
            url = (tpl % sym) + "&" + qs if "?quest=" in tpl else \
                  (tpl % sym) + _up.quote("?" + qs, safe="")
        else:
            url = (tpl % sym) + tail
        if name == "translate":
            url += "&_x_tr_sl=en&_x_tr_tl=zh-CN&_x_tr_hl=en"
        try:
            raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=45) \
                .read().decode("utf-8", "replace")
            out = _parse(raw, name)
            if out:
                return out
        except Exception as e:
            s = str(e)
            if "429" in s:
                _cooldown[name] = time.time() + 90
            elif "404" in s:
                return {}                     # 退市票，换通道也没用
            else:
                _cooldown[name] = time.time() + 15
        time.sleep(1.2)
    return {}


if __name__ == "__main__":
    import sys
    for s in sys.argv[1:] or ["AAPL"]:
        t0 = time.time()
        d = fetch(s)
        print("%-6s %d 个月 %.1fs" % (s, len(d), time.time() - t0))
