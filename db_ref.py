# -*- coding: utf-8 -*-
"""db_ref.py — 成分股与行业分类入库（替代 static/*.csv / *.json）

现状问题（用户明确要求：一切以在线库为准，禁止线下文件做数据依据）
  · static/members_frozen.csv   成分区间
  · static/sector_frozen.json   行业分类
  这两个文件是回测的**口径定义**，却放在仓库里，属于「线下文件做依据」。

做法
  1. 建表 members（symbol, start_date, end_date）与 sectors（symbol, sector）
  2. 首次从文件 bootstrap 一次（只在表为空时执行）
  3. 之后所有代码读库；文件仅作为历史留档，删除也不影响运行
"""
import csv
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

DDL = """
create table if not exists members (
  symbol     text not null,
  start_date date not null,
  end_date   date not null,
  source     text default 'fja05680',
  primary key (symbol, start_date)
);
create table if not exists sectors (
  symbol text primary key,
  sector text,
  source text default 'datahub',
  updated_at timestamptz default now()
);
alter table members enable row level security;
alter table sectors enable row level security;
do $$ begin
  begin
    create policy public_read on members for select to anon, authenticated using (true);
  exception when duplicate_object then null; end;
  begin
    create policy public_read on sectors for select to anon, authenticated using (true);
  exception when duplicate_object then null; end;
end $$;
"""


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


def bootstrap():
    c = db()
    with c.cursor() as cur:
        for st in [x.strip() for x in DDL.split(";") if x.strip() and not x.strip().startswith("--")]:
            try:
                cur.execute(st)
            except Exception as e:
                print("  ! %s: %s" % (st.splitlines()[0][:50], str(e)[:60]))
        cur.execute("select count(*) from members")
        n_mem = cur.fetchone()[0]
        cur.execute("select count(*) from sectors")
        n_sec = cur.fetchone()[0]

        if n_mem == 0:
            p = os.path.join(BASE, "static", "members_frozen.csv")
            rows = list(csv.DictReader(io.open(p, encoding="utf-8")))
            data = [(r["ticker"], r["start_date"] or "1970-01-01",
                     r["end_date"] or "2099-12-31") for r in rows]
            psycopg2.extras.execute_values(
                cur, "insert into members(symbol,start_date,end_date) values %s "
                     "on conflict do nothing", data, page_size=2000)
            print("members 已从文件 bootstrap：%d 行" % len(data))

        if n_sec == 0:
            p = os.path.join(BASE, "static", "sector_frozen.json")
            d = json.load(io.open(p, encoding="utf-8"))
            data = [(k, v) for k, v in d.items()]
            psycopg2.extras.execute_values(
                cur, "insert into sectors(symbol,sector) values %s "
                     "on conflict (symbol) do update set sector=excluded.sector", data,
                page_size=2000)
            print("sectors 已从文件 bootstrap：%d 行" % len(data))

        cur.execute("select count(*), min(start_date), max(end_date) from members")
        print("members:", cur.fetchone())
        cur.execute("select count(*) from sectors")
        print("sectors:", cur.fetchone()[0])
    c.close()


def load_members():
    """返回 {ticker: [(start, end), ...]}；库不可用时返回 None（调用方必须报错，不许退回文件）"""
    c = db()
    with c.cursor() as cur:
        cur.execute("select symbol, start_date, end_date from members order by symbol, start_date")
        rows = cur.fetchall()
    c.close()
    out = {}
    for s, a, b in rows:
        out.setdefault(s, []).append((a.isoformat(), b.isoformat()))
    return out


def load_sectors():
    c = db()
    with c.cursor() as cur:
        cur.execute("select symbol, sector from sectors")
        rows = cur.fetchall()
    c.close()
    return {s: v for s, v in rows}


if __name__ == "__main__":
    bootstrap()
