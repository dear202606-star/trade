-- 守卫函数与触发器：唯一权威定义（不要再在 Python 脚本里 create or replace）
--
-- 背景（数据库架构师审计 P0-02）：
--   lock_symbols.py 装「强版本」（查 locked_symbols + locks + DELETE 分支），
--   lock_stage.py 的 ensure_table() 装「弱版本」（只查 locks，而 locks 是空表），
--   而 lock_stage.py --check 在每次 quotes 构建里都会跑 → 大部分时间锁形同虚设。
--
-- 今后：函数与触发器只由本文件管理；Python 脚本只做读取与断言。

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
end $$ language plpgsql;

drop trigger if exists trg_guard_months on prices_monthly;
create trigger trg_guard_months
  before insert or update or delete on prices_monthly
  for each row execute function guard_locked_months();

create or replace function guard_truncate() returns trigger as $$
begin
  raise exception 'TRUNCATE is not allowed on protected tables';
end $$ language plpgsql;

drop trigger if exists trg_guard_truncate on prices_monthly;
create trigger trg_guard_truncate
  before truncate on prices_monthly
  for each statement execute function guard_truncate();
