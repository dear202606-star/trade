# -*- coding: utf-8 -*-
"""probe_delisted.py — 专攻「退市票 + 全收益」，在 CI（美国出口）跑

背景：171 只退市票只有 Alpha Vantage 能补，25/天要 7 天。
     但中文门户（新浪/东财/雪球）对美股退市票往往保留历史，且「前复权」含分红。
     本机（大陆）访问东财会超时，所以必须在美国 runner 上探测。

探测目标：
  1. 东财 push2his（secid 105/106/107 = NASDAQ/NYSE/AMEX），fqt=1 前复权
  2. 东财 K 线另一域名 push2his / push2
  3. 雪球（需先握手拿 cookie，再请求 kline）
  4. 网易 / 腾讯 备用
输出：每个 (源, 标的) 是否拿到数据、月份数、首末月份。
"""
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
      "Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}

TICKERS = ["ATVI", "ETFC", "XL", "AABA", "CERN", "AAPL"]   # 前 5 个退市，AAPL 作对照


def get(url, headers=None, timeout=20):
    h = dict(UA)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def eastmoney(sym):
    """返回 (月份数, 首月, 末月)"""
    out = []
    for pre in ("105", "106", "107"):
        url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
               "?secid=%s.%s&klt=103&fqt=1&beg=19900101&end=20500101"
               "&fields1=f1,f2,f3&fields2=f51,f53" % (pre, sym))
        try:
            st, body = get(url)
            d = json.loads(body)
            kl = (d.get("data") or {}).get("klines") or []
            if kl:
                out.append((pre, len(kl), kl[0].split(",")[0], kl[-1].split(",")[0]))
        except Exception as e:
            out.append((pre, "ERR:" + str(e)[:24], "", ""))
    return out


def xueqiu(sym):
    """雪球需要先握手拿 cookie"""
    try:
        _, _ = get("https://xueqiu.com/", headers={"Referer": "https://xueqiu.com/"})
    except Exception:
        pass
    url = ("https://stock.xueqiu.com/v5/stock/chart/kline.json?symbol=%s"
           "&begin=%d&period=month&type=before&count=-260" % (sym, int(time.time()) * 1000))
    try:
        st, body = get(url, headers={"Referer": "https://xueqiu.com/"})
        d = json.loads(body)
        items = ((d.get("data") or {}).get("item")) or []
        return (len(items), "", "") if items else (0, "", "")
    except Exception as e:
        return ("ERR:" + str(e)[:30], "", "")


def netease(sym):
    try:
        st, body = get("https://quotes.money.163.com/service/chddata.html?code=1%s&start=19900101&end=20500101" % sym)
        lines = [l for l in body.strip().split("\n") if l][1:]
        return (len(lines), "", "") if lines else (0, "", "")
    except Exception as e:
        return ("ERR:" + str(e)[:30], "", "")


def main():
    print("%-8s %-10s %-28s" % ("标的", "源", "结果"))
    for t in TICKERS:
        for pre, n, a, b in eastmoney(t):
            print("%-8s %-10s %-28s" % (t, "em-" + pre, "%s 行 %s~%s" % (n, a, b)))
        n, a, b = xueqiu(t)
        print("%-8s %-10s %-28s" % (t, "xueqiu", n))
        n, a, b = netease(t)
        print("%-8s %-10s %-28s" % (t, "netease", n))
        print()


if __name__ == "__main__":
    main()
