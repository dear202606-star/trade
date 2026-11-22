# -*- coding: utf-8 -*-
"""probe_absence.py — 逐通道取证：那句「Yahoo 说没有」到底是谁说的。只读，不写库。

结案台账把 HTTP 404 当作供应商的权威缺席（provider_not_found），可 404 也可能是
中转自己回的：Google 翻译代理拒绝转发、jina/codetabs 的错误页。两者的处置相反——
前者该停止重试并结案，后者是通道故障，换条通道就有数据。分不清就会把现役票
（WPX、DNB、MMC 这类）永久逐出全收益补采集合。

用法
  python probe_absence.py WPX MMC DNB ALTR AAPL
"""
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request

import yahoo_fetch as YF

UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "Chrome/130 Safari/537.36")}
TICKER = re.compile(r"^[A-Z0-9.\-]{1,12}$")
DEFAULT = "WPX MMC DNB IGT ALTR AAPL"


def _ym(epoch, fmt="%Y-%m"):
    return dt.datetime.fromtimestamp(int(epoch), dt.timezone.utc).strftime(fmt) if epoch else "-"


def classify(text):
    """把正文分成三档：Yahoo 答话 / Yahoo 权威缺席 / 不是 Yahoo 在说话。"""
    m = re.search(r"\{.*\}", text or "", re.S)      # jina 把 JSON 包在 markdown 里
    try:
        chart = json.loads(m.group(0) if m else text)["chart"]
    except Exception:
        return "非 Yahoo 正文", " ".join((text or "").split())[:90]
    if chart.get("error"):
        return "Yahoo 权威缺席", str(chart["error"])[:120]
    result = chart.get("result") or []
    if not result:
        return "Yahoo 空壳（result 为空）", ""
    ts = result[0].get("timestamp") or []
    first = (result[0].get("meta") or {}).get("firstTradeDate")
    span = "%s..%s" % (_ym(ts[0]), _ym(ts[-1])) if ts else "0 根"
    return "Yahoo 答话", "%d 根 %s firstTrade=%s" % (len(ts), span, _ym(first, "%Y-%m-%d"))


def symbols_from(argv, environ):
    """命令行优先，其次 SYMBOLS 环境变量（工作流传参走 env，不插值进 shell）。"""
    tokens = argv[1:] or (environ.get("SYMBOLS") or DEFAULT).split()
    keep = [t.upper() for t in tokens if TICKER.match(t.upper())]
    if not keep:
        raise SystemExit("没有合法标的代码（形如 WPX、BRK.B）")
    return keep


def main(argv, environ=None):
    environ = environ if environ is not None else os.environ
    for sym in symbols_from(argv, environ):
        print("\n== %s ==" % sym, flush=True)
        for name, tpl in YF.CHANNELS:
            url = YF.build_url(name, tpl, sym, YF._q(sym))
            try:
                body = urllib.request.urlopen(
                    urllib.request.Request(url, headers=UA), timeout=45).read().decode(
                    "utf-8", "replace")
                kind, detail = classify(body)
                print("  %-11s HTTP 200  %-26s %s" % (name, kind, detail), flush=True)
            except urllib.error.HTTPError as e:
                try:
                    kind, detail = classify(e.read().decode("utf-8", "replace"))
                except Exception:
                    kind, detail = "正文不可读", ""
                print("  %-11s HTTP %s   %-26s %s" % (name, e.code, kind, detail), flush=True)
            except Exception as e:
                print("  %-11s 抛 %s: %s" % (name, type(e).__name__, str(e)[:80]), flush=True)
    print("\n判读：只要有一条通道回「Yahoo 答话」且根数 > 0，这条代码在源里就存在，"
          "任何 provider_not_found 结案都不成立。", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
