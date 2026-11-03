-- 选源决策表 + 视图（替代 11.7 万行的 series_selected 实体表）
--
-- 背景（架构师审计 P0-03）：
--   锁只挂在 prices_monthly，而站点读的是每 6 小时 DROP+RENAME 重建的 series_selected，
--   所以「锁定之后仍能通过重跑选源改历史」。根因是把「决策」物化成了数据。
--
-- 修法：
--   series_pick 只存决策（847 行：每票用哪个源），v_series 用视图实时 join 出序列。
--   series_selected 变成视图（同名），所有读代码零改动。
--   加触发器：已锁标的的选源不允许被改写。

create table if not exists series_pick (
  symbol     text primary key,
  source     text not null,
  basis      text,
  chosen_at  timestamptz default now(),
  frozen     boolean default false,
  reason     text
);

-- 首次从现有物化表回填决策（只在这些票还没决策时）
insert into series_pick(symbol, source, basis, reason)
select distinct on (symbol) symbol, source, basis, 'bootstrap from series_selected'
from series_selected
order by symbol, month desc
on conflict (symbol) do nothing;

-- 视图：每票按决策取源
create or replace view v_series as
select p.symbol, p.month, p.adj_close, p.raw_close, p.source, p.basis
from prices_monthly p
join series_pick k on k.symbol = p.symbol and k.source = p.source
where p.month >= '2008-01';

-- 选源冻结守卫：已锁标的或被标记 frozen 的，不允许换源
create or replace function guard_series_pick() returns trigger as $$
begin
  if exists (select 1 from locked_symbols where symbol = new.symbol)
     or (tg_op = 'UPDATE' and old.frozen) then
    if tg_op = 'UPDATE' and new.source <> old.source then
      raise exception 'symbol % 的选源已冻结（% -> %）', new.symbol, old.source, new.source;
    end if;
  end if;
  return new;
end $$ language plpgsql;

drop trigger if exists trg_guard_pick on series_pick;
create trigger trg_guard_pick before update on series_pick
for each row execute function guard_series_pick();

alter table series_pick enable row level security;
grant select on series_pick to anon, authenticated;
