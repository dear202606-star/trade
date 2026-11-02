create table if not exists public.liquidity_windows (
 id bigint generated always as identity primary key,
 symbol text not null,
 base_month text not null check(base_month ~ '^\d{4}-(0[1-9]|1[0-2])$'),
 rank_month text not null check(rank_month ~ '^\d{4}-(0[1-9]|1[0-2])$'),
 trading_days integer not null check(trading_days>0),
 primary_amount double precision not null check(primary_amount>0 and primary_amount<'Infinity'::float8),
 independent_lower double precision not null check(independent_lower>0 and independent_lower<'Infinity'::float8),
 independent_upper double precision not null check(independent_upper>0 and independent_upper<'Infinity'::float8),
 evidence_sha256 text not null check(evidence_sha256 ~ '^[0-9a-f]{64}$'),
 calculator_sha256 text not null check(calculator_sha256 ~ '^[0-9a-f]{64}$'),
 evidence jsonb not null,
 verified boolean not null default false,
 created_at timestamptz not null default now(),
 unique(symbol,base_month,rank_month,evidence_sha256,calculator_sha256),
 check(independent_lower<=primary_amount and primary_amount<=independent_upper)
);
create or replace function public.reject_liquidity_window_mutation() returns trigger
language plpgsql as $$begin raise exception 'Liquidity evidence is append-only'; end$$;
drop trigger if exists immutable_liquidity_rows on public.liquidity_windows;
create trigger immutable_liquidity_rows before update or delete on public.liquidity_windows
 for each row execute function public.reject_liquidity_window_mutation();
drop trigger if exists immutable_liquidity_truncate on public.liquidity_windows;
create trigger immutable_liquidity_truncate before truncate on public.liquidity_windows
 for each statement execute function public.reject_liquidity_window_mutation();
alter table public.liquidity_windows enable row level security;
revoke all on public.liquidity_windows from anon,authenticated;
create or replace view public.v_liquidity_windows as
 select distinct on(n.symbol,n.base_month,n.rank_month) n.*
 from public.liquidity_windows n join public.series_selected p
 on p.symbol=n.symbol and p.month=n.rank_month
 order by n.symbol,n.base_month,n.rank_month,n.created_at desc,n.id desc;
grant select on public.v_liquidity_windows to anon,authenticated;
