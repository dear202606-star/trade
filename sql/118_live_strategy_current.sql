begin;
-- Keep four current results; archive distinct closing results, not every intraday poll.
create table if not exists public.live_strategy_current (
 variant text not null check(variant in ('A','B')),
 kind text not null check(kind in ('equity','preview')),
 source_as_of timestamptz not null,
 quote_version text not null,
 input_sha256 text not null,
 payload jsonb not null,
 evidence jsonb not null,
 created_at timestamptz not null default now(),
 primary key(variant,kind)
);
insert into public.live_strategy_current
 select distinct on(variant,kind) variant,kind,source_as_of,quote_version,input_sha256,payload,evidence,created_at
 from public.live_strategy_results order by variant,kind,source_as_of desc,created_at desc
 on conflict do nothing;
alter table public.live_strategy_results add column if not exists financial_sha256 text;
create unique index if not exists live_strategy_closing_financial_version
 on public.live_strategy_results(variant,kind,financial_sha256);
alter table public.live_strategy_current enable row level security;
revoke all on public.live_strategy_current from anon,authenticated;
grant select on public.live_strategy_current to anon,authenticated;
drop policy if exists live_strategy_current_read on public.live_strategy_current;
create policy live_strategy_current_read on public.live_strategy_current for select to anon,authenticated using(true);
create or replace view public.v_live_strategy_results with(security_invoker=true) as
 select variant,kind,source_as_of,quote_version,input_sha256,payload,created_at
 from public.live_strategy_current;
grant select on public.v_live_strategy_results to anon,authenticated;
commit;
