-- 050 binds trg_guard_pick to guard_frozen_pick, replacing 020's function.
-- Preserve its active data_lock guard and also enforce legacy locks/frozen
-- decisions. Same-source basis edits and key renames must not bypass locks.
create or replace function public.guard_frozen_pick() returns trigger
language plpgsql set search_path = public, pg_temp as $$
begin
  if exists (select 1 from public.data_lock l
              where l.status='locked'
                and (l.symbol=old.symbol or (tg_op='UPDATE' and l.symbol=new.symbol))
                and not exists (select 1 from public.data_lock x
                                 where x.supersedes_id=l.id and x.status='void')) then
    raise exception '选源已冻结：% 存在有效锁，换源必须插入新锁版本并留痕', old.symbol;
  end if;
  if old.frozen or exists (
       select 1 from public.locked_symbols l where l.symbol=old.symbol
       or (tg_op='UPDATE' and l.symbol=new.symbol)) then
    if tg_op='DELETE' then
      raise exception 'symbol % 的选源已冻结，不能删除', old.symbol;
    end if;
    if (new.symbol,new.source,new.basis) is distinct from (old.symbol,old.source,old.basis)
       or (old.frozen and not new.frozen) then
      raise exception 'symbol % 的选源或收益口径已冻结', old.symbol;
    end if;
  end if;
  if tg_op='DELETE' then return old; end if;
  return new;
end $$;

drop trigger if exists trg_guard_pick on public.series_pick;
create trigger trg_guard_pick before update or delete on public.series_pick
  for each row execute function public.guard_frozen_pick();
