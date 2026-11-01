# -*- coding: utf-8 -*-
"""修复触发器：DELETE 未被拦截（已锁标的的行被删掉了）。

根因：函数在 DECLARE 里用 `case when tg_op='DELETE' then old.symbol else new.symbol end`
初始化变量；DELETE 上下文中 NEW 未赋值，导致 sym 为 NULL，exists 检查失效。
改为显式分支：DELETE 走 OLD，INSERT/UPDATE 走 NEW。
"""
import io
import os

import psycopg2

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = "fnvahsyrzmmxwovbsqpc"
HOST = "aws-0-ap-northeast-1.pooler.supabase.com"

FN = r"""
create or replace function guard_locked_months() returns trigger as $$
declare
  sym text;
  m   text;
  r   record;
begin
  if tg_op = 'DELETE' then
    sym := old.symbol;
    m   := old.month;
    if exists (select 1 from locked_symbols where symbol = sym) then
      raise exception 'symbol % is locked (locked_symbols)', sym;
    end if;
    for r in select scope_from, scope_to from locks
              where kind = 'monthly_prices' and status = 'locked' loop
      if m between r.scope_from and r.scope_to then
        raise exception 'month % is locked (% .. %)', m, r.scope_from, r.scope_to;
      end if;
    end loop;
    return old;
  end if;

  sym := new.symbol;
  m   := new.month;
  if tg_op = 'UPDATE' and new.month <> old.month then
    raise exception 'month is immutable (attempt % -> %)', old.month, new.month;
  end if;
  if exists (select 1 from locked_symbols where symbol = sym) then
    raise exception 'symbol % is locked (locked_symbols)', sym;
  end if;
  for r in select scope_from, scope_to from locks
            where kind = 'monthly_prices' and status = 'locked' loop
    if m between r.scope_from and r.scope_to then
      raise exception 'month % is locked (% .. %)', m, r.scope_from, r.scope_to;
    end if;
  end loop;
  return new;
end $$ language plpgsql
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


def install():
    c = db()
    with c.cursor() as cur:
        cur.execute(FN)
    c.close()
    print("触发器函数已重写")


def verify():
    c = db()
    with c.cursor() as cur:
        tests = [
            ("DELETE 已锁", "delete from prices_monthly where symbol='MSFT' and month='2020-06'"),
            ("UPDATE 已锁", "update prices_monthly set adj_close=adj_close+1 "
                            "where symbol='MSFT' and month='2020-06'"),
            ("INSERT 已锁", "insert into prices_monthly(symbol,month,adj_close,raw_close,source,"
                            "quality,basis) values ('MSFT','2020-06',1,1,'t','ok','total_return')"),
            ("DELETE 未锁", "delete from prices_monthly where symbol='ZZNOLOCK' and month='2020-06'"),
        ]
        for label, sql in tests:
            try:
                cur.execute(sql)
                print("   %-12s 允许（rowcount=%d）" % (label, cur.rowcount))
            except Exception as e:
                print("   %-12s 拦截: %s" % (label, str(e).split("\n")[0][:70]))
    c.close()


if __name__ == "__main__":
    install()
    verify()
