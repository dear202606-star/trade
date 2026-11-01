-- M2：维度层（symbols 启用 + sectors 并表 + members 补约束）
--
-- 权威来源：docs/DB_SCHEMA_REDESIGN_2026-11.md §6.2
-- 要点：行业并入 symbols；sectors 降级为视图；members 补外键与 CHECK；
--       覆盖度改为物化视图（不落冗余列）。

-- 6.2.1 维度表：把 symbols / sectors 合成一处
alter table public.symbols
  add column if not exists sector        text,
  add column if not exists sector_asof   date,
  add column if not exists sector_source text,
  add column if not exists updated_at    timestamptz not null default now(),
  add column if not exists delisted_date date;

insert into public.symbols(symbol, sector, sector_asof, sector_source)
select s.symbol, s.sector, current_date, s.source from public.sectors s
on conflict (symbol) do update
  set sector = excluded.sector, sector_source = excluded.sector_source, updated_at = now();

insert into public.symbols(symbol)
select distinct symbol from public.members
union select distinct symbol from public.prices_monthly
on conflict (symbol) do nothing;

-- 6.2.2 sectors 降级为视图
do $$ begin
  if exists (select 1 from pg_class where relname='sectors' and relkind='r')
     and not exists (select 1 from pg_class where relname='sectors_legacy') then
    alter table public.sectors rename to sectors_legacy;
  end if;
end $$;

create or replace view public.sectors with (security_invoker = true) as
  select symbol, sector, sector_source as source, sector_asof::timestamptz as updated_at
  from public.symbols where sector is not null;

grant select on public.sectors to anon, authenticated;
grant select on public.symbols to anon, authenticated;

-- 6.2.3 members：补 FK 与 CHECK（1,262 行，毫秒级）
do $$ begin
  if not exists (select 1 from pg_constraint where conname='members_end_ge_start_ck') then
    alter table public.members add constraint members_end_ge_start_ck check (end_date >= start_date);
  end if;
  if not exists (select 1 from pg_constraint where conname='members_symbol_fk') then
    alter table public.members add constraint members_symbol_fk
      foreign key (symbol) references public.symbols(symbol) not valid;
    alter table public.members validate constraint members_symbol_fk;
  end if;
end $$;

alter table public.members add column if not exists as_of date default current_date;

create or replace view public.v_membership_open with (security_invoker = true) as
  select m.* from public.members m where m.end_date >= current_date;

-- 6.2.4 覆盖度改为物化视图（不落冗余列）
drop materialized view if exists public.mv_symbol_coverage;
create materialized view public.mv_symbol_coverage as
  select symbol, min(month) as first_month, max(month) as last_month,
         count(*) as months, count(*) filter (where basis='total_return') as tr_months
  from public.prices_monthly group by symbol;
create unique index if not exists mv_symbol_coverage_pk on public.mv_symbol_coverage(symbol);
grant select on public.mv_symbol_coverage to anon, authenticated;
