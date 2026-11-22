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
import urllib.error
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


class _Malformed(Exception):
    """通道答了话，正文里却没有可解析的 chart 形状（限流页、代理错误页）。

    它必须记成「问过但没答上」而不是「答了个空」：后者会被调用方读成源里没有
    这段历史，于是限流页面有机会洗成权威缺席。result 为 null 而 error 也没说话
    的同一种响应，同样归到这里——沉默不是否认。
    """


class Bars(dict):
    """{YYYY-MM: (adj, raw)} + 这一轮机群到底有没有被真正问过。

    调用方原来只看「有没有根」，于是两种相反的形状完全同形：5 条通道都在冷却
    （一次请求都没发出去）与源答了话、答案就是空。前者过 90 秒就能重问，后者
    重试一万次也不多一根。计数跟着结果走，红和绿才说得出自己的依据。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.attempts = 0         # 真正发出的请求数（被冷却跳过的通道不算问过）
        self.answered = 0         # 其中答回可解析 chart 正文的次数
        self.first_trade = None   # 源自述的首个交易日 'YYYY-MM'，空答案也带着它


def _q(sym):
    return ("?period1=%d&period2=%d&interval=1mo&events=div%%2Csplit"
            % (PERIOD1_EPOCH, int(time.time())))


def _parse(text, channel):
    """返回 Bars({YYYY-MM: (adj, raw)})；jina 通道会包一层 markdown，需要截取 JSON"""
    out = Bars()
    if channel == "jina":
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise _Malformed("%s 正文里没有 JSON（%d 字节）" % (channel, len(text)))
        text = m.group(0)
    d = json.loads(text)["chart"]
    if d.get("error"):
        raise _NoData(d["error"].get("code") or "error")
    result = d.get("result")
    if not result:
        raise _Malformed("result 为空而 error 也没说话")
    d = result[0]
    first = (d.get("meta") or {}).get("firstTradeDate")
    if first:
        # 一根没回时也留得下这句话：TGNA（2026-11-22 取证）三条通道齐刷刷回 200 +
        # 空 timestamp + firstTradeDate=2026-03，而库内那个身份 2017-06 就离场了。
        # 源自述的首个交易日就是肯定式证据，不必等它先吐几根出来。
        out.first_trade = dt.datetime.fromtimestamp(
            first, dt.timezone.utc).strftime("%Y-%m")
    ts = d.get("timestamp") or []
    ind = d.get("indicators") or {}
    adj = (ind.get("adjclose") or [{}])[0].get("adjclose") or []
    close = (ind.get("quote") or [{}])[0].get("close") or []
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


def build_url(name, tpl, sym, tail):
    """一条通道实际会被打到的 URL（fetch 与逐通道取证脚本共用，避免探针自创口径）。

    代理型通道必须把内层查询整体编码。旧写法把 period1/period2/interval 直接拼在
    代理自己的 query 上，Yahoo 收到的是无参 /v8/finance/chart/SYM，codetabs 因此
    稳定 400 Bad Request。
    """
    qs = tail[1:] if tail.startswith("?") else tail
    if "?quest=" in tpl or "?url=" in tpl:
        url = (tpl % _sym(sym)) + urllib.parse.quote("?" + qs, safe="")
    else:
        url = (tpl % _sym(sym)) + tail
    if name == "translate":
        url += "&_x_tr_sl=en&_x_tr_tl=zh-CN&_x_tr_hl=en"
    return url


def yahoo_denies(exc):
    """这条 404 是不是 Yahoo 亲口说的「查无此码」。

    只有正文是 Yahoo 自己的 chart 信封、且 error 说话时才算裁决。代理自己回的 404
    （Google 翻译拒绝转发、codetabs 的错误页）是通道故障：结案是 append-only 的，
    拿一条网关的错误页去永久逐出一只票，等于让网关替供应商说话。

    2026-11-22 逐通道取证（CI run 45029255176）：WPX/MMC/DNB/IGT/SWN/BIG 的 404
    正文确实出自 Yahoo——direct 与 translate 同形回 "No data found, symbol may be
    delisted"，同一时刻 AAPL 在两条通道都回 204 根。所以现有台账站得住，这条判据
    守的是将来那种「只有代理在说话」的 404。读不出正文就按故障处理，结案不许取这种证据。
    """
    try:
        chart = json.loads(exc.read().decode("utf-8", "replace"))["chart"]
    except Exception:
        return False
    return bool(chart.get("error"))


def fetch(sym, tries=SWEEP_TRIES):
    """轮换取历史，返回覆盖最完整的一次；机群全部失败才返回空 Bars；
    Yahoo 权威判定不存在且一无所获时抛 SymbolNotFound。

    旧逻辑 `if out: return out` 把任何非空响应当成终点：某条通道 400、超时或只回
    最近几根月线时，更好的通道根本不会被问起，补历史作业于是每轮取回同样的几个月、
    被校验整批判成「月份不足」，0 行入库。现在只在覆盖回窗口起点时才提前返回，否则
    一轮扫完机群取最完整的（通道差异是真的：同一请求 translate 回 AAPL 204 根，而
    本机实测 codetabs 超时、allorigins 500、direct 403）。

    「只回几根」还有第二种含义，必须与通道故障分开：2026-11-22 实测 SATS 回 3 根、
    BBBY 回 5 根，meta.firstTradeDate 分别是 2026-10 与 2026-07——重新上市后的历史
    本来就这么长，源里没有，换任何通道都不会更多，靠重试永远补不出来。

    「一根都没回」同样有两种相反的含义，所以结果必须自带 attempts/answered/first_trade
    才交得出手：冷却命中的通道是被 continue 跳过的，整群冷却时一次请求都没发出去，
    那种「空」我们根本没资格解释；而 2026-11-22 取证 TGNA 有 3 条通道答话、回的是
    200 + 空 timestamp + firstTradeDate=2026-03，那种空是源在说话。
    """
    tail = _q(sym)
    best = Bars()
    absent = False
    answered = 0
    for attempt in range(tries):
        name, tpl = CHANNELS[_idx["i"] % len(CHANNELS)]
        _idx["i"] += 1
        if _cooldown.get(name, 0) > time.time():
            continue
        url = build_url(name, tpl, sym, tail)
        best.attempts += 1
        try:
            raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=45) \
                .read().decode("utf-8", "replace")
            out = _parse(raw, name)
        except _NoData:
            absent = True
            answered += 1                 # 答话了，而且答的是「查无此码」
            break
        except urllib.error.HTTPError as e:
            if e.code == 404 and yahoo_denies(e):
                # Yahoo 亲口回答 Not Found（退市票），换通道也不会有；但更早通道
                # 取到的片段仍要交给校验判断，不能退化成「空数据」丢掉证据。
                absent = True
                answered += 1
                break
            _cooldown[name] = time.time() + (90 if e.code == 429 else 15)
        except Exception as e:
            if "429" in str(e):
                _cooldown[name] = time.time() + 90
            else:
                _cooldown[name] = time.time() + 15
        else:
            answered += 1
            if out.first_trade and not best.first_trade:
                best.first_trade = out.first_trade    # 空答案里那句自述也是证据
            if reaches_window_start(out):
                # 历史已取全，换通道不可能更好
                out.attempts, out.answered = best.attempts, answered
                return out
            if len(out) > len(best):
                # 整份换上，不做跨通道拼接：一份响应是自洽的，拼出来的「更长」来路不明。
                best.clear()
                best.update(out)                       # 截断响应留着，好过空手而归
        time.sleep(1.2)
    best.answered = answered
    if absent and not best:
        raise SymbolNotFound(sym)
    return best


if __name__ == "__main__":
    import sys
    for s in sys.argv[1:] or ["AAPL"]:
        t0 = time.time()
        d = fetch(s)
        print("%-6s %d 个月｜尝试 %d 答话 %d 源自述首交易日 %s｜%.1fs"
              % (s, len(d), d.attempts, d.answered, d.first_trade, time.time() - t0))
