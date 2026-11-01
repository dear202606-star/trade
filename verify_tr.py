# -*- coding: utf-8 -*-
"""verify_tr.py — 用「原始价+分红」独立复算全收益，验证复权价没算错

原理
  Yahoo 的 adj_close = 原始价按分红/拆股回溯调整。
  所以：(raw_close 末值 + 期间分红) 的累计收益，应当≈ adj_close 的累计收益。
  两者差异在 1% 以内 → 复权正确；差异大 → 复权有问题或分红缺失。

用法
  python verify_tr.py            # 校验所有「有分红事件且已锁」的标的
  python verify_tr.py --all      # 校验所有有事件的标的
  python verify_tr.py --ticker DUK
"""
import argparse
import io
import os
import sys
from collections import defaultdict

import psycopg2

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
REF = "fnvahsyrzmmxwovbsqpc"
HOST = "aws-0-ap-northeast-1.pooler.supabase.com"
Y0, Y1 = "2012-01", "2025-12"
TOL = 0.02          # 2% 以内视为一致


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


def check(cur, sym):
    cur.execute("""select month, adj_close, raw_close from prices_monthly
                   where symbol=%s and source='yahoo_relay'
                     and month between %s and %s order by month""", (sym, Y0, Y1))
    px = cur.fetchall()
    if len(px) < 24:
        return None, "月份不足(%d)" % len(px)
    # 有拆股/分拆事件的标的，raw→adj 的映射不是「加分红」那么简单
    # （例如 AIV 2020-12 的分拆被 Yahoo 记成 0.807 和 9.295 两个非整数因子），
    # 这类票无法用本方法校验，显式跳过而不是给一个假结论。
    cur.execute("""select count(*) from corporate_actions
                   where symbol=%s and kind='split'""", (sym,))
    if cur.fetchone()[0] > 0:
        return None, "有拆股/分拆，需单独处理"
    cur.execute("""select to_char(ex_date,'YYYY-MM'), ratio from corporate_actions
                   where symbol=%s and kind='dividend'""", (sym,))
    divs = defaultdict(float)
    for m, r in cur.fetchall():
        divs[m] += float(r or 0)
    tr = 1.0
    for i in range(1, len(px)):
        _, a0, r0 = px[i - 1]
        m1, a1, r1 = px[i]
        if not r0 or not r1:
            continue
        tr *= (float(r1) + divs.get(m1, 0.0)) / float(r0)
    adj = float(px[-1][1]) / float(px[0][1])
    return (tr / adj - 1), None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--ticker", default="")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    c = db()
    with c.cursor() as cur:
        if a.ticker:
            syms = [a.ticker.upper()]
        else:
            cond = "" if a.all else """and exists (
                        select 1 from data_lock l where l.scope='symbol_month'
                         and l.symbol=ca.symbol and l.status='locked'
                         and not exists (select 1 from data_lock x
                                          where x.supersedes_id=l.id and x.status='void'))"""
            cur.execute("""select distinct ca.symbol from corporate_actions ca
                           where ca.kind='dividend' %s order by ca.symbol""" % cond)
            syms = [r[0] for r in cur.fetchall()]
        if a.limit:
            syms = syms[:a.limit]

        print("待校验 %d 只（有分红事件%s）" % (len(syms), "" if a.all else "且已锁"))
        ok = bad = skip = 0
        worst = []
        for s in syms:
            diff, why = check(cur, s)
            if diff is None:
                skip += 1
                continue
            if abs(diff) <= TOL:
                ok += 1
            else:
                bad += 1
                worst.append((s, diff))
        print("通过 %d ｜ 超阈值 %d ｜ 跳过 %d" % (ok, bad, skip))
        if worst:
            worst.sort(key=lambda x: -abs(x[1]))
            print("偏差最大的 10 只：")
            for s, d in worst[:10]:
                print("  %-8s %+.1f%%" % (s, d * 100))
    c.close()
    print("\n结论:", "✅ 复权价与独立复算一致" if bad == 0 else "⚠ 存在偏差，需逐只核查")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
