-- Identity facts are online reference data, separate from price/source picks.
create table if not exists public.verified_ticker_renames (
 old_symbol text not null,
 current_symbol text not null,
 effective_date date not null,
 evidence_url text not null check(evidence_url like 'https://%'),
 evidence_sha256 text not null check(evidence_sha256 ~ '^[0-9a-f]{64}$'),
 verified boolean not null default false,
 created_at timestamptz not null default now(),
 primary key(old_symbol,current_symbol,effective_date),
 check(old_symbol<>current_symbol)
);
alter table public.verified_ticker_renames enable row level security;
revoke all on public.verified_ticker_renames from anon,authenticated;
create or replace view public.v_verified_ticker_renames as
 select old_symbol,current_symbol,effective_date,evidence_url,evidence_sha256
 from public.verified_ticker_renames where verified;
grant select on public.v_verified_ticker_renames to anon,authenticated;
