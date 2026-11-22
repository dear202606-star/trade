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

import source_absence as absence

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


TARGET_SQL = """
        select k.symbol
          from series_pick k
          join members m on m.symbol = k.symbol
         where m.end_date >= '2009-01-01'
           -- 所需窗口（2012-01..2025-12）内当过至少 12 个月成员才值得补全收益：
           -- 2026-11-22 数过，199 只候选里 25 只的窗口内成员月份是 0（NE/MWW/MER
           -- 这类 2012 年前就离场的），任何源都给不出它们 12 个月的窗口内历史，
           -- 过去每轮照样打一次 Yahoo。
           and {months} >= {minimum}
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
           -- 已按 price_only 结案的权威缺席不再重试。结案只在「退市仍然成立」
           -- 期间生效：membership_end 必须与当前 members.end_date 完全一致，
           -- 标的重新上市（或身份被复核改期）就自动回到补采集合，无需删记录。
           and not exists (
             select 1 from source_absence_acceptance a
              where a.symbol = k.symbol and a.source = 'yahoo_relay'
                and a.basis = 'total_return'
                and a.membership_end = m.end_date
                and a.membership_end < current_date)
         group by k.symbol
         order by k.symbol
         limit %s
"""


def targets(cur, limit, symbol=None):
    """选源不是全收益、且在指数里的标的"""
    sql = TARGET_SQL.format(months=absence.months_in_window('m.start_date', 'm.end_date'),
                            minimum=absence.MIN_MEMBERSHIP_MONTHS)
    cur.execute(sql, (symbol, symbol, limit))
    return [r[0] for r in cur.fetchall()]


def close_out_absence(conn, symbol):
    """Yahoo 明确回答没有这个代码时，把这只票按 price_only 结案。

    http_status 记的是供应商的 Not Found 裁决，不是传输层状态码：经代理通道取证
    （2026-11-22 WFM），同一句缺席也可能是 HTTP 200 + chart.error 送回来的，那时
    没有 404 可记，但 yahoo_fetch 已把它归成同一种权威缺席。
    """
    return absence.close_absence(conn, symbol, 'yahoo_relay', 404, 'fill_yahoo_gaps')


def close_covered_history(conn, symbol):
    """窗口内的历史在库内已经完整、Yahoo 这一轮又给不出全收益 → 结案。

    通道回空时无法区分「源没有」与「通道断了」，这条判据让这种区分不再决定
    作业颜色：真要缺月的票结不了案、照样报红；一个月都不缺的票，无论通道是
    坏了还是懒得回答，升级口径都取不到新数据，重试只是每晚白烧流量。
    """
    return absence.close_covered_absence(conn, symbol, 'yahoo_relay', 'fill_yahoo_gaps')


def membership(conn, symbol):
    """(start_date, end_date)；库内没有成员记录时返回 (None, None)。"""
    with conn.cursor() as cur:
        cur.execute("select min(start_date), max(end_date) from members where symbol=%s",
                    (symbol,))
        row = cur.fetchone()
    return (row[0], row[1]) if row and row[0] else (None, None)


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

    ok = bad = rows = skipped = absent = unmatched = covered = 0
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
        except YF.SymbolNotFound:
            if close_out_absence(c, sym):
                absent += 1
                print("  ∅ %-6s Yahoo 判无此代码（Not Found）且已退市 → 按 price_only 结案，停止补采" % sym,
                      flush=True)
            else:
                bad += 1
                print("  ✗ %-6s Yahoo 判无此代码，但该标的并未确认退市，不能结案" % sym, flush=True)
            continue
        except Exception as e:
            # 通道故障不是权威缺席：这里绝不结案，本轮照常计一次失败。
            bad += 1
            print("  ✗ %-6s 抓取失败 %s" % (sym, str(e)[:60]), flush=True)
            continue
        if not bars:
            if close_covered_history(c, sym):
                covered += 1
                print("  ∅ %-6s 全部通道回空，但窗口内历史库内已完整 → 按 price_only 结案，停止补采"
                      % sym, flush=True)
            else:
                bad += 1
                print("  ✗ %-6s 全部通道回空，窗口内还缺月份，无法判断是源没有还是通道断了" % sym,
                      flush=True)
            continue
        good, why, _ = IV.check_payload(sym, bars, "total_return", allow_stale=True)
        if not good:
            months = sorted(bars)
            end_date = absence.delisted_end(c, sym)
            if end_date and absence.belongs_to_later_identity(months, end_date):
                unmatched += 1
                print("  ⊘ %-6s %s｜源回 %d 根 [%s..%s] 全部晚于最后交易日 %s"
                      "→ 代码已被重新上市占用，本标的历史不在这个源里"
                      % (sym, why, len(months), months[0], months[-1], end_date), flush=True)
                continue
            if close_covered_history(c, sym):
                covered += 1
                print("  ∅ %-6s %s｜源回 %d 根 [%s..%s] 不含窗口月份，库内已完整 → 按 price_only 结案"
                      % (sym, why, len(months), months[0], months[-1]), flush=True)
                continue
            bad += 1
            print("  ✗ %-6s %s｜源回 %d 根 [%s..%s]、最后交易日 %s"
                  % (sym, why, len(months), months[0], months[-1], end_date or '现役'), flush=True)
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
    print("完成：成功 %d / 失败 %d / 结案缺席 %d / 结案窗口已完整 %d / 结案身份不匹配 %d / "
          "保护或资格变化跳过 %d / 写入 %d 行 / %.0fs" %
          (ok, bad, absent, covered, unmatched, skipped, rows, time.time() - t0))
    # 只有「真故障」才让作业变红。用户 2026-11-21 定的口径是退市票接受
    # price_only：权威缺席与「代码被重新上市占用」都不是故障，下一轮 targets()
    # 也不会再取它们。预算没跑完同样不是故障——它是设计内的续跑，过去把它
    # 计成失败，等于每周给一条永不变绿的门禁添一条永不变红的噪音。
    remaining = len(syms) - ok - bad - skipped - absent - unmatched - covered
    if bad:
        raise RuntimeError('Yahoo historical backfill failed: %d 只真故障' % bad)
    if remaining:
        print("⚠ 剩余 %d 只未跑（时间预算 %ds 已用完），下次继续；本轮无故障" %
              (remaining, a.max_seconds))


if __name__ == "__main__":
    main()
