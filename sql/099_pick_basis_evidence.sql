-- Old clients must not reintroduce a total-return label on price-only rows.
create or replace function public.guard_pick_basis_evidence() returns trigger
language plpgsql set search_path = public, pg_temp as $$
declare
  observed_count bigint;
  observed_basis text;
begin
  select count(*),case when bool_and(p.basis='total_return')
                      then 'total_return' else 'price_only' end
    into observed_count,observed_basis
    from public.prices_monthly p
    where p.symbol=new.symbol and p.source=new.source and p.month>='2008-01';
  if observed_count=0 then
    raise exception '选源 %/% 没有可核验价格记录', new.symbol,new.source;
  end if;
  if new.basis is distinct from observed_basis then
    raise exception '选源 %/% 口径标签 % 与实际 % 不符',
                    new.symbol,new.source,new.basis,observed_basis;
  end if;
  return new;
end $$;

drop trigger if exists trg_guard_pick_basis on public.series_pick;
create trigger trg_guard_pick_basis before insert or update on public.series_pick
  for each row execute function public.guard_pick_basis_evidence();
