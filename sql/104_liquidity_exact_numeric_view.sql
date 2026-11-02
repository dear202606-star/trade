-- Float text serialization depends on server settings. Project exact decimal
-- evidence only after enforcing equality to the stored binary numeric values.
alter table public.liquidity_windows add constraint liquidity_summary_totals_match check (
 trading_days=(evidence->'summary'->>'trading_days')::integer and
 primary_amount=(evidence->'summary'->>'primary_amount')::double precision and
 independent_lower=(evidence->'summary'->>'independent_lower')::double precision and
 independent_upper=(evidence->'summary'->>'independent_upper')::double precision and
 evidence->'summary' ?& array['trading_days','primary_amount','independent_lower','independent_upper']
);
drop view public.v_liquidity_windows;
create view public.v_liquidity_windows as
 select distinct on(n.symbol,n.base_month,n.rank_month)
 n.id,n.symbol,n.base_month,n.rank_month,n.trading_days,
 (n.evidence->'summary'->>'primary_amount')::numeric as primary_amount,
 (n.evidence->'summary'->>'independent_lower')::numeric as independent_lower,
 (n.evidence->'summary'->>'independent_upper')::numeric as independent_upper,
 n.evidence_sha256,n.calculator_sha256,n.evidence,n.verified,n.created_at
 from public.liquidity_windows n join public.series_selected p
 on p.symbol=n.symbol and p.month=n.rank_month
 order by n.symbol,n.base_month,n.rank_month,n.created_at desc,n.id desc;
grant select on public.v_liquidity_windows to anon,authenticated;
