# -*- coding: utf-8 -*-
"""fetch_stockanalysis.py — 用 stockanalysis.com 补退市票的全收益历史

为什么它重要（2026-11-01 实测）
  · 字段 `a` = 拆股+分红复权，与库内 yahoo/AV 的全收益价**逐值相同**
    （DUK / NVDA / KO 120 个月比值全部 1.0000，月收益一致到 0.01pp）
  · **对退市票同样可用**（ATVI 实测有数据），而 Yahoo 对退市票一律 404
  · 免费、无需 key、月度数据可得

限制
  · 免费层单标的最多 10 年（range=10Y），退市票的窗口是「退市前 10 年」
  · 因此它能把 2016 年后的覆盖补满，2010–2015 仍需 Alpha Vantage

用法
  python fetch_stockanalysis.py --symbols 50          # 补前 50 只缺全收益的
  python fetch_stockanalysis.py --ticker ATVI         # 单只
"""
import argparse
import io
import json
import math
import re
import os
import sys
import time
import urllib.error
import urllib.request

import psycopg2

import source_absence as absence

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
REF = "fnvahsyrzmmxwovbsqpc"
HOST = "aws-0-ap-northeast-1.pooler.supabase.com"
UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "Chrome/130 Safari/537.36")}
API = "https://stockanalysis.com/api/symbol/s/%s/history?range=10Y&period=Monthly"

# 只有「所需窗口内当过至少 12 个月成员、且还没被结案」的票才值得再打：
# 2026-11-22 数过，缺全收益且有成员记录的 67 只里 23 只的窗口内成员月份
# 不足 12 个月，它们的 400 每轮重打一次，纯属浪费。
TARGET_SQL = """
        select p.symbol
          from prices_monthly p
          join members m on m.symbol = p.symbol
         where {months} >= {minimum}
           and not exists (
             select 1 from source_absence_acceptance a
              where a.symbol = p.symbol and a.source = 'stockanalysis'
                and a.basis = 'total_return'
                and a.membership_end = m.end_date
                and a.membership_end < current_date)
         group by p.symbol
        having count(*) filter (where p.basis = 'total_return') = 0
         order by p.symbol
"""


def _not_found_status(exc):
    """stockanalysis 判「无此代码」的状态码，否则 None。

    只认 400 与 404：400 是它对退市代码的实际回答（2026-11-22 实测），404 是常规
    Not Found。403/429 是通道被限流，不是裁决——写进 append-only 台账等于让这只票
    从此再也不会被补采，而限流下一轮自己就好。
    """
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) and code in (400, 404) else None


def parse_rows(rows):
    if not isinstance(rows,list):raise ValueError('Unexpected monthly history schema')
    result={}
    for row in rows:
        if not isinstance(row,dict):raise ValueError('Unexpected monthly history row')
        month=str(row.get('t') or '')[:7]
        if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',month) or month in result:
            raise ValueError('Invalid or duplicate monthly history date')
        values=[]
        for key in ('a','c'):
            value=row.get(key)
            if value is None or isinstance(value,bool):
                raise ValueError('Explicit adjusted and close fields are required')
            value=float(value)
            if not math.isfinite(value) or value<=0:raise ValueError('Invalid monthly history price')
            values.append(value)
        result[month]=tuple(values)
    return result


def db():
    pw = os.environ.get("SUPABASE_DB_PASSWORD", "")
    if not pw:
        p = os.path.join(BASE, "output", "_supabase_dbpass.txt")
        if os.path.exists(p):
            pw = io.open(p, encoding="utf-8").read().strip()
    c = psycopg2.connect(host=HOST, port=6543, user="postgres." + REF, password=pw,
                         dbname="postgres", connect_timeout=20, sslmode="require")
    c.autocommit = True
    return c


def fetch(sym, tries=3):
    url = API % sym.lower().replace(".", "-")
    for attempt in range(tries):
        try:
            raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) \
                .read().decode("utf-8", "replace")
            d = json.loads(raw)
            data = d.get("data")
            rows = data.get("data") if isinstance(data, dict) else data
            if not rows:
                return {}
            return parse_rows(rows)
        except (ValueError,TypeError):
            raise
        except Exception as e:
            # Not Found 是供应商的确定回答，重试不会变好；429/403 是限流，照旧退避。
            if _not_found_status(e):
                raise
            if attempt == tries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=int, default=0)
    ap.add_argument("--ticker", default="")
    a = ap.parse_args()

    import ingest_validate as IV
    import db_prices as DB

    c = db()
    targets = [a.ticker] if a.ticker else []
    if not targets:
        with c.cursor() as cur:
            cur.execute(TARGET_SQL.format(
                months=absence.months_in_window('m.start_date', 'm.end_date'),
                minimum=absence.MIN_MEMBERSHIP_MONTHS))
            targets = [r[0] for r in cur.fetchall()]
        if a.symbols:
            targets = targets[:a.symbols]

    print("目标 %d 只（缺全收益）" % len(targets), flush=True)
    ok = bad = closed = unmatched = 0
    batch = []
    t0 = time.time()
    for i, sym in enumerate(targets, 1):
        try:
            bars = fetch(sym)
        except Exception as e:
            status = _not_found_status(e)
            if status and absence.close_absence(c, sym, 'stockanalysis',
                                                status, 'fetch_stockanalysis'):
                closed += 1
                print("  ∅ %-6s stockanalysis 回 %d 且已退市 → 按 price_only 结案，停止补采"
                      % (sym, status), flush=True)
                continue
            bad += 1
            print("  ✗ %-6s 抓取失败 %s" % (sym, str(e)[:50]), flush=True)
            continue
        if not bars:
            bad += 1
            print("  ✗ %-6s 供应商回空（无 4xx 可记，不结案）" % sym, flush=True)
            continue
        good, why, _ = IV.check_payload(sym, bars, "total_return", allow_stale=True)
        if not good:
            months = sorted(bars)
            end_date = absence.delisted_end(c, sym)
            if end_date and absence.belongs_to_later_identity(months, end_date):
                unmatched += 1
                print("  ⊘ %-6s %s｜源回 %d 根 [%s..%s] 全部晚于最后交易日 %s"
                      " → 代码已被重新上市占用，不计故障"
                      % (sym, why, len(months), months[0], months[-1], end_date), flush=True)
                continue
            bad += 1
            print("  ✗ %-6s %s｜源回 %d 根 [%s..%s]、最后交易日 %s"
                  % (sym, why, len(months), months[0], months[-1], end_date or '现役'), flush=True)
            continue
        for m, (adj, raw) in bars.items():
            batch.append((sym, m, adj, raw, "stockanalysis", "ok", "total_return"))
        ok += 1
        if len(batch) >= 4000:
            DB.write_rows(batch)
            batch = []
        if i % 20 == 0:
            print("  %d/%d 成功%d 失败%d %.0fs" % (i, len(targets), ok, bad, time.time() - t0), flush=True)
        time.sleep(1.0)
    if batch:
        DB.write_rows(batch)
    c.close()
    print("完成：成功 %d / 失败 %d / 结案缺席 %d / 结案身份不匹配 %d / 用时 %.0fs"
          % (ok, bad, closed, unmatched, time.time() - t0))
    try:
        DB.log_run("stockanalysis", len(targets), ok, failed=bad,
                   duration_s=time.time() - t0,
                   note="10Y monthly, total-return; closed=%d unmatched=%d" % (closed, unmatched))
    except Exception:
        pass
    # 与 fill_yahoo_gaps 同一条口径：只有真故障才报红。过去这一步挂着
    # continue-on-error，0/67 全失败也照样绿，等于门禁不存在（2026-11-22 实况）。
    if bad:
        raise RuntimeError('stockanalysis backfill failed: %d 只真故障' % bad)


if __name__ == "__main__":
    main()
