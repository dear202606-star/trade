# -*- coding: utf-8 -*-
"""fetch_actions.py — 采集分红与拆股事件，补上「全收益无法独立复算」的缺口

背景（架构师审计 C7）
  Yahoo 请求里本来就带了 `events=div,split`，但解析时只取 adjclose/close，
  `d["events"]` 被直接丢弃。而 corporate_actions 表是 0 行 ——
  意味着 adj_close 无法用任何独立数据复算，审计时只能"相信"它。

  Yahoo 的 adjusted close = 原始价 × 复权因子，而复权因子正是由
  「分红 + 拆股」决定的。把事件存下来，才能：
    · 用 raw_close + 事件 独立重算 adj_close，验证源没算错
    · 未来换源时判断两源复权是否一致

用法
  python fetch_actions.py --limit 100
  python fetch_actions.py --symbols AAPL,MSFT
"""
import argparse
import io
import json
import os
import sys
import time
import urllib.request

import psycopg2
import psycopg2.extras

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
REF = "fnvahsyrzmmxwovbsqpc"
HOST = "aws-0-ap-northeast-1.pooler.supabase.com"
UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")}
CHANNELS = [
    "https://query1.finance.yahoo.com/v8/finance/chart/%s?period1=1262304000&period2=%d"
    "&interval=1mo&events=div,split",
    "https://query1-finance-yahoo-com.translate.goog/v8/finance/chart/%s?period1=1262304000"
    "&period2=%d&interval=1mo&events=div,split&_x_tr_sl=en&_x_tr_tl=zh-CN&_x_tr_hl=en",
]


def db():
    pw = os.environ.get("SUPABASE_DB_PASSWORD", "")
    if not pw:
        p = os.path.join(BASE, "output", "_supabase_dbpass.txt")
        if os.path.exists(p):
            pw = io.open(p, encoding="utf-8").read().strip()
    c = psycopg2.connect(host=HOST, port=6543, user="postgres." + REF, password=pw,
                         dbname="postgres", connect_timeout=15, sslmode="require")
    c.autocommit = True
    return c


def fetch_events(sym):
    """返回 [(date, kind, ratio)]"""
    import datetime as dt
    tail = int(time.time())
    out = []
    for i, tpl in enumerate(CHANNELS):
        url = (tpl % (sym.replace(".", "-"), tail)) if "%d" in tpl else (tpl % sym)
        try:
            raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                         timeout=40).read().decode("utf-8", "replace")
            d = json.loads(raw)["chart"]["result"][0]
            ev = d.get("events") or {}
            for kind, key in (("dividend", "dividends"), ("split", "splits")):
                for _, v in (ev.get(key) or {}).items():
                    ts = v.get("date")
                    if not ts:
                        continue
                    day = dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")
                    if kind == "dividend":
                        out.append((day, "dividend", float(v.get("amount") or 0)))
                    else:
                        num, den = v.get("numerator"), v.get("denominator")
                        ratio = (float(num) / float(den)) if num and den else None
                        out.append((day, "split", ratio))
            return out
        except Exception:
            if i == len(CHANNELS) - 1:
                return []
            time.sleep(1.5)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--symbols", default="")
    a = ap.parse_args()

    c = db()
    if a.symbols:
        syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    else:
        with c.cursor() as cur:
            cur.execute("""select distinct symbol from prices_monthly
                           where source='yahoo_relay' and basis='total_return'
                           order by symbol limit %s""", (a.limit,))
            syms = [r[0] for r in cur.fetchall()]
    print("目标 %d 只" % len(syms))

    rows, ok, bad = [], 0, 0
    t0 = time.time()
    for i, s in enumerate(syms, 1):
        evs = fetch_events(s)
        if not evs:
            bad += 1
        else:
            ok += 1
            for day, kind, ratio in evs:
                rows.append((s, day, kind, ratio, "yahoo"))
        if i % 25 == 0:
            print("  %d/%d 有事件 %d 无事件 %d %.0fs" % (i, len(syms), ok, bad, time.time() - t0),
                  flush=True)
        time.sleep(0.5)

    if rows:
        with c.cursor() as cur:
            psycopg2.extras.execute_values(
                cur, """insert into corporate_actions(symbol, ex_date, kind, ratio, source)
                        values %s on conflict (symbol, ex_date, kind) do update
                        set ratio = excluded.ratio""", rows, page_size=1000)
        print("写入事件 %d 条" % len(rows))
    with c.cursor() as cur:
        cur.execute("select kind, count(*) from corporate_actions group by 1")
        print("corporate_actions 现状:", cur.fetchall())
    c.close()
    print("完成：有事件 %d / 无事件 %d / %.0fs" % (ok, bad, time.time() - t0))


if __name__ == "__main__":
    main()
