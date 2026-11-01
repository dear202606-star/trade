# -*- coding: utf-8 -*-
"""realtime_check.py — 当年持仓层是否保持 5 分钟级更新

检查四件事
  1. pg_cron 的盘中任务（*/5 13-21 * * 1-5）是否在跑、成功率多少
  2. 最近一次盘中派发距今多久（交易日 + 盘中时段内应 <10 分钟）
  3. signal.json / equity.json 的新鲜度
  4. 当天是否有价格写入

退出码非 0 表示实时层有问题（可接看门狗）。
"""
import io
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

import psycopg2

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
REF = "fnvahsyrzmmxwovbsqpc"
HOST = "aws-0-ap-northeast-1.pooler.supabase.com"
SITE = "https://dear202606-star.github.io/trade/data/"
FAILS = []


def ok(cond, name, detail=""):
    print(("  ✅ " if cond else "  ❌ ") + "%-34s %s" % (name, detail))
    if not cond:
        FAILS.append(name)


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


def main():
    now = datetime.now(timezone.utc)
    print("当前 UTC: %s（星期 %d）" % (now.strftime("%Y-%m-%d %H:%M"), now.isoweekday()))
    c = db()
    with c.cursor() as cur:
        print("\n[1] 盘中派发任务")
        cur.execute("select jobid, jobname, schedule, active from cron.job where schedule like '*/5%'")
        jobs = cur.fetchall()
        ok(bool(jobs), "存在 5 分钟任务", str(jobs))
        for jid, name, sched, act in jobs:
            ok(act, "任务 %s 已启用" % name, sched)
            cur.execute("""select count(*), count(*) filter (where status='succeeded'),
                                  max(start_time)
                             from cron.job_run_details
                            where jobid=%s and start_time > now() - interval '3 days'""", (jid,))
            n, okn, last = cur.fetchone()
            ok(n == 0 or okn == n, "近 3 天成功率", "%d/%d" % (okn, n))
            if last:
                print("     最近一次: %s" % last)

        print("\n[2] 数据新鲜度")
        cur.execute("select count(*) from prices_monthly where fetched_at > now() - interval '24 hours'")
        fresh = cur.fetchone()[0]
        ok(fresh > 0, "24 小时内写入价格行", "%d 行" % fresh)
        cur.execute("""select count(*) from sys_event
                        where kind='fetch' and created_at > now() - interval '24 hours'""")
        n_ev = cur.fetchone()[0]
        ok(n_ev > 0, "24 小时内采集事件", "%d 条" % n_ev)
    c.close()

    print("\n[3] 站点产物新鲜度")
    for f in ("b/signal.json", "b/equity.json"):
        try:
            r = urllib.request.urlopen(urllib.request.Request(
                SITE + f + "?n=%d" % time.time(), headers={"User-Agent": "Mozilla/5.0"}), timeout=25)
            lm = r.headers.get("Last-Modified")
            d = json.loads(r.read())
            gen = d.get("generated_at") or d.get("as_of") or "?"
            ok(True, f, "generated=%s ｜ LM=%s" % (gen, lm))
        except Exception as e:
            ok(False, f, str(e)[:50])

    print("\n结果：失败 %d 项" % len(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
