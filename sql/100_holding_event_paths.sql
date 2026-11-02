-- Derived holding values are separate from quoted prices and historical locks.
-- New reviews append evidence versions; old evidence is never overwritten.
create table if not exists public.holding_event_paths (
  id bigint generated always as identity primary key,
  symbol text not null,
  hold_year integer not null check(hold_year between 2008 and 2100),
  event_date date not null,
  policy text not null check(policy='cash_and_all_successor_shares'),
  monthly_values jsonb not null check(jsonb_typeof(monthly_values)='array' and jsonb_array_length(monthly_values)=12),
  evidence jsonb not null check(jsonb_typeof(evidence)='object'),
  evidence_sha256 text not null check(evidence_sha256 ~ '^[0-9a-f]{64}$'),
  calculator_sha256 text not null check(calculator_sha256 ~ '^[0-9a-f]{64}$'),
  verified boolean not null default false,
  created_at timestamptz not null default now(),
  check(extract(year from event_date)=hold_year),
  unique(symbol,hold_year,evidence_sha256,calculator_sha256)
);
alter table public.holding_event_paths enable row level security;
revoke all on public.holding_event_paths from anon,authenticated;

create or replace function public.guard_holding_event_evidence() returns trigger
language plpgsql as $$ begin
  raise exception 'holding-event evidence is append-only; insert a new reviewed version';
end $$;
drop trigger if exists trg_holding_event_immutable on public.holding_event_paths;
create trigger trg_holding_event_immutable before update or delete on public.holding_event_paths
  for each row execute function public.guard_holding_event_evidence();
drop trigger if exists trg_holding_event_no_truncate on public.holding_event_paths;
create trigger trg_holding_event_no_truncate before truncate on public.holding_event_paths
  for each statement execute function public.guard_holding_event_evidence();

create or replace view public.v_holding_event_paths as
 select distinct on(symbol,hold_year) * from public.holding_event_paths
 where policy='cash_and_all_successor_shares'
 order by symbol,hold_year,created_at desc,id desc;
grant select on public.v_holding_event_paths to anon,authenticated;

-- The last CVC quote is June 20, not a June month-end holding value. Cash
-- consideration from June 21 is valued by the separate evidence path.
insert into public.security_history_window(symbol,month_to,evidence_url,reason) values
 ('CVC','2016-05','https://www.sec.gov/Archives/edgar/data/784681/000094787116001286/ss1506472_8k.htm',
  'Cablevision shares converted to USD34.90 cash before trading on 2016-06-21; partial June quotes are not month-end holdings')
on conflict(symbol) do nothing;
