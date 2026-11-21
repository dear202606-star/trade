-- User decision 2026-11-21: delisted index members whose only outstanding gap
-- is Yahoo answering "Not Found" are closed out on price_only and stop being
-- re-fetched. Yahoo returns 404 authoritatively — unlike a relay outage, no
-- retry can ever produce a row — yet 65 such symbols stayed in
-- fill_yahoo_gaps.targets() forever and were re-queried every 6 hours.
--
-- This table records a SOURCE AVAILABILITY VERDICT, nothing else. It never
-- replaces a price and never relaxes a quality bar: db_select.compute_picks
-- already selects price_only whenever no source clears the total_return
-- coverage floor (db_select.py:63-72), so accepting price_only for these names
-- changes no historical number that was already reviewed.
--
-- Original prices, series_pick, data_lock, locked_symbols and members are
-- untouched. series_quarantine is deliberately NOT reused for this: it also
-- excludes a symbol from historical source selection (db_select.py:60 and
-- sql/095_security_history_windows.sql:26), which would silently delete ~135
-- members from the backtest instead of closing one fetch gap.
--
-- Revocation is not a delete. targets() honors a verdict only while the symbol
-- is in fact still delisted (members.end_date in the past), so a re-listed
-- constituent re-enters the backfill set on its own.

create table if not exists public.source_absence_acceptance (
  symbol text not null,
  source text not null,
  basis text not null,
  accepted_basis text not null default 'price_only'
    check (accepted_basis = 'price_only'),
  reason text not null
    check (reason = 'provider_not_found'),
  http_status integer not null check (http_status between 400 and 499),
  membership_end date not null,
  observed_on date not null default (current_date),
  runner text not null,
  decided_at timestamptz not null default now(),
  primary key (symbol, source, basis)
);

-- Append-only: a verdict is added, never edited or removed. Correcting a
-- verdict means inserting evidence elsewhere (identity review, membership) and
-- letting targets() re-derive eligibility from it.
create or replace function public.guard_source_absence_acceptance()
returns trigger language plpgsql set search_path=public,pg_temp as $$
begin
  if tg_op = 'INSERT' then
    if coalesce(new.symbol,'') = '' or coalesce(new.source,'') = ''
       or coalesce(new.basis,'') = '' or coalesce(new.runner,'') = '' then
      raise exception 'Source absence verdict needs symbol, source, basis and runner';
    end if;
    if new.membership_end >= current_date then
      raise exception 'Only a delisted member may be closed out as absent: %', new.symbol;
    end if;
    return new;
  end if;
  raise exception 'Source absence acceptance is append-only';
end $$;

drop trigger if exists source_absence_append_only on public.source_absence_acceptance;
create trigger source_absence_append_only
  before update or delete on public.source_absence_acceptance
  for each row execute function public.guard_source_absence_acceptance();

-- membership_end must agree with the reviewed index membership at insert time;
-- a verdict that names a still-listed member is rejected, not silently ignored.
create or replace function public.check_source_absence_membership()
returns trigger language plpgsql set search_path=public,pg_temp as $$
declare live_end date;
begin
  select max(m.end_date) into live_end from public.members m where m.symbol = new.symbol;
  if live_end is null then
    raise exception 'Source absence verdict for % has no index membership to date', new.symbol;
  end if;
  if live_end <> new.membership_end or live_end >= current_date then
    raise exception 'Source absence verdict for % requires the delisted membership end date (members.end_date = %)',
      new.symbol, live_end;
  end if;
  return new;
end $$;

drop trigger if exists source_absence_membership on public.source_absence_acceptance;
create trigger source_absence_membership
  before insert on public.source_absence_acceptance
  for each row execute function public.check_source_absence_membership();
