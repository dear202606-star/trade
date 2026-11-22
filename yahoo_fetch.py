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
import urllib.parse
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

PERIOD1_EPOCH = 1262304000                      # 2010-01-01: monthly backfill window start
WINDOW_START_MONTH = dt.datetime.fromtimestamp(
    PERIOD1_EPOCH, dt.timezone.utc).strftime("%Y-%m")

# 一轮恰好扫完整个机群：轮数少于通道数时，全局游标可能永远轮不到能返回完整
# 窗口的那条通道（实测同一请求 allorigins 回 203 根、translate 只回 5 根）。
SWEEP_TRIES = len(CHANNELS)


def reaches_window_start(bars):
    """True when a response covers back to the first requested month.

    Keys are ISO `YYYY-MM`, so lexicographic order is chronological. Once a relay
    returns history that starts at the window edge, no other channel can add
    months, and querying more of them only spends egress.
    """
    return bool(bars) and min(bars) <= WINDOW_START_MONTH


class SymbolNotFound(Exception):
    """Yahoo itself answered Not Found and no channel returned a single bar.

    调用方必须能把这种「权威缺席」和机群整体故障分开：前者补不出任何东西，
    每 6 小时重试一次只会白烧流量（65 只退市票就是这样一个循环），后者必须
    继续重试。只有在一无所获时才升级成这个异常——取回过片段的仍交给校验判断。
    """


class _NoData(Exception):
    """Yahoo 在响应体里判定该代码无数据，与通道故障是两种性质。

    状态码指望不上：经 allorigins 实测（2026-11-21 取证 WFM）回的是 HTTP 200 +
    {"chart":{"result":null,"error":{"code":"Not Found",
     "description":"No data found, symbol may be delisted"}}}。旧代码直接对
    result 下标，抛的是 TypeError，被当成通道故障冷却 15 秒继续轮换，于是
    退市票既补不出东西、也永远进不了结案分支。
    """


def _q(sym):
    return ("?period1=%d&period2=%d&interval=1mo&events=div%%2Csplit"
            % (PERIOD1_EPOCH, int(time.time())))


def _parse(text, channel):
    """返回 {YYYY-MM: (adj, raw)}；jina 通道会包一层 markdown，需要截取 JSON"""
    if channel == "jina":
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return {}
        text = m.group(0)
    d = json.loads(text)["chart"]
    if d.get("error"):
        raise _NoData(d["error"].get("code") or "error")
    d = d["result"][0]
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


def _sym(s):
    """Yahoo 代码规范：BRK.B → BRK-B"""
    return s.replace(".", "-")


def fetch(sym, tries=SWEEP_TRIES):
    """轮换取历史，返回覆盖最完整的一次；机群全部失败才返回 {}；
    Yahoo 权威判定不存在且一无所获时抛 SymbolNotFound。

    代理通道经常把 period1/period2 丢掉，只回最近几根月线（实测同一请求
    allorigins 回 203 根、translate 只回 5 根）。旧逻辑 `if out: return out`
    把这种截断当成成功并立刻返回，于是补历史作业每 6 小时取回同样的 5 个月、
    被校验整批判成「月份不足」、下次原样重演，0 行入库。只有先识别「这不是
    完整窗口」，才谈得上判断某只票到底是拿不到还是被截断。

    「截断」有两个来源：代理自己把 period1/period2 丢了，以及我们把参数拼到了
    代理的外层 query 上（Yahoo 因此收到无参请求）——后者见下面拼 URL 处的注释。
    """
    tail = _q(sym)
    best = {}
    absent = False
    for attempt in range(tries):
        name, tpl = CHANNELS[_idx["i"] % len(CHANNELS)]
        _idx["i"] += 1
        if _cooldown.get(name, 0) > time.time():
            continue
        qs = tail[1:] if tail.startswith("?") else tail
        if "?quest=" in tpl or "?url=" in tpl:
            # 内层查询必须整体编码。旧写法把 period1/period2/interval 直接拼在
            # 代理自己的 query 上，Yahoo 收到的是无参 /v8/finance/chart/SYM：
            # codetabs 稳定 400 Bad Request，偶尔也只回默认区间的两三根月线
            # （实测 SATS 2 根、SPLS 11 根，正是 CI 里的「月份不足（2/11）」）。
            url = (tpl % _sym(sym)) + urllib.parse.quote("?" + qs, safe="")
        else:
            url = (tpl % _sym(sym)) + tail
        if name == "translate":
            url += "&_x_tr_sl=en&_x_tr_tl=zh-CN&_x_tr_hl=en"
        try:
            raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=45) \
                .read().decode("utf-8", "replace")
            out = _parse(raw, name)
            if reaches_window_start(out):
                return out                  # 历史已取全，换通道不可能更好
            if len(out) > len(best):
                best = out                  # 截断响应留着，好过空手而归
        except _NoData:
            absent = True
            break
        except Exception as e:
            s = str(e)
            if "429" in s:
                _cooldown[name] = time.time() + 90
            elif "404" in s:
                # Yahoo 自己回答 Not Found（退市票），换通道也不会有；但更早通道
                # 取到的片段仍要交给校验判断，不能退化成「空数据」丢掉证据。
                absent = True
                break
            else:
                _cooldown[name] = time.time() + 15
        time.sleep(1.2)
    if absent and not best:
        raise SymbolNotFound(sym)
    return best


if __name__ == "__main__":
    import sys
    for s in sys.argv[1:] or ["AAPL"]:
        t0 = time.time()
        d = fetch(s)
        print("%-6s %d 个月 %.1fs" % (s, len(d), time.time() - t0))
