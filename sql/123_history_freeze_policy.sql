-- sql/123_history_freeze_policy.sql
--
-- 历史冻结策略：把「2025 年前已验收历史不再走采集管线」显式化、可查询、
-- 可复核。**本文件不新建、不修改、不撤销任何锁**，只提供只读判定与视图。
--
-- 背景（2026-11-07 全链路审查）
--   · sql/050_data_lock.sql 已有 962 条 symbol_month 有效锁，范围
--     2012-01 ~ 2025-12，且 guard_frozen_prices 触发器在库内硬拒改写。
--     因此 2025 年之前的历史**已经处于冻结态**，不需要再新建锁
--     （新建锁反而会与 data_lock_uq 冲突，且写锁入口已被刻意移除）。
--   · 旧消费端（fill_yahoo_gaps.targets / relay_source.has_protected_history）
--     用「存在任一锁 → 整只标的跳过」，会误伤 2026 年之后的新增月份。
--     本文件提供精确到「标的 + 月份」的判定函数，供采集侧按日期裁剪。
--
-- 关键语义：冻结的是不随时间漂移的绝对量
--   ✅ raw_close、已发生的 corporate_actions、由它们固化并验收的回测产物
--   ❌ adj_close 绝对数值 —— 前复权价会随未来分红/拆股整体重算
--      （verify_tr.py:5 / fetch_actions.py:9-10 / yahoo_fetch.py:28-29）。
--      对前复权只能锁「复权基准版本 + 可复算证据」，不能锁绝对价格。
--      现有 data_lock.fingerprint 存的是 sha256(month|adj_close) 绝对数值，
--      因此 verify_locks.py 的漂移报告必须区分「篡改」与「源端整体重算」。
--      在引入复权基准版本字段之前，本策略只把 raw 侧视为已冻结。

begin;

-- 1) 精确到月份的是否冻结判定。逻辑与 guard_frozen_prices 完全一致，
--    包含撤销链（supersedes_id → status='void'）判断，避免判定与守卫打架。
create or replace function public.is_frozen_month(p_symbol text, p_month text)
returns boolean
language sql stable
set search_path = public, pg_temp
as $$
  select exists (
    select 1 from public.data_lock l
     where l.status = 'locked'
       and not exists (
         select 1 from public.data_lock x
          where x.supersedes_id = l.id and x.status = 'void')
       and ( (l.symbol = p_symbol
              and (l.month_from is null or p_month >= l.month_from)
              and (l.month_to   is null or p_month <= l.month_to))
          or (l.scope = 'month_range'
              and p_month between l.month_from and l.month_to) )
  )
$$;
comment on function public.is_frozen_month(text,text) is
  '精确到标的+月份的冻结判定；采集侧据此按日期裁剪，禁止再整只跳过';

-- 2) 每只标的的冻结边界，供采集侧一次性装载后本地裁剪。
create or replace view public.v_frozen_month_boundaries as
select l.symbol,
       min(l.month_from) as month_from,
       max(coalesce(l.month_to, l.month_from)) as month_to,
       count(*) as lock_count
  from public.data_lock l
 where l.status = 'locked'
   and l.scope in ('symbol', 'symbol_month')
   and not exists (
     select 1 from public.data_lock x
      where x.supersedes_id = l.id and x.status = 'void')
 group by l.symbol;

-- 3) 全局冻结策略摘要：一眼看清冻结范围与覆盖规模。
create or replace view public.v_history_freeze_policy as
select '2012-01'::text as frozen_from,
       '2025-12'::text as frozen_to,
       '2026-01-01'::date as frozen_before,
       (select count(*) from public.v_frozen_month_boundaries) as locked_symbols,
       (select count(*) from public.data_lock l
         where l.status='locked') as active_locks,
       'raw_close + corporate_actions + accepted backtest artifacts'::text as frozen_objects,
       'adj_close absolute values are intentionally NOT frozen (recomputed on future corporate actions)'::text as excluded_objects,
       'read-only fingerprint review; no re-collection'::text as pipeline_behavior;

grant select on public.v_frozen_month_boundaries, public.v_history_freeze_policy to anon, authenticated;
grant execute on function public.is_frozen_month(text,text) to anon, authenticated;

commit;
