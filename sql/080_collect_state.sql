-- 采集进度看板：一眼看清每个环节「还剩多少」，避免重复处理已完成的
--
-- 背景：所有采集脚本改为「增量」后，需要一个统一的地方回答：
--   哪些标的已验证完？哪些还缺口？卡在哪个环节？

create or replace view public.v_collect_state with (security_invoker = true) as
with need as (
  -- 需求边界：2010-2025 曾进过指数、或库里有价格
  select distinct symbol from public.members where end_date >= '2009-06-30'
  union
  select distinct symbol from public.prices_monthly
), have as (
  select symbol,
         count(*) filter (where basis = 'total_return'
                            and month between '2010-01' and '2025-12') as tr_months,
         count(*) filter (where month between '2010-01' and '2025-12') as all_months,
         max(fetched_at) as last_fetch
    from public.prices_monthly group by symbol
), yahoo as (
  select symbol, count(*) as n from public.prices_monthly
   where source = 'yahoo_relay' and basis = 'total_return'
   group by symbol
), acts as (
  select symbol, count(*) as n from public.corporate_actions group by symbol
), locks as (
  select l.symbol from public.data_lock l
   where l.scope = 'symbol_month' and l.status = 'locked'
     and not exists (select 1 from public.data_lock x
                      where x.supersedes_id = l.id and x.status = 'void')
)
select
  n.symbol,
  coalesce(h.tr_months, 0)  as tr_months,
  coalesce(h.all_months, 0) as all_months,
  coalesce(y.n, 0)          as yahoo_months,
  coalesce(a.n, 0)          as action_events,
  (l.symbol is not null)    as is_locked,
  h.last_fetch,
  case
    when l.symbol is not null                       then 'done_locked'
    when coalesce(h.tr_months, 0) >= 150            then 'ready_to_lock'
    when coalesce(h.tr_months, 0) > 0               then 'partial_tr'
    when coalesce(h.all_months, 0) > 0              then 'price_only_only'
    else                                                 'no_data'
  end as stage
from need n
left join have  h on h.symbol = n.symbol
left join yahoo y on y.symbol = n.symbol
left join acts  a on a.symbol = n.symbol
left join locks l on l.symbol = n.symbol;

comment on view public.v_collect_state is
  '每个标的的采集进度与卡点；stage 用于决定下一步该跑哪个采集器';

create or replace view public.v_collect_summary with (security_invoker = true) as
select stage,
       count(*) as symbols,
       count(*) filter (where action_events = 0) as need_actions
  from public.v_collect_state
 group by stage order by stage;

grant select on public.v_collect_state to anon, authenticated;
grant select on public.v_collect_summary to anon, authenticated;
