-- Runtime results advance independently of locked historical research.
begin;
create table if not exists public.live_strategy_anchor (
 variant text not null check(variant in ('A','B')),
 hold_year integer not null,
 payload jsonb not null,
 evidence_sha256 text not null check(evidence_sha256 ~ '^[0-9a-f]{64}$'),
 created_at timestamptz not null default now(),
 primary key(variant,hold_year)
);
create table if not exists public.live_strategy_results (
 variant text not null check(variant in ('A','B')),
 kind text not null check(kind in ('equity','preview')),
 source_as_of timestamptz not null,
 quote_version text not null check(quote_version ~ '^[0-9a-f]{64}$'),
 input_sha256 text not null check(input_sha256 ~ '^[0-9a-f]{64}$'),
 payload jsonb not null,
 evidence jsonb not null,
 created_at timestamptz not null default now(),
 primary key(variant,kind,quote_version,input_sha256)
);
create or replace function public.guard_live_strategy_evidence() returns trigger language plpgsql as $$
begin raise exception 'Runtime financial evidence is append-only'; end $$;
drop trigger if exists live_anchor_immutable on public.live_strategy_anchor;
create trigger live_anchor_immutable before update or delete or truncate on public.live_strategy_anchor
 for each statement execute function public.guard_live_strategy_evidence();
drop trigger if exists live_results_immutable on public.live_strategy_results;
create trigger live_results_immutable before update or delete or truncate on public.live_strategy_results
 for each statement execute function public.guard_live_strategy_evidence();
alter table public.live_strategy_anchor enable row level security;
alter table public.live_strategy_results enable row level security;
revoke all on public.live_strategy_anchor,public.live_strategy_results from anon,authenticated;
grant select on public.live_strategy_anchor,public.live_strategy_results to anon,authenticated;
drop policy if exists live_anchor_read on public.live_strategy_anchor;
create policy live_anchor_read on public.live_strategy_anchor for select to anon,authenticated using(true);
drop policy if exists live_results_read on public.live_strategy_results;
create policy live_results_read on public.live_strategy_results for select to anon,authenticated using(true);
create or replace view public.v_live_strategy_results with(security_invoker=true) as
 select distinct on(variant,kind) variant,kind,source_as_of,quote_version,input_sha256,payload,created_at
 from public.live_strategy_results order by variant,kind,source_as_of desc,created_at desc,input_sha256;
grant select on public.v_live_strategy_results to anon,authenticated;
commit;
