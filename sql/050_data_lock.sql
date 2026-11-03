-- M5：锁层合并（locks + locked_symbols → data_lock）
--
-- 权威来源：docs/DB_SCHEMA_REDESIGN_2026-11.md §6.5
-- 要点：一把锁=一条不可变声明（append-only，作废靠插新行+supersedes_id）；
--       统一守卫 priсes_monthly 与 series_pick；统一指纹函数。

-- 6.5.1 统一锁表
create table if not exists public.data_lock (
  id            bigint generated always as identity primary key,
  scope         text not null check (scope in ('month_range','symbol','symbol_month')),
  symbol        text,
  month_from    text,
  month_to      text,
  status        text not null default 'locked' check (status in ('locked','void')),
  row_count     bigint,
  symbol_count  int,
  fingerprint   text,
  fingerprint_algo text not null default 'sha256',
  run_id        uuid,
  locked_at     timestamptz not null default now(),
  locked_by     text not null default current_user,
  supersedes_id bigint references public.data_lock(id),
  note          text,
  constraint data_lock_shape_ck check (
      (scope='month_range'  and symbol is null     and month_from is not null and month_to is not null)
   or (scope='symbol'       and symbol is not null and month_from is null)
   or (scope='symbol_month' and symbol is not null and month_from is not null)),
  constraint data_lock_range_ck check (month_to is null or month_from is null or month_to >= month_from)
);

create unique index if not exists data_lock_uq
  on public.data_lock (scope, symbol, month_from, month_to) nulls not distinct
  where status = 'locked';
create index if not exists data_lock_by_symbol on public.data_lock (symbol)
  where status='locked' and scope in ('symbol','symbol_month');
create index if not exists data_lock_by_month on public.data_lock (month_from, month_to)
  where status='locked' and scope='month_range';

-- 6.5.2 append-only
create or replace function public.data_lock_append_only() returns trigger
language plpgsql set search_path = public, pg_temp as $$
begin
  raise exception 'data_lock is append-only (attempted %)；作废请插入 status=void + supersedes_id', tg_op;
end $$;
drop trigger if exists trg_data_lock_append on public.data_lock;
create trigger trg_data_lock_append before update or delete on public.data_lock
  for each row execute function public.data_lock_append_only();

-- 6.5.4 唯一守卫
create or replace function public.guard_frozen_prices() returns trigger
language plpgsql set search_path = public, pg_temp as $$
declare v_sym text; v_mon text; v_hit bigint;
begin
  if tg_op = 'DELETE' then v_sym := old.symbol; v_mon := old.month;
  else v_sym := new.symbol; v_mon := new.month; end if;

  if tg_op = 'UPDATE' and new.month <> old.month then
    raise exception 'month is immutable (% -> %)', old.month, new.month;
  end if;

  select l.id into v_hit
    from public.data_lock l
   where l.status = 'locked'
     and not exists (select 1 from public.data_lock x where x.supersedes_id = l.id and x.status='void')
     and ( (l.symbol = v_sym
            and (l.month_from is null or v_mon >= l.month_from)
            and (l.month_to   is null or v_mon <= l.month_to))
        or (l.scope = 'month_range' and v_mon between l.month_from and l.month_to) )
   limit 1;

  if v_hit is not null then
    raise exception 'frozen: % % is covered by data_lock#%（改历史请新建锁版本）', v_sym, v_mon, v_hit;
  end if;
  return case when tg_op = 'DELETE' then old else new end;
end $$;

drop trigger if exists trg_guard_months on public.prices_monthly;
create trigger trg_guard_months before insert or update or delete on public.prices_monthly
  for each row execute function public.guard_frozen_prices();

-- 6.5.5 选源也要锁
create or replace function public.guard_frozen_pick() returns trigger
language plpgsql set search_path = public, pg_temp as $$
declare v_sym text := coalesce(new.symbol, old.symbol);
begin
  if exists (select 1 from public.data_lock l
              where l.status='locked' and l.symbol = v_sym
                and not exists (select 1 from public.data_lock x
                                 where x.supersedes_id = l.id and x.status='void')) then
    raise exception '选源已冻结：% 存在有效锁，换源必须插入新锁版本并留痕', v_sym;
  end if;
  return coalesce(new, old);
end $$;
drop trigger if exists trg_guard_pick on public.series_pick;
create trigger trg_guard_pick before update or delete on public.series_pick
  for each row execute function public.guard_frozen_pick();

-- 6.5.7 旧锁表迁移 + 兼容视图
do $$ begin
  if exists (select 1 from pg_class where relname='locks' and relkind='r') then
    insert into public.data_lock(scope, symbol, month_from, month_to, status, row_count, symbol_count,
                                 fingerprint, fingerprint_algo, locked_at, note)
    select 'month_range', null, scope_from, scope_to, 'locked', rows, symbols, hash, 'md5', locked_at, note
    from public.locks where status = 'locked';
  end if;
end $$;

do $$ begin
  if exists (select 1 from pg_class where relname='locked_symbols' and relkind='r') then
    insert into public.data_lock(scope, symbol, month_from, month_to, status, symbol_count,
                                 fingerprint, fingerprint_algo, locked_at, note)
    select 'symbol_month', symbol, '2012-01', '2025-12', 'locked', 1, hash, 'sha256-16', locked_at, note
    from public.locked_symbols
    on conflict do nothing;
  end if;
end $$;

do $$ begin
  if exists (select 1 from pg_class where relname='locks' and relkind='r')
     and not exists (select 1 from pg_class where relname='locks_legacy') then
    alter table public.locks rename to locks_legacy;
  end if;
  if exists (select 1 from pg_class where relname='locked_symbols' and relkind='r')
     and not exists (select 1 from pg_class where relname='locked_symbols_legacy') then
    alter table public.locked_symbols rename to locked_symbols_legacy;
  end if;
end $$;

create or replace view public.locks with (security_invoker = true) as
  select row_number() over (order by id)::int as id, 'monthly_prices' as kind,
         month_from as scope_from, month_to as scope_to, status, row_count as rows,
         symbol_count as symbols, fingerprint as hash, true as monthly_only, locked_at, note
  from public.data_lock where scope='month_range';

create or replace view public.locked_symbols with (security_invoker = true) as
  select symbol, locked_at, 168 as months, fingerprint as hash, note
  from public.data_lock where scope='symbol_month' and status='locked';

alter table public.data_lock enable row level security;
grant select on public.data_lock to anon, authenticated;
grant select on public.locks to anon, authenticated;
grant select on public.locked_symbols to anon, authenticated;
