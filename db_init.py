# -*- coding: utf-8 -*-
"""db_init.py — 建立数据仓库基线（Supabase / Postgres）

设计原则（为「结果绝对正确」服务）
  1. 多源并存：同一 (symbol, month) 允许多个 source，互不覆盖，便于比较与追溯
  2. 数据集不可变：dataset 锁定时用触发器阻止 UPDATE/DELETE
  3. 全链路可溯源：每行带 source / fetched_at；每次采集写 fetch_runs
  4. 校验独立入库：validations 记录每条检查的结论，不靠日志

用法
  python db_init.py --schema          # 建表
  python db_init.py --load-baseline   # 把 static/prices_unified 载入为 baseline_v1
  python db_init.py --report          # 覆盖率报告
"""
import argparse
import hashlib
import io
import json
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
PORT = 6543

SCHEMA = """
create table if not exists symbols (
  symbol        text primary key,
  name          text,
  gics_sector   text,
  first_month   text,
  last_month    text,
  is_delisted   boolean default false,
  note          text,
  created_at    timestamptz default now()
);

create table if not exists corporate_actions (
  symbol   text not null,
  ex_date  date not null,
  kind     text not null,          -- split | dividend | merger | ticker_change
  ratio    numeric,
  source   text,
  primary key (symbol, ex_date, kind)
);

create table if not exists prices_monthly (
  symbol      text not null,
  month       text not null,       -- YYYY-MM
  adj_close   numeric,
  raw_close   numeric,
  source      text not null,       -- alphavantage | eodhd | sina_repaired | manual
  quality     text default 'ok',   -- ok | verified | suspect | patched
  fetched_at  timestamptz default now(),
  primary key (symbol, month, source)
);
create index if not exists idx_prices_symbol on prices_monthly(symbol);
create index if not exists idx_prices_month on prices_monthly(month);

create table if not exists datasets (
  id          serial primary key,
  name        text unique not null,
  created_at  timestamptz default now(),
  symbols     int,
  months      int,
  source_mix  jsonb,
  input_hash  text,
  status      text default 'draft',  -- draft | verified | locked
  locked_at   timestamptz,
  notes       text
);

create table if not exists dataset_rows (
  dataset_id  int not null references datasets(id) on delete cascade,
  symbol      text not null,
  month       text not null,
  adj_close   numeric,
  source      text,
  primary key (dataset_id, symbol, month)
);

create table if not exists validations (
  id          serial primary key,
  dataset_id  int references datasets(id) on delete cascade,
  check_name  text,
  status      text,                -- pass | fail | warn
  detail      text,
  ran_at      timestamptz default now()
);

create table if not exists fetch_runs (
  id          serial primary key,
  source      text,
  started_at  timestamptz default now(),
  finished_at timestamptz,
  requested   int,
  ok          int,
  failed      int,
  note        text
);

-- 不可变保护：已锁定的数据集禁止改动
create or replace function guard_locked_dataset() returns trigger as $$
begin
  if exists (select 1 from datasets where id = coalesce(new.dataset_id, old.dataset_id)
             and status = 'locked') then
    raise exception 'dataset is locked; create a new version instead';
  end if;
  return coalesce(new, old);
end $$ language plpgsql;

drop trigger if exists trg_guard_rows on dataset_rows;
create trigger trg_guard_rows before insert or update or delete on dataset_rows
for each row execute function guard_locked_dataset();
"""


def conn():
    pw = io.open(os.path.join(BASE, "output", "_supabase_dbpass.txt"), encoding="utf-8").read().strip()
    return psycopg2.connect(host=HOST, port=PORT, user="postgres." + REF, password=pw,
                            dbname="postgres", connect_timeout=20, sslmode="require")


def do_schema():
    with conn() as c, c.cursor() as cur:
        cur.execute(SCHEMA)
    print("✅ 表结构已创建/更新")


def load_baseline(name="baseline_v1"):
    uni = os.path.join(BASE, "static", "prices_unified")
    files = sorted(f for f in os.listdir(uni) if f.endswith(".json"))
    rows, mix = [], {}
    for f in files:
        d = json.load(open(os.path.join(uni, f), encoding="utf-8"))
        src = d.get("source", "sina_repaired")
        q = d.get("quality", "ok")
        mix[src] = mix.get(src, 0) + 1
        for ym, px in d["bars"]:
            rows.append((d["ticker"], ym, px, src, q))
    h = hashlib.sha256(json.dumps(sorted(rows), default=str).encode()).hexdigest()[:16]

    with conn() as c, c.cursor() as cur:
        cur.execute("delete from datasets where name=%s", (name,))
        cur.execute("""insert into datasets(name, symbols, months, source_mix, input_hash, status, notes)
                       values (%s,%s,%s,%s,%s,'draft',%s) returning id""",
                    (name, len(files), len(rows), json.dumps(mix), h,
                     "由 static/prices_unified 载入（三源拼接）"))
        did = cur.fetchone()[0]
        psycopg2.extras.execute_values(
            cur, "insert into dataset_rows(dataset_id,symbol,month,adj_close,source) values %s",
            [(did, s, m, a, src) for s, m, a, src, q in rows], page_size=5000)
        # 同时灌入 prices_monthly（多源并存）
        psycopg2.extras.execute_values(
            cur, """insert into prices_monthly(symbol,month,adj_close,raw_close,source,quality)
                    values %s on conflict (symbol,month,source) do update
                    set adj_close=excluded.adj_close, quality=excluded.quality""",
            [(s, m, a, a, src, q) for s, m, a, src, q in rows], page_size=5000)
    print("✅ 数据集 %s：%d 只 / %d 行 / hash %s / 来源 %s" % (name, len(files), len(rows), h, mix))
    return did


def report():
    with conn() as c, c.cursor() as cur:
        cur.execute("select count(distinct symbol), count(*), min(month), max(month) from prices_monthly")
        n, rows, m0, m1 = cur.fetchone()
        print("价格表：%d 只 / %d 行 / %s → %s" % (n, rows, m0, m1))
        cur.execute("select source, count(distinct symbol), count(*) from prices_monthly group by 1 order by 3 desc")
        for s, ns, nr in cur.fetchall():
            print("  %-18s %4d 只 / %6d 行" % (s, ns, nr))
        cur.execute("select name, symbols, months, status, input_hash from datasets order by id desc limit 3")
        print("数据集：")
        for r in cur.fetchall():
            print("  %-14s %4d 只 %6d 行  %-8s %s" % r)

        # 与成分表对比
        import csv
        mem = list(csv.DictReader(open(os.path.join(BASE, "static", "members_frozen.csv"), encoding="utf-8")))
        need = sorted({r["ticker"] for r in mem if r["end_date"] >= "2009-01-01"})
        cur.execute("select distinct symbol from prices_monthly")
        have = {r[0] for r in cur.fetchall()}
        missing = [t for t in need if t not in have]
        print("成分表需要 %d 只 ｜ 库内 %d 只 ｜ 缺 %d 只" % (len(need), len(have), len(missing)))
        print("  缺失样例:", missing[:15])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", action="store_true")
    ap.add_argument("--load-baseline", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.schema:
        do_schema()
    if a.load_baseline:
        load_baseline()
    if a.report:
        report()
