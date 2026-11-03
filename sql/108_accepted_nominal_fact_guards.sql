-- Only explicit, fully reviewed closing-fact receipts freeze a point.
-- A unit_evidence string alone is not complete acceptance.
create index if not exists sys_event_nominal_fact_receipts
on public.sys_event(subject, (metrics->>'evidence_sha256'))
where kind='validation' and status='pass'
  and source in ('verified_closing_fact','fund_native_closing_fact');

create or replace function public.guard_nominal_fact_receipt() returns trigger
language plpgsql set search_path=public,pg_temp as $$
declare s text; d date; prior record;
begin
  if tg_op='TRUNCATE' then
    if exists(select 1 from sys_event where kind='validation' and status='pass'
        and source in ('verified_closing_fact','fund_native_closing_fact')) then
      raise exception 'Accepted nominal evidence cannot be truncated';
    end if;
    return null;
  end if;
  if tg_op<>'INSERT' then
    if old.kind='validation' and old.status='pass'
       and old.source in ('verified_closing_fact','fund_native_closing_fact') then
      raise exception 'Accepted nominal evidence is append-only';
    end if;
    if tg_op='DELETE' then return old; end if;
    return new;
  end if;
  if new.kind<>'validation' or new.status is distinct from 'pass'
     or coalesce(new.source,'') not in ('verified_closing_fact','fund_native_closing_fact') then
    return new;
  end if;
  s=split_part(new.subject,':',1);
  d=split_part(new.subject,':',2)::date;
  if s is null or s='' or d is null or new.subject is distinct from s||':'||d::text
     or jsonb_typeof(new.metrics->'proof') is distinct from 'object'
     or coalesce(new.metrics->>'evidence_sha256','') !~ '^[0-9a-f]{64}$' then
    raise exception 'Invalid nominal acceptance receipt';
  end if;
  perform pg_advisory_xact_lock(hashtext('nominal_fact:'||s||':'||to_char(d,'YYYY-MM'))::bigint);
  select metrics into prior from sys_event
    where kind='validation' and status='pass'
      and source in ('verified_closing_fact','fund_native_closing_fact')
      and split_part(subject,':',1)=s
      and substr(split_part(subject,':',2),1,7)=to_char(d,'YYYY-MM') limit 1;
  if found and prior.metrics is distinct from new.metrics then
    raise exception 'Nominal point already accepted; explicit version review required';
  end if;
  perform 1 from nominal_monthly where symbol=s and month=to_char(d,'YYYY-MM')
    and source='eastmoney_native' and trade_date=d and unit_evidence is not null
    and evidence_sha256=new.metrics->>'evidence_sha256' for share;
  if not found then raise exception 'Receipt has no matching reviewed nominal row'; end if;
  return new;
end $$;

drop trigger if exists nominal_fact_receipt_guard on public.sys_event;
create trigger nominal_fact_receipt_guard before insert or update or delete on public.sys_event
for each row execute function public.guard_nominal_fact_receipt();
drop trigger if exists nominal_fact_receipt_truncate_guard on public.sys_event;
create trigger nominal_fact_receipt_truncate_guard before truncate on public.sys_event
for each statement execute function public.guard_nominal_fact_receipt();

create or replace function public.guard_accepted_nominal_row() returns trigger
language plpgsql set search_path=public,pg_temp as $$
begin
  if tg_op='TRUNCATE' then
    if exists(select 1 from sys_event where kind='validation' and status='pass'
        and source in ('verified_closing_fact','fund_native_closing_fact')) then
      raise exception 'Accepted nominal prices cannot be truncated';
    end if;
    return null;
  end if;
  perform pg_advisory_xact_lock(hashtext('nominal_fact:'||old.symbol||':'||old.month)::bigint);
  if old.source='eastmoney_native' and exists(select 1 from sys_event e
      where e.kind='validation' and e.status='pass'
        and e.source in ('verified_closing_fact','fund_native_closing_fact')
        and e.subject=old.symbol||':'||old.trade_date::text
        and e.metrics->>'evidence_sha256'=old.evidence_sha256) then
    raise exception 'Accepted nominal price and provenance are immutable';
  end if;
  if tg_op='DELETE' then return old; end if;
  return new;
end $$;
drop trigger if exists accepted_nominal_row_guard on public.nominal_monthly;
create trigger accepted_nominal_row_guard before update or delete on public.nominal_monthly
for each row execute function public.guard_accepted_nominal_row();
drop trigger if exists accepted_nominal_truncate_guard on public.nominal_monthly;
create trigger accepted_nominal_truncate_guard before truncate on public.nominal_monthly
for each statement execute function public.guard_accepted_nominal_row();

create or replace view public.v_nominal_monthly as
with accepted as materialized (
  select distinct n.symbol,n.month,n.source,n.evidence_sha256
  from sys_event e join nominal_monthly n
    on n.symbol=split_part(e.subject,':',1)
   and n.month=substr(split_part(e.subject,':',2),1,7)
   and n.trade_date::text=split_part(e.subject,':',2)
   and n.source='eastmoney_native' and n.unit_evidence is not null
   and n.evidence_sha256=e.metrics->>'evidence_sha256'
  where e.kind='validation' and e.status='pass'
    and e.source in ('verified_closing_fact','fund_native_closing_fact')
)
select distinct on(n.symbol,n.month) n.* from nominal_monthly n
join series_selected p using(symbol,month)
left join accepted a on a.symbol=n.symbol and a.month=n.month
where n.unit_evidence is not null
  and (a.symbol is null or (n.source=a.source and n.evidence_sha256=a.evidence_sha256))
order by n.symbol,n.month,case n.source when 'alphavantage_native' then 0
  when 'quandl_wiki' then 1 when 'kaggle_yfinance2022' then 2
  when 'kaggle_yfinance2023' then 3 when 'kaggle_tech2026' then 4
  when 'eastmoney_native' then 5 else 6 end;
grant select on public.v_nominal_monthly to anon,authenticated;
