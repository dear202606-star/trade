# -*- coding: utf-8 -*-
"""db_select.py — 序列选源（在库内计算，落成 series_selected 表）

问题（两份审计的 P0）
  逐月挑「最高等级源」会在源切换处拼出假收益：两源价格水平不可比，
  实测 574 处断层、最大单月 +9,155%，2010 年 Top12 只重合 1/12，2011 年 3/3 持仓是假的。

规则（按审计建议：选源粒度从 (symbol,month) 改为 symbol）
  1. 每只票只选**一个主源**：口径优先（total_return > price_only）→ 源等级 → 覆盖月数
  2. 同源整段取用，绝不逐月换源 → 序列内部连续，无拼接跳变
  3. 主源覆盖不到的月份就是缺口（下游按「缺 12 月剔除」处理），宁缺勿错
  4. 计算全部在数据库内完成，结果落 series_selected，下游一次扫描即可
"""
import io
import os
import sys

import psycopg2

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
REF = "fnvahsyrzmmxwovbsqpc"
HOST = "aws-0-ap-northeast-1.pooler.supabase.com"

DDL = """
create table if not exists series_selected (
  symbol    text not null,
  month     text not null,
  adj_close numeric,
  raw_close numeric,
  source    text,
  basis     text,
  primary key (symbol, month)
);
create index if not exists idx_series_month on series_selected(month);
alter table series_selected enable row level security;
-- CI 用公开 anon key 只读（数据本身公开）
"""

BUILD = """
with cand as (
  select symbol, source,
         max(case when basis = 'total_return' then 0 else 1 end) as basis_rank,
         count(*) filter (where month between '2010-01' and '2025-12') as cov,
         case source
           when 'alphavantage' then 1
           when 'yahoo_relay'  then 2
           when 'eodhd'        then 3
           when 'hf_amirtrader' then 3
           when 'hf_ayushkhaire' then 4
           when 'sina_repaired+eodhd' then 4
           else 5 end as src_rank
  from prices_monthly
  group by symbol, source
),
pick as (
  select symbol, source,
         row_number() over (partition by symbol
                            order by basis_rank, src_rank, cov desc) as rn
  from cand
)
insert into series_selected(symbol, month, adj_close, raw_close, source, basis)
select p.symbol, p.month, p.adj_close, p.raw_close, p.source, p.basis
from prices_monthly p
join pick on pick.symbol = p.symbol and pick.source = p.source and pick.rn = 1
where p.month >= '2008-01'
  and p.adj_close is not null
on conflict (symbol, month) do update
  set adj_close = excluded.adj_close, raw_close = excluded.raw_close,
      source = excluded.source, basis = excluded.basis;
"""


def db():
    pw = os.environ.get("SUPABASE_DB_PASSWORD", "")
    if not pw:
        p = os.path.join(BASE, "output", "_supabase_dbpass.txt")
        if os.path.exists(p):
            pw = io.open(p, encoding="utf-8").read().strip()
    c = psycopg2.connect(host=HOST, port=6543, user="postgres." + REF, password=pw,
                         dbname="postgres", connect_timeout=6, sslmode="require")
    c.autocommit = True
    return c


def _stmts(ddl):
    """按分号切分，剔掉空语句与纯注释块"""
    out = []
    for x in ddl.split(";"):
        body = "\n".join(l for l in x.splitlines() if not l.strip().startswith("--")).strip()
        if body:
            out.append(body)
    return out


def build_table():
    c = db()
    with c.cursor() as cur:
        cur.execute("set statement_timeout = '600s'" + "")   # 池化器默认超时太短，重建会被打断
        for st in _stmts(DDL):
            cur.execute(st)
        cur.execute("drop table if exists series_selected_new")
        for st in _stmts(DDL.replace("series_selected", "series_selected_new")
                            .replace("idx_series_month", "idx_series_month_new")):
            cur.execute(st)
        cur.execute(BUILD.replace("insert into series_selected", "insert into series_selected_new"))
        cur.execute("drop table if exists series_selected")
        cur.execute("alter table series_selected_new rename to series_selected")
        cur.execute("""select count(*), count(distinct symbol), count(distinct source),
                              count(*) filter (where basis='total_return')
                       from series_selected""")
        n, syms, srcs, tr = cur.fetchone()
    c.close()
    print("series_selected: %d 行 / %d 只 / %d 个源 / 全收益 %d 行（%.1f%%）" % (
        n, syms, srcs, tr, 100.0 * tr / max(1, n)))
    return n


RANGES = [('2008-01','2010-12'), ('2011-01','2013-12'), ('2014-01','2016-12'),
          ('2017-01','2019-12'), ('2020-01','2022-12'), ('2023-01','2026-12')]


def load_all(chunks=None, cache_file=None, stamp=None):
    """按年份分片读取（池化器吞吐差、易断连 → 分片 + 重试 + 派生缓存）"""
    import json as _json
    import time as _t

    out = {}
    attempted = 0
    for lo, hi in (chunks or RANGES):
        sql = ("select symbol, month, adj_close, raw_close from series_selected "
               "where month between %s and %s")
        for attempt in range(3):
            try:
                c = db()
                with c.cursor() as cur:
                    cur.execute(sql, (lo, hi))
                    rows = cur.fetchall()
                c.close()
                for sym, month, adj, raw in rows:
                    out.setdefault(sym, {})[month] = (
                        float(adj), float(raw) if raw else float(adj))
                print("  %s..%s %d 行" % (lo, hi, len(rows)), flush=True)
                break
            except Exception as e:
                print("  %s..%s 重试 %d: %s" % (lo, hi, attempt + 1, str(e)[:50]), flush=True)
                attempted += 1
                if attempted >= 1:
                    # 首块就连不上，说明口令/网络不可用，继续重试只是白等
                    raise RuntimeError("数据库不可达（首个分片即失败）：%s" % str(e)[:80])
                _t.sleep(3)
    if not out:
        # 全部失败必须抛错，否则上层会以为「数据为空」并静默退回文件
        raise RuntimeError("series_selected 读取全部失败（无口令/网络不可达）")
    return out


if __name__ == "__main__":
    build_table()
