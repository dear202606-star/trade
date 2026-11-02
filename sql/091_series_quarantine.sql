-- Retain prices and source decisions, but keep unreviewed series out of all
-- selected-data readers. Older collectors cannot clear this by reimporting.
-- The read-only B sensitivity run found no 2010-2025 holding/return changes.
-- Remove a row only after issuer/price review; the underlying data is intact.
begin;
create table if not exists series_quarantine (
  symbol text primary key,
  reason text not null,
  created_at timestamptz not null default now()
);
alter table series_quarantine enable row level security;

insert into series_quarantine(symbol, reason)
select symbol, 'Unreviewed >10x or <0.01x Sina jump within membership; see output/selected_jump_audit.json. May include genuine events; not a price correction.'
from unnest(array['BMC','CBE','COV','GAS','HAR','PCL','SBNY','SCG','TIE']) as symbol
on conflict (symbol) do nothing;

insert into series_quarantine(symbol, reason)
values ('PARA', 'HF selected values 107090 and 18800 in 2023-11/12 disagree with Sina USD 14.37 and 14.79; issuer/currency identity unverified. See output/selected_jump_audit.json.')
on conflict (symbol) do nothing;

insert into series_quarantine(symbol, reason)
values ('EP', 'Member universe is El Paso (delisted 2012); current EP is Empire Petroleum (ticker since 2022). Historical issuer identity unverified; SEC references in docs/ARCHIVE_IDENTITY_REVIEW.md.')
on conflict (symbol) do nothing;

create or replace view v_series as
select p.symbol, p.month, p.adj_close, p.raw_close, p.source, p.basis
from prices_monthly p
join series_pick k on k.symbol=p.symbol and k.source=p.source
where p.month >= '2008-01'
  and not exists(select 1 from series_quarantine q where q.symbol=p.symbol);
commit;
