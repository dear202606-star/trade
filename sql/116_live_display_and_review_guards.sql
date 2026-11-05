begin;
-- A page needs the benchmark and current holdings, not 500 raw quote objects.
create or replace view public.v_live_quote_display with(security_invoker=true) as
 select jsonb_set(q.payload,'{quotes}',coalesce((
   select jsonb_object_agg(quote.key,quote.value)
   from jsonb_each(q.payload->'quotes') quote
   where quote.key='SPY' or quote.key in (
     select position->>'ticker' from public.live_strategy_anchor a
     cross join lateral jsonb_array_elements(a.payload->'equity'->'position') position
     where a.hold_year=substring(q.payload->>'session',1,4)::integer
   )), '{}'::jsonb)) || jsonb_build_object('display_only',true) as payload
 from public.live_quote_snapshot q where q.singleton;
grant select on public.v_live_quote_display to anon,authenticated;
create or replace function public.guard_live_identity_reviews() returns trigger language plpgsql as $$
begin
 if old.source in ('live_identity_review','live_security_directory','live_cash_date_review')
    or (tg_op='UPDATE' and new.source in ('live_identity_review','live_security_directory','live_cash_date_review')) then
   raise exception 'Runtime identity evidence is append-only';
 end if;
 return case when tg_op='DELETE' then old else new end;
end $$;
drop trigger if exists live_identity_immutable on public.sys_event;
create trigger live_identity_immutable before update or delete on public.sys_event
 for each row execute function public.guard_live_identity_reviews();
commit;
