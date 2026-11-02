-- Screening observations are separate from return prices and existing locks.
create table if not exists public.nominal_monthly (
  symbol text not null,
  month text not null check (month ~ '^\d{4}-(0[1-9]|1[0-2])$'),
  close numeric not null check (close > 0 and close::text not in ('NaN','Infinity','-Infinity')),
  trade_date date not null,
  source text not null check (source in ('sina_daily','quandl_wiki','kaggle_yfinance2022','alphavantage_native')),
  source_url text not null,
  fetched_at timestamptz not null,
  evidence_sha256 text not null check (evidence_sha256 ~ '^[0-9a-f]{64}$'),
  crosscheck_pairs integer not null check (crosscheck_pairs >= 24),
  median_log_deviation numeric not null check (median_log_deviation between 0 and .005),
  p90_log_deviation numeric not null check (p90_log_deviation between 0 and .05),
  unit_evidence text,
  primary key(symbol,month,source),
  check (trade_date::text like month || '-%')
);
-- Upgrade the preparatory table from before the source-unit audit. Existing
-- Sina observations remain stored, but cannot screen until units are proved.
alter table public.nominal_monthly add column if not exists unit_evidence text;
alter table public.nominal_monthly drop constraint if exists nominal_monthly_source_check;
alter table public.nominal_monthly add constraint nominal_monthly_source_check
  check (source in ('sina_daily','quandl_wiki','kaggle_yfinance2022','alphavantage_native'));
alter table public.nominal_monthly drop constraint if exists nominal_monthly_pkey;
alter table public.nominal_monthly add primary key(symbol,month,source);
alter table public.nominal_monthly drop constraint if exists nominal_security_window_ck;
alter table public.nominal_monthly add constraint nominal_security_window_ck check (
  unit_evidence is null or not (
    (symbol='LB' and month>='2021-08') or
    (symbol='BJS' and month>='2010-04') or
    (symbol='AYE' and month>='2011-02')));
alter table public.nominal_monthly enable row level security;
revoke all on public.nominal_monthly from anon, authenticated;

-- Same quarantine and selected-month boundary as the return-price view.
create or replace view public.v_nominal_monthly as
  select distinct on(n.symbol,n.month) n.* from public.nominal_monthly n
  join public.series_selected p using(symbol,month)
  where n.unit_evidence is not null
  order by n.symbol,n.month,case n.source
    when 'alphavantage_native' then 0 when 'quandl_wiki' then 1
    when 'kaggle_yfinance2022' then 2 else 3 end;
grant select on public.v_nominal_monthly to anon, authenticated;
