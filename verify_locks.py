# -*- coding: utf-8 -*-
"""verify_locks.py — 校验已锁标的是否真的没变过

原理
  锁定时把序列（2012-01..2025-12 的 month|adj_close）哈希存进 data_lock.fingerprint。
  现在用同一算法重算，与存量比对：
    · 一致 → 当前选中序列与旧指纹一致，不证明原验收语义正确
    · 不一致 → 可能是选源、身份边界或数据变化，必须调查，不能自动重锁
    · 整票被复核隔离（series_quarantine）→ 读数按设计退出，锁与原始数据都保留，
      这类锁没有可比对象：既不算通过也不算漂移，单独计数并打印出来

用法
  python verify_locks.py            # 全量校验
  python verify_locks.py --sample 50
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


def hash_series(rows):
    h = hashlib.sha256()
    for m, v in rows:
        h.update(("%s|%.10f;" % (m, float(v or 0))).encode())
    return h.hexdigest()[:16]


def live_locks(c):
    """有效锁：未被作废取代的那批。"""
    with c.cursor() as cur:
        cur.execute("""select l.symbol, l.fingerprint from data_lock l
                       where l.scope='symbol_month' and l.status='locked'
                         and not exists (select 1 from data_lock x
                                          where x.supersedes_id = l.id and x.status='void')
                       order by l.symbol""")
        return cur.fetchall()


def quarantined_symbols(c):
    """被人工复核整票隔离的代码——v_series 里永远查不到它们，这是设计而非故障。"""
    with c.cursor() as cur:
        cur.execute("select distinct symbol from series_quarantine")
        return {r[0] for r in cur.fetchall()}


def series_rows(c, sym):
    with c.cursor() as cur:
        cur.execute("""select month, adj_close from v_series
                       where symbol=%s and month between %s and %s
                       order by month""", (sym, Y0, Y1))
        return cur.fetchall()


def classify(locks, rows_of, quarantined):
    """把每把锁归到四类之一，返回计数、异常样例与被隔离的代码。

    rows_of(symbol) → [(month, adj_close), …]。隔离判据必须在「序列缺失」之前：
    同一个「v_series 里一行都没有」，对隔离票是复核结论的正常工作状态，对其它票
    才是真丢数据。两者混计会让一次合规隔离把门禁永久钉红（2026-11-05 SUN 之后
    每晚如此），而恒红的门禁等于没有门禁。
    """
    ok = drift = missing = held = 0
    bad, quarantined_locked = [], []
    for sym, fp in locks:
        rows = rows_of(sym)
        if not rows:
            if sym in quarantined:
                held += 1
                quarantined_locked.append(sym)
                continue
            missing += 1
            bad.append((sym, "序列缺失"))
            continue
        h = hash_series(rows)
        if h == fp:
            ok += 1
        else:
            drift += 1
            bad.append((sym, "%s -> %s（%d 个月）" % (fp, h, len(rows))))
    return {"ok": ok, "drift": drift, "missing": missing, "held": held,
            "bad": bad, "quarantined_locked": quarantined_locked}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0)
    a = ap.parse_args()

    c = db()
    locks = live_locks(c)
    if a.sample:
        locks = locks[:a.sample]
    print("待校验 %d 只" % len(locks))
    result = classify(locks, lambda sym: series_rows(c, sym), quarantined_symbols(c))
    c.close()

    ok, drift, missing = result["ok"], result["drift"], result["missing"]
    print("一致 %d ｜ 漂移 %d ｜ 缺失 %d ｜ 复核隔离 %d"
          % (ok, drift, missing, result["held"]))
    if result["quarantined_locked"]:
        print("  隔离票的锁保留不动、不参与指纹比对：%s"
              % "、".join(sorted(set(result["quarantined_locked"]))[:10]))
    if result["bad"]:
        print("异常样例:")
        for s, why in result["bad"][:10]:
            print("  %-8s %s" % (s, why))
    if not locks:
        print("\n结论: 没有可检查的有效锁，未验证历史数据。")
    else:
        print("\n结论:", "本次检查的选中序列与旧指纹一致，不代表完整数据验收。"
              if drift == 0 and missing == 0 else "存在不一致；保留原锁并调查原因，禁止自动重建指纹。")
    return 0 if drift == 0 and missing == 0 else 1



if __name__ == "__main__":
    sys.exit(main())
