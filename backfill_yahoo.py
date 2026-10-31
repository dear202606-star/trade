# -*- coding: utf-8 -*-
"""backfill_yahoo.py — 并行回填（每条中转一个线程，互不共享限额）

策略
  · 5 条通道各跑一个 worker，每个 worker 只用自己的通道 → 互不干扰对方的限流
  · 每条通道独立冷却（429 → 冷却 60–120s）
  · 写库批量 6000 行，线程内不发 DB 写，主线程汇总
  · 采集校验（ingest_validate）在入队前执行，不合格直接丢弃
"""
import io
import queue
import sys
import threading
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import psycopg2

import db_prices as DB
import ingest_validate as IV
import yahoo_fetch as YF

BASE = "."
LOCK = threading.Lock()
RESULTS = []
STATS = {"ok": 0, "skip": 0, "bad": 0}
Q = queue.Queue()


def worker(channel_name, channel_tpl):
    tail_tpl = ("?period1=1262304000&period2=%d&interval=1mo&events=div%%2Csplit")
    cooldown = 0
    while True:
        try:
            sym = Q.get_nowait()
        except queue.Empty:
            return
        if cooldown > time.time():
            time.sleep(cooldown - time.time())
        url = channel_tpl % sym
        tail = tail_tpl % int(time.time())
        if channel_name == "translate":
            url += tail + "&_x_tr_sl=en&_x_tr_tl=zh-CN&_x_tr_hl=en"
        elif "?quest=" in channel_tpl or "?url=" in channel_tpl:
            import urllib.parse as _up
            url += ("&" + tail[1:]) if "?quest=" in channel_tpl else _up.quote(tail, safe="")
        else:
            url += tail
        try:
            import urllib.request
            raw = urllib.request.urlopen(urllib.request.Request(url, headers=YF.UA),
                                         timeout=45).read().decode("utf-8", "replace")
            bars = YF._parse(raw, channel_name)
        except Exception as e:
            s = str(e)
            if "429" in s:
                cooldown = time.time() + 90
            elif "404" in s:
                with LOCK:
                    STATS["skip"] += 1
                continue
            else:
                cooldown = time.time() + 20
            with LOCK:
                STATS["skip"] += 1
            continue
        if not bars:
            with LOCK:
                STATS["skip"] += 1
            continue
        good, why, _ = IV.check_payload(sym, bars, "total_return")
        if not good:
            with LOCK:
                STATS["bad"] += 1
            print("  ✗ %-6s %s" % (sym, why), flush=True)
            continue
        with LOCK:
            STATS["ok"] += 1
            for m, (a, r) in bars.items():
                RESULTS.append((sym, m, a, r, "yahoo_relay", "ok", "total_return"))
        time.sleep(1.0)


def main(limit=None):
    pw = io.open("output/_supabase_dbpass.txt", encoding="utf-8").read().strip()
    c = psycopg2.connect(host="aws-0-ap-northeast-1.pooler.supabase.com", port=6543,
                         user="postgres.fnvahsyrzmmxwovbsqpc", password=pw,
                         dbname="postgres", sslmode="require")
    cur = c.cursor()
    cur.execute("""select p.symbol from prices_monthly p
                   where p.month like '%-12' and p.month between '2012-12' and '2025-12'
                   group by 1 having count(*) filter (where basis='total_return') = 0
                   order by count(*) desc""")
    syms = [r[0] for r in cur.fetchall()]
    c.close()
    if limit:
        syms = syms[:limit]
    print("待补 %d 只，启动 %d 个通道" % (len(syms), len(YF.CHANNELS)), flush=True)
    for s in syms:
        Q.put(s)

    threads = [threading.Thread(target=worker, args=(n, t), daemon=True) for n, t in YF.CHANNELS]
    t0 = time.time()
    for t in threads:
        t.start()
    while any(t.is_alive() for t in threads):
        time.sleep(15)
        with LOCK:
            n_ok, n_skip = STATS["ok"], STATS["skip"]
            n_res = len(RESULTS)
            pending = n_res
        if pending >= 6000:
            with LOCK:
                batch, RESULTS[:] = RESULTS[:], []
            DB.write_rows(batch)
        print("  成功 %d / 跳过 %d / 失败 %d ｜ 剩余 %d ｜ %.0fs" % (
            n_ok, n_skip, STATS["bad"], Q.qsize(), time.time() - t0), flush=True)
    if RESULTS:
        DB.write_rows(RESULTS)
    print("完成：成功 %d / 跳过 %d / 失败 %d，用时 %.0fs" % (
        STATS["ok"], STATS["skip"], STATS["bad"], time.time() - t0))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
