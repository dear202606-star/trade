# -*- coding: utf-8 -*-
"""apply_sql.py — 执行 sql/*.sql 并做后置断言

为什么要这个
  审计 P0-02：守卫函数被 lock_symbols.py 与 lock_stage.py 两个脚本用
  `create or replace` 抢管，强版本被弱版本覆盖 → 锁在大部分时间失效。
  修法是：DDL 只由 sql/ 目录管理，Python 脚本不再改函数，只做读取与断言。

用法
  python apply_sql.py sql/010_guard_locked.sql
  python apply_sql.py --verify          # 只验证线上函数是强版本
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


def apply(path):
    sql = io.open(path, encoding="utf-8").read()
    c = db()
    with c.cursor() as cur:
        cur.execute("set statement_timeout = '120s'")
        cur.execute(sql)
    c.close()
    print("已执行:", path)


def verify():
    """断言线上函数是强版本（查 locked_symbols + 有 DELETE 分支 + 触发器含 delete）"""
    c = db()
    ok = True
    with c.cursor() as cur:
        cur.execute("select prosrc from pg_proc where proname='guard_locked_months'")
        row = cur.fetchone()
        if not row:
            print("❌ 守卫函数不存在")
            return False
        src = row[0]
        checks = [
            ("查 locked_symbols", "locked_symbols" in src),
            ("查 locks", "from locks" in src),
            ("有 DELETE 分支", "tg_op = 'DELETE'" in src),
            ("拦截搬月份", "month is immutable" in src),
        ]
        for name, cond in checks:
            print(("  ✅ " if cond else "  ❌ ") + name)
            ok = ok and cond
        cur.execute("""select pg_get_triggerdef(oid) from pg_trigger
                       where tgrelid='public.prices_monthly'::regclass and not tgisinternal""")
        defs = [r[0] for r in cur.fetchall()]
        has_delete = any("DELETE" in d and "INSERT" in d for d in defs)
        print(("  ✅ " if has_delete else "  ❌ ") + "触发器含 DELETE 事件")
        ok = ok and has_delete
        print("  触发器数:", len(defs))
    c.close()
    print("\n结论:", "✅ 强版本已生效" if ok else "❌ 守卫不完整，锁可能失效")
    return ok


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--verify":
        sys.exit(0 if verify() else 1)
    if len(sys.argv) > 1:
        apply(sys.argv[1])
        sys.exit(0 if verify() else 1)
    print(__doc__)
