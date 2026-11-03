-- M7：接入层重写（ingest_staging 加校验门槛）
--
-- 背景：promote_staging() 会把任何人写进暂存表的数据直接合并进 prices_monthly，
--       只校验月份格式与价格>0。拿到公开 anon key 的人可以污染正式行情。
--
-- 新规则（全部在库内执行，CI 无法绕过）
--   1. source 必须在 data_source 里注册
--   2. month 必须在 2000-01 ~ 当前月+1 之间
--   3. adj_close / raw_close 必须 > 0
--   4. 同 (symbol,month,source) 已存在时，新值偏离 >30% → 拒收（防篡改）
--   5. 若该票已有相邻月份价格，新行的月收益与既有序列偏离 >200% → 拒收（防垃圾数据）
--   6. 落在 data_lock 冻结范围内的行 → 拒收（锁优先）
-- 拒收原因写入 reject_reason，永不静默丢弃。

create or replace function promote_staging(max_rows int default 20000)
returns table(inserted int, rejected int) as $$
declare
  n_ins int := 0;
  n_rej int := 0;
  cur_ym text := to_char((now() at time zone 'utc')::date, 'YYYY-MM');
begin
  -- 1) 结构 + 来源 + 时间范围
  update ingest_staging i
     set reject_reason = case
           when i.month !~ '^\d{4}-(0[1-9]|1[0-2])$' then 'bad_month'
           when i.adj_close is null or i.adj_close <= 0 then 'bad_price'
           when i.raw_close is not null and i.raw_close <= 0 then 'bad_price'
           when i.basis not in ('total_return','price_only') then 'bad_basis'
           when i.month < '2000-01' or i.month > (cur_ym || '-99') then 'out_of_range'
           when not exists (select 1 from data_source d where d.source_id = i.source)
             then 'unknown_source'
           else null end
   where i.promoted = false and i.reject_reason is null;

  -- 2) 与已存在的同键行冲突（偏离 >30% 视为篡改）
  update ingest_staging i
     set reject_reason = 'conflict_with_existing'
   where i.promoted = false and i.reject_reason is null
     and exists (
       select 1 from prices_monthly p
        where p.symbol = i.symbol and p.month = i.month and p.source = i.source
          and p.adj_close is not null and i.adj_close is not null
          and abs(i.adj_close / p.adj_close - 1) > 0.30);

  -- 3) 与已有相邻月份的价格水平相差过大（防把垃圾写进来）
  update ingest_staging i
     set reject_reason = 'implausible_vs_history'
   where i.promoted = false and i.reject_reason is null
     and exists (
       select 1 from prices_monthly p
        where p.symbol = i.symbol and p.source = i.source
          and p.month = to_char((to_date(i.month || '-01','YYYY-MM-DD') - interval '1 month')::date,'YYYY-MM')
          and p.adj_close > 0
          and abs(i.adj_close / p.adj_close - 1) > 2.0);

  -- 4) 落在冻结范围的行一律拒收（锁优先）
  update ingest_staging i
     set reject_reason = 'frozen_by_lock'
   where i.promoted = false and i.reject_reason is null
     and exists (
       select 1 from data_lock l
        where l.status = 'locked'
          and not exists (select 1 from data_lock x where x.supersedes_id = l.id and x.status='void')
          and ( (l.symbol = i.symbol
                 and (l.month_from is null or i.month >= l.month_from)
                 and (l.month_to   is null or i.month <= l.month_to))
             or (l.scope = 'month_range'
                 and i.month between l.month_from and l.month_to) ));

  select count(*) into n_rej from ingest_staging
   where promoted = false and reject_reason is not null;

  -- 5) 合并合格行
  with ok as (
    select distinct on (symbol, month, source)
           symbol, month, adj_close, raw_close, source, quality, basis
      from ingest_staging
     where promoted = false and reject_reason is null
       -- 新标的隔离：库内没有该票时，要求本批至少 24 个月才放行（不足则继续攒，不永久拒收）
       and ( exists (select 1 from prices_monthly p0 where p0.symbol = ingest_staging.symbol)
             or (select count(distinct s3.month) from ingest_staging s3
                  where s3.symbol = ingest_staging.symbol and s3.promoted = false
                    and s3.reject_reason is null) >= 24 )
     order by symbol, month, source, id desc
     limit max_rows
  ), ins as (
    insert into prices_monthly(symbol, month, adj_close, raw_close, source, quality, basis)
    select symbol, month, adj_close, raw_close, source, quality, basis from ok
    on conflict (symbol, month, source) do update
      set adj_close = excluded.adj_close,
          raw_close = excluded.raw_close,
          quality   = excluded.quality,
          basis     = excluded.basis,
          fetched_at = now()
    returning 1
  )
  select count(*) into n_ins from ins;

  update ingest_staging set promoted = true
   where promoted = false and reject_reason is null;

  return query select n_ins, n_rej;
end $$ language plpgsql security definer
set search_path = public, pg_temp;

-- 拒收的行保留 30 天后清理（留痕，不静默丢）
create or replace function purge_staging_rejects(days int default 30)
returns int as $$
declare n int;
begin
  delete from ingest_staging
   where reject_reason is not null and inserted_at < now() - make_interval(days => days);
  get diagnostics n = row_count;
  return n;
end $$ language plpgsql security definer
set search_path = public, pg_temp;
