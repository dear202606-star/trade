# -*- coding: utf-8 -*-
"""relock.py — 修复指纹漂移的锁（append-only 方式）

背景
  verify_locks 发现 AAPL/MSFT 指纹与锁定值不一致：
  原因是测试触发器时误删了行 → 恢复数据 → 重新锁定了一次，
  但当时锁定用的是「尚未重建的物化表」，切到视图后序列变了 → 指纹对不上。
  数据本身是对的（AAPL 2021 +34.6%、2024 +30.7%，与已知一致）。

做法（遵守 data_lock append-only）
  1. 若存在「有效 locked 行」且指纹与当前不符 → 插入一行 status='void' 且 supersedes_id 指向它
  2. 插入新的 locked 行，指纹按当前 v_series 重算
"""
import argparse
import hashlib
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
Y0, Y1 = "2012-01", "2025-12"


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


def h16(rows):
    h = hashlib.sha256()
    for m, v in rows:
        h.update(("%s|%.10f;" % (m, float(v or 0))).encode())
    return h.hexdigest()[:16]


def effective_locks(cur, sym):
    """有效锁 = status='locked' 且没有被 void 行 supersede"""
    cur.execute("""
        select id, fingerprint from data_lock l
         where scope='symbol_month' and symbol=%s and status='locked'
           and not exists (select 1 from data_lock x
                            where x.supersedes_id = l.id and x.status='void')
         order by id desc
    """, (sym,))
    return cur.fetchall()


def relock(sym, reason="数据修正后重算指纹"):
    c = db()
    with c.cursor() as cur:
        cur.execute("""select month, adj_close from v_series
                       where symbol=%s and month between %s and %s order by month""",
                    (sym, Y0, Y1))
        rows = cur.fetchall()
        if not rows:
            print("%s 序列为空，跳过" % sym)
            return False
        newh = h16(rows)
        eff = effective_locks(cur, sym)
        if eff and eff[-1][1] == newh:
            print("%s 指纹一致，无需处理" % sym)
            return False
        for oid, ofp in eff:
            cur.execute("""insert into data_lock(scope,symbol,month_from,month_to,status,
                                                 symbol_count,fingerprint,fingerprint_algo,
                                                 supersedes_id,note)
                           values('symbol_month',%s,%s,%s,'void',1,%s,'sha256-16',%s,%s)""",
                        (sym, Y0, Y1, ofp, oid, "退役旧锁：" + reason))
        cur.execute("""insert into data_lock(scope,symbol,month_from,month_to,status,
                                             symbol_count,fingerprint,fingerprint_algo,note)
                       values('symbol_month',%s,%s,%s,'locked',1,%s,'sha256-16',%s)
                       returning id""",
                    (sym, Y0, Y1, newh, reason + "（重算指纹）"))
        nid = cur.fetchone()[0]
    c.close()
    print("%-8s 旧锁 %s → 新锁 #%d 指纹 %s" % (
        sym, [f[0] for f in eff] or "无", nid, newh))
    return True


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="*")
    a = ap.parse_args()
    syms = a.symbols or ["AAPL", "MSFT"]
    for s in syms:
        relock(s)
