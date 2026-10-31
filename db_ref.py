# -*- coding: utf-8 -*-
"""db_ref.py — 成分股与行业分类入库（替代 static/*.csv / *.json）

用户要求：一切以在线库为准，禁止线下文件做数据依据。
  · members  成分区间（原 static/members_frozen.csv）
  · sectors  行业分类（原 static/sector_frozen.json）

读取策略：DB 直连优先，失败自动走 REST（同一份库数据，CI 无口令时可用）。
写入只在 bootstrap（表为空时）发生，文件不再是运行时依赖。
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

DDL = [
    "create table if not exists members (symbol text not null, start_date date not null, "
    "end_date date not null, source text default 'fja05680', primary key (symbol, start_date))",
    "create table if not exists sectors (symbol text primary key, sector text, "
    "source text default 'datahub', updated_at timestamptz default now())",
    "alter table members enable row level security",
    "alter table sectors enable row level security",
]


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


def _rest(table, cols, order=None):
    import db_rest
    params = {"select": cols}
    if order:
        params["order"] = order
    return db_rest.get(table, params)


def bootstrap():
    c = db()
    with c.cursor() as cur:
        for st in DDL:
            try:
                cur.execute(st)
            except Exception as e:
                print("  ! %s: %s" % (st[:46], str(e)[:60]))
        for pol in ("members", "sectors"):
            try:
                cur.execute("create policy public_read on %s for select to anon, "
                            "authenticated using (true)" % pol)
            except Exception:
                pass
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
            print("members 已 bootstrap：%d 行" % len(data))

        if n_sec == 0:
            p = os.path.join(BASE, "static", "sector_frozen.json")
            d = json.load(io.open(p, encoding="utf-8"))
            psycopg2.extras.execute_values(
                cur, "insert into sectors(symbol,sector) values %s "
                     "on conflict (symbol) do update set sector=excluded.sector",
                list(d.items()), page_size=2000)
            print("sectors 已 bootstrap：%d 行" % len(d))

        cur.execute("select count(*), min(start_date), max(end_date) from members")
        print("members:", cur.fetchone())
        cur.execute("select count(*) from sectors")
        print("sectors:", cur.fetchone()[0])
    c.close()


def load_members():
    """{ticker: [(start, end), ...]}；DB → REST，两者都空则报错"""
    try:
        c = db()
        with c.cursor() as cur:
            cur.execute("select symbol, start_date, end_date from members "
                        "order by symbol, start_date")
            rows = cur.fetchall()
        c.close()
        out = {}
        for s, a, b in rows:
            out.setdefault(s, []).append((a.isoformat(), b.isoformat()))
        if out:
            return out
    except Exception as e:
        print("⚠ members 直连失败（%s），改走 REST" % str(e)[:50], flush=True)
    rows = _rest("members", "symbol,start_date,end_date", "symbol,start_date")
    out = {}
    for r in rows:
        out.setdefault(r["symbol"], []).append((r["start_date"], r["end_date"]))
    if not out:
        raise RuntimeError("members 从库读取失败（直连与 REST 均空）")
    return out


def load_sectors():
    try:
        c = db()
        with c.cursor() as cur:
            cur.execute("select symbol, sector from sectors")
            rows = cur.fetchall()
        c.close()
        if rows:
            return {s: v for s, v in rows}
    except Exception as e:
        print("⚠ sectors 直连失败（%s），改走 REST" % str(e)[:50], flush=True)
    rows = _rest("sectors", "symbol,sector")
    out = {r["symbol"]: r["sector"] for r in rows}
    if not out:
        raise RuntimeError("sectors 从库读取失败（直连与 REST 均空）")
    return out


if __name__ == "__main__":
    bootstrap()
