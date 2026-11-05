begin;
create table if not exists public.live_ranking_context (
 symbol text not null,
 base_year integer not null,
 session date not null,
 phase text not null check(phase in ('intraday','close')),
 payload jsonb not null,
 evidence_sha256 text not null check(evidence_sha256 ~ '^[0-9a-f]{64}$'),
 created_at timestamptz not null default now(),
 primary key(symbol,base_year,session,phase)
);
drop trigger if exists ranking_context_immutable on public.live_ranking_context;
create trigger ranking_context_immutable before update or delete or truncate on public.live_ranking_context
 for each statement execute function public.guard_live_strategy_evidence();
alter table public.live_ranking_context enable row level security;
revoke all on public.live_ranking_context from anon,authenticated;
commit;
