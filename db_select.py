# -*- coding: utf-8 -*-
"""db_select.py — 选源（改为「决策表 + 视图」，不再重建实体表）

背景（架构师审计 P0-03 / P0-01b）
  旧的 build_table() 每 6 小时 DROP + RENAME 一张 11.7 万行的表：
    · 重建窗口里 CI 可能读到半成品；
    · 锁只挂在 prices_monthly，换源能绕过锁改历史；
    · 权限收紧后新表会丢 anon 读权限。

新结构
  series_pick(847 行)  决策：每票用哪个源（已锁/已冻结的不可改）
  v_series             视图：决策 join 价格，实时得出序列
  series_selected      同名视图（指向 v_series），所有读代码零改动

用法
  python db_select.py            # 刷新决策（幂等）
  python db_select.py --dry-run  # 只看会改哪些票
"""
import argparse
import io
import os
import sys

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
                         dbname="postgres", connect_timeout=20, sslmode="require")
    c.autocommit = True
    return c


def compute_picks(cur):
    """每票选一个主源：全收益优先 → 源等级 → 2010-2025 覆盖月数"""
    cur.execute("""
        with cand as (
          select symbol, source,
                 max(case when basis = 'total_return' then 0 else 1 end) as basis_rank,
                 count(*) filter (where month between '2012-01' and '2025-12') as cov
          from prices_monthly
          where month >= '2008-01'
            and source <> 'kaggle_cc0'
            and source <> 'kaggle_yahoo2020'
            and not exists (select 1 from series_quarantine q where q.symbol=prices_monthly.symbol)
          group by symbol, source
        )
        select symbol, source, case when basis_rank=0 then 'total_return' else 'price_only' end from (
          select symbol, source, basis_rank,
                 row_number() over (partition by symbol
                   order by
                     -- 1) 覆盖够（>=150 个月）且是全收益 最优
                     case when basis_rank = 0 and cov >= 100 then 0 else 1 end,
                     -- 2) 其次：覆盖够（不限口径）
                     case when cov >= 150 then 0 else 1 end,
                     -- 3) 覆盖越多越好
                     -cov,
                     basis_rank,
                     case source
                       when 'alphavantage' then 1 when 'yahoo_relay' then 2
                       when 'stockanalysis' then 3 when 'eodhd' then 4
                       when 'hf_amirtrader' then 4 when 'hf_ayushkhaire' then 5
                       when 'sina_repaired+eodhd' then 5 else 6 end) as rn
          from cand
        ) t where rn = 1
    """)
    return cur.fetchall()


def refresh(dry_run=False):
    c = db()
    changed = kept = blocked = 0
    with c.cursor() as cur:
        picks = compute_picks(cur)
        cur.execute("select symbol, source, basis from series_pick")
        cur_pick = {symbol: (source, basis) for symbol, source, basis in cur.fetchall()}
        cur.execute("select symbol from locked_symbols")
        locked = {r[0] for r in cur.fetchall()}
        cur.execute("select symbol from series_pick where frozen")
        frozen = {r[0] for r in cur.fetchall()}

        rows = []
        for sym, src, basis in picks:
            old = cur_pick.get(sym)
            if old == (src, basis):
                kept += 1
                continue
            if old is not None and (sym in locked or sym in frozen):
                blocked += 1
                continue
            changed += 1
            rows.append((sym, src, basis))

        print("决策：不变 %d ｜ 变更 %d ｜ 因锁定/冻结而拒绝 %d" % (kept, changed, blocked))
        if dry_run:
            print("样例变更:", rows[:10])
            c.close()
            return

        if rows:
            psycopg2.extras.execute_values(
                cur, """insert into series_pick(symbol, source, basis, chosen_at, reason)
                        values %s
                        on conflict (symbol) do update
                          set source = excluded.source, basis = excluded.basis, chosen_at = now(),
                              reason = excluded.reason""",
                [(s, src, basis, __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc), "auto") for s, src, basis in rows],
                page_size=500)
        cur.execute("select count(*), count(distinct symbol) from v_series")
        n, syms = cur.fetchone()
        print("v_series: %d 行 / %d 只" % (n, syms))
    c.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    refresh(a.dry_run)
