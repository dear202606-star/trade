-- Mutable current quotes are independent of immutable historical evidence.
create table if not exists public.live_quote_snapshot (
 singleton boolean primary key default true check(singleton),
 source_as_of timestamptz not null,
 collected_at timestamptz not null,
 evidence_sha256 text not null check(evidence_sha256 ~ '^[0-9a-f]{64}$'),
 payload jsonb not null check(coalesce(jsonb_typeof(payload)='object'
   and payload->>'ok'='true' and jsonb_typeof(payload->'quotes')='object'
   and payload->'quotes' ? 'SPY' and payload ? 'ts',false))
);
alter table public.live_quote_snapshot enable row level security;
revoke all on public.live_quote_snapshot from anon,authenticated;
grant select on public.live_quote_snapshot to anon,authenticated;
drop policy if exists live_quote_read on public.live_quote_snapshot;
create policy live_quote_read on public.live_quote_snapshot
 for select to anon,authenticated using (true);
