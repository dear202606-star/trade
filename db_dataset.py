# -*- coding: utf-8 -*-
"""db_dataset.py — 数据集版本化与快照（审计 P1-1/P1-2：dataset_rows 无口径、无可追溯）

做什么
  1. 给 dataset_rows 补 basis / quality / source 列（原来只有 adj_close）
  2. 从 series_selected（每票单源的唯一序列）生成一个新数据集版本
  3. 记录行数、标的数、口径占比、内容哈希、以及每个源的贡献
  4. 生成后立即跑一致性检查：dataset_rows 必须能逐行追溯到 prices_monthly

用法
  python db_dataset.py --build b_v1        # 生成版本
  python db_dataset.py --list
"""
import argparse
import hashlib
import io
import json
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

MIGRATE = """
alter table dataset_rows add column if not exists basis text;
alter table dataset_rows add column if not exists quality text;
alter table dataset_rows add column if not exists raw_close numeric;
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


def build(name):
    c = db()
    with c.cursor() as cur:
        for st in [x.strip() for x in MIGRATE.split(";") if x.strip()]:
            cur.execute(st)
        cur.execute("delete from datasets where name=%s", (name,))
        cur.execute("""insert into datasets(name, status, notes)
                       values (%s,'draft',%s) returning id""",
                    (name, "由 series_selected 生成（每票单源）"))
        did = cur.fetchone()[0]

        cur.execute("""insert into dataset_rows(dataset_id, symbol, month, adj_close, source, basis, raw_close)
                       select %s, symbol, month, adj_close, source, basis, raw_close
                       from series_selected""", (did,))
        cur.execute("""select count(*), count(distinct symbol),
                              count(*) filter (where basis='total_return'),
                              count(distinct source)
                       from dataset_rows where dataset_id=%s""", (did,))
        n, syms, tr, srcs = cur.fetchone()
        cur.execute("""select source, count(*) from dataset_rows
                       where dataset_id=%s group by 1 order by 2 desc""", (did,))
        mix = {s: int(k) for s, k in cur.fetchall()}
        cur.execute("""select md5(string_agg(symbol||month||coalesce(adj_close::text,''),'|'
                              order by symbol, month))
                       from dataset_rows where dataset_id=%s""", (did,))
        h = cur.fetchone()[0]
        cur.execute("""update datasets set symbols=%s, months=%s, source_mix=%s,
                              input_hash=%s where id=%s""",
                    (syms, n, json.dumps(mix), h, did))

        # 逐行可追溯性检查：每行必须能在 prices_monthly 找到同源同月同值
        cur.execute("""select count(*) from dataset_rows d
                       where d.dataset_id=%s and not exists (
                         select 1 from prices_monthly p
                         where p.symbol=d.symbol and p.month=d.month
                           and p.source=d.source and p.adj_close=d.adj_close)""", (did,))
        orphan = cur.fetchone()[0]
        cur.execute("""insert into validations(dataset_id, check_name, status, detail)
                       values (%s,'dataset_rows 可追溯到 prices_monthly',%s,%s)""",
                    (did, "pass" if orphan == 0 else "fail",
                     "孤立行 %d / 共 %d" % (orphan, n)))
    c.close()
    print("数据集 %s（id=%s）：%d 行 / %d 只 / 全收益 %.1f%% / %d 个源 / hash %s" % (
        name, did, n, syms, 100.0 * tr / max(1, n), srcs, h))
    print("  来源分布:", mix)
    print("  可追溯性: %s（孤立行 %d）" % ("✅ 通过" if orphan == 0 else "❌ 失败", orphan))
    return 0 if orphan == 0 else 1


def listing():
    c = db()
    with c.cursor() as cur:
        cur.execute("""select id, name, status, symbols, months, input_hash,
                              to_char(created_at,'MM-DD HH24:MI')
                       from datasets order by id""")
        print("%-3s %-14s %-8s %6s %8s %-10s %s" % ("id", "name", "status", "syms", "rows", "hash", "created"))
        for r in cur.fetchall():
            print("%-3s %-14s %-8s %6s %8s %-10s %s" % r)
    c.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default="")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        listing()
    if a.build:
        sys.exit(build(a.build))
