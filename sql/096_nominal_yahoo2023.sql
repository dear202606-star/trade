-- Add a separately identified fixed event snapshot for screening observations.
-- Return-price sources and source picks remain unchanged.
alter table public.nominal_monthly drop constraint if exists nominal_monthly_source_check;
alter table public.nominal_monthly add constraint nominal_monthly_source_check
  check (source in ('sina_daily','quandl_wiki','kaggle_yfinance2022',
                   'alphavantage_native','kaggle_yfinance2023'));

create or replace view public.v_nominal_monthly as
  select distinct on(n.symbol,n.month) n.* from public.nominal_monthly n
  join public.series_selected p using(symbol,month)
  where n.unit_evidence is not null
  order by n.symbol,n.month,case n.source
    when 'alphavantage_native' then 0 when 'quandl_wiki' then 1
    when 'kaggle_yfinance2022' then 2 when 'kaggle_yfinance2023' then 3 else 4 end;
grant select on public.v_nominal_monthly to anon, authenticated;
