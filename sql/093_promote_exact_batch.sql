-- Preserve the existing RPC signature. Only mark keys actually written;
-- overflow and new-symbol batches below 24 months must remain pending.
create index if not exists idx_staging_pending_key
  on ingest_staging(symbol,month,source,id desc)
  where not promoted and reject_reason is null;
analyze ingest_staging;

create or replace function public.promote_staging(max_rows integer default 20000)
returns table(inserted integer, rejected integer)
language plpgsql security definer set search_path to public, pg_temp
as $$
declare
  n_ins integer := 0;
  n_rej integer := 0;
  cur_ym text := to_char((now() at time zone 'utc')::date, 'YYYY-MM');
begin
  if max_rows is null or max_rows < 1 or max_rows > 20000 then
    raise exception 'max_rows must be between 1 and 20000';
  end if;
  perform pg_advisory_xact_lock(hashtext('trade_promote_staging')::bigint);
  -- New inserts wait until validation and marking finish, so a row cannot
  -- arrive between those statements and enter the batch without validation.
  lock table ingest_staging in share row exclusive mode;

  update ingest_staging i set reject_reason = case
    when i.month is null or i.month !~ '^\d{4}-(0[1-9]|1[0-2])$' then 'bad_month'
    when i.adj_close is null or i.adj_close <= 0
      or i.adj_close::text in ('NaN','Infinity','-Infinity') then 'bad_price'
    when i.raw_close is not null and (i.raw_close <= 0
      or i.raw_close::text in ('NaN','Infinity','-Infinity')) then 'bad_price'
    when i.basis is null or i.basis not in ('total_return','price_only') then 'bad_basis'
    when i.month < '2000-01' or i.month > cur_ym then 'out_of_range'
    when not exists(select 1 from data_source d where d.source_id=i.source) then 'unknown_source'
    else null end
  where not i.promoted and i.reject_reason is null;

  update ingest_staging i set reject_reason='conflict_with_existing'
  where not i.promoted and i.reject_reason is null and exists(
    select 1 from prices_monthly p where p.symbol=i.symbol and p.month=i.month
      and p.source=i.source and p.adj_close>0 and abs(i.adj_close/p.adj_close-1)>.30);

  update ingest_staging i set reject_reason='implausible_vs_history'
  where not i.promoted and i.reject_reason is null and exists(
    select 1 from prices_monthly p where p.symbol=i.symbol and p.source=i.source
      and p.month=to_char((to_date(i.month||'-01','YYYY-MM-DD')-interval '1 month')::date,'YYYY-MM')
      and p.adj_close>0 and abs(i.adj_close/p.adj_close-1)>2.0);

  update ingest_staging i set reject_reason='frozen_by_lock'
  where not i.promoted and i.reject_reason is null and (
    exists(select 1 from locked_symbols l where l.symbol=i.symbol)
    or exists(
      select 1 from data_lock l where l.status='locked'
        and not exists(select 1 from data_lock x where x.supersedes_id=l.id and x.status='void')
        and ((l.symbol=i.symbol and (l.month_from is null or i.month>=l.month_from)
              and (l.month_to is null or i.month<=l.month_to))
          or (l.scope='month_range' and i.month between l.month_from and l.month_to))));

  select count(*) into n_rej from ingest_staging where not promoted and reject_reason is not null;

  with ok as materialized (
    select distinct on (symbol,month,source)
      id,symbol,month,adj_close,raw_close,source,quality,basis
    from ingest_staging i where not promoted and reject_reason is null
      and (exists(select 1 from prices_monthly p where p.symbol=i.symbol)
        or (select count(distinct s.month) from ingest_staging s
            where s.symbol=i.symbol and not s.promoted and s.reject_reason is null)>=24)
    order by symbol,month,source,id desc limit max_rows
  ), ins as (
    insert into prices_monthly(symbol,month,adj_close,raw_close,source,quality,basis)
    select symbol,month,adj_close,raw_close,source,quality,basis from ok
    on conflict(symbol,month,source) do update set
      adj_close=excluded.adj_close,raw_close=excluded.raw_close,quality=excluded.quality,
      basis=excluded.basis,fetched_at=now()
    returning symbol,month,source
  ), marked as (
    update ingest_staging i set promoted=true
    from ok join ins using(symbol,month,source)
    where not i.promoted and i.reject_reason is null
      and i.symbol=ok.symbol and i.month=ok.month and i.source=ok.source and i.id<=ok.id
    returning i.id
  )
  select count(*) into n_ins from ins;
  return query select n_ins,n_rej;
end $$;
