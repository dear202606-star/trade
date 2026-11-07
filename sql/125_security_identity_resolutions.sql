-- Reviewed identity resolutions are evidence of a decision, never replacement
-- prices. A row here means "this quarantine was reviewed and must be kept";
-- it does NOT release the quarantine, accept the price history, or change
-- full_history_accepted. Original prices, series_pick, data_lock and members
-- are never touched by this migration.
--
-- Applied only after a rollback rehearsal matches the evidence, per
-- PROJECT_GOAL ("record impact and ask the user before overriding locked
-- history"). Default path in research/security_identity_resolutions.py is
-- rollback; --write is a separate, explicit decision.

create table if not exists public.security_identity_resolution (
  symbol text primary key,
  resolution text not null
    check (resolution in ('exclude_reviewed','pending_evidence')),
  membership_end date,
  proof jsonb not null,
  evidence_sha256 text not null,
  decided_by text not null,
  decided_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

-- Append-only: a resolution may be added, never edited or removed. The
-- Python reader re-computes sha256 over the canonical proof and refuses
-- anything that does not match, so a stale or tampered row fails closed.
create or replace function public.guard_security_identity_resolution()
returns trigger language plpgsql set search_path=public,pg_temp as $$
begin
  if tg_op = 'INSERT' then
    if new.resolution not in ('exclude_reviewed','pending_evidence') then
      raise exception 'Unknown security identity resolution';
    end if;
    if coalesce(new.evidence_sha256,'') = '' or new.proof is null then
      raise exception 'Identity resolution requires proof and evidence digest';
    end if;
    return new;
  end if;
  raise exception 'Reviewed identity resolutions are append-only';
end $$;

create or replace function public.guard_security_identity_resolution_truncate()
returns trigger language plpgsql set search_path=public,pg_temp as $$
begin
  raise exception 'Reviewed identity resolutions cannot be truncated';
end $$;

drop trigger if exists security_identity_resolution_immutable
  on public.security_identity_resolution;
create trigger security_identity_resolution_immutable
before insert or update or delete on public.security_identity_resolution
for each row execute function public.guard_security_identity_resolution();

drop trigger if exists security_identity_resolution_no_truncate
  on public.security_identity_resolution;
create trigger security_identity_resolution_no_truncate
before truncate on public.security_identity_resolution
for each statement execute function public.guard_security_identity_resolution_truncate();
