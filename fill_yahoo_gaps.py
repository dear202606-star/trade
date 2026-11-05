# -*- coding: utf-8 -*-
"""fill_yahoo_gaps.py — 给「选源不是全收益」的标的补 Yahoo 全收益（CI 跑，美国出口）

背景
  覆盖率只有 70% 的真正原因不是缺数据，而是**选源选错了**：
    ADI/ADP/ORCL/HON 这类现役大票，库里有 hf（189 个月，仅价格）和 eodhd（13 个月，全收益），
    旧规则「全收益优先」让它们选了只有 13 个月的 eodhd → 2010-2025 全空。
  正确做法：先把 Yahoo 全收益拉下来（Yahoo 在美国 runner 可直连），再重选。

用法（在 CI，有 DB 口令）
  python fill_yahoo_gaps.py --limit 200
"""
import argparse
import io
import os
import sys
import time

import psycopg2
import psycopg2.extras

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
REF = "fnvahsyrzmmxwovbsqpc"
HOST = "aws-0-ap-northeast-1.pooler.supabase.com"


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


def targets(cur, limit, symbol=None):
    """选源不是全收益、且在指数里的标的"""
    cur.execute("""
        select k.symbol
          from series_pick k
          join members m on m.symbol = k.symbol
         where m.end_date >= '2009-01-01'
           and k.basis <> 'total_return'
           and not k.frozen
           and (cast(%s as text) is null or k.symbol = %s)
           and not exists (select 1 from locked_symbols l where l.symbol=k.symbol)
           and not exists (select 1 from series_quarantine q where q.symbol=k.symbol)
           and not exists (
             select 1 from data_lock l where l.status='locked'
               and (l.symbol=k.symbol or l.scope='month_range')
               and not exists (select 1 from data_lock x
                               where x.supersedes_id=l.id and x.status='void'))
           and not exists (
             select 1 from prices_monthly p
              where p.symbol = k.symbol and p.source = 'yahoo_relay'
                and p.basis = 'total_return'
                and p.month between '2012-01' and '2025-12')
         group by k.symbol
         order by k.symbol
         limit %s
    """, (symbol, symbol, limit))
    return [r[0] for r in cur.fetchall()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--max-seconds", type=int, default=1200)
    a = ap.parse_args()

    import yahoo_fetch as YF
    import ingest_validate as IV

    c = db()
    with c.cursor() as cur:
        syms = targets(cur, a.limit)
    print("需要补 Yahoo 全收益的标的：%d 只" % len(syms))
    if not syms:
        c.close()
        return

    ok = bad = rows = skipped = 0
    batch = []
    t0 = time.time()
    deadline = t0 + a.max_seconds
    for i, sym in enumerate(syms, 1):
        if time.time() > deadline:
            print("  ⏱ 达到时间预算 %ds，停止（剩余 %d 只留待下次）" % (a.max_seconds, len(syms)-i+1),
                  flush=True)
            break
        with c.cursor() as cur:
            if not targets(cur, 1, sym):
                skipped += 1
                continue
        try:
            bars = YF.fetch(sym)
        except Exception as e:
            bad += 1
            print("  ✗ %-6s %s" % (sym, str(e)[:40]), flush=True)
            continue
        if not bars:
            bad += 1
            continue
        good, why, _ = IV.check_payload(sym, bars, "total_return", allow_stale=True)
        if not good:
            bad += 1
            print("  ✗ %-6s %s" % (sym, why), flush=True)
            continue
        for m, (adj, raw) in bars.items():
            batch.append((sym, m, adj, raw, "yahoo_relay", "ok", "total_return"))
        ok += 1
        if len(batch) >= 8000:
            with c.cursor() as cur:
                psycopg2.extras.execute_values(
                    cur, """insert into prices_monthly(symbol,month,adj_close,raw_close,
                                                        source,quality,basis)
                            values %s on conflict (symbol,month,source) do update
                            set adj_close=excluded.adj_close, raw_close=excluded.raw_close,
                                basis=excluded.basis, quality=excluded.quality,
                                fetched_at=now()""", batch, page_size=2000)
            rows += len(batch)
            batch = []
        if i % 25 == 0:
            print("  %d/%d 成功 %d 失败 %d %.0fs" % (i, len(syms), ok, bad, time.time() - t0),
                  flush=True)
        time.sleep(0.6)
    if batch:
        with c.cursor() as cur:
            psycopg2.extras.execute_values(
                cur, """insert into prices_monthly(symbol,month,adj_close,raw_close,
                                                    source,quality,basis)
                        values %s on conflict (symbol,month,source) do update
                        set adj_close=excluded.adj_close, raw_close=excluded.raw_close,
                            basis=excluded.basis, quality=excluded.quality, fetched_at=now()""",
                batch, page_size=2000)
        rows += len(batch)
    c.close()
    print("完成：成功 %d / 失败 %d / 保护或资格变化跳过 %d / 写入 %d 行 / %.0fs" %
          (ok, bad, skipped, rows, time.time() - t0))
    remaining = len(syms) - ok - bad - skipped
    if bad or remaining:
        raise RuntimeError('Yahoo historical backfill incomplete: %d failed, %d unattempted' % (bad, remaining))


if __name__ == "__main__":
    main()
