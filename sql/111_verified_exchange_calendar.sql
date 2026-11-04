create index if not exists sys_event_exchange_calendar
 on public.sys_event(created_at desc,id desc)
 where kind='validation' and source='verified_exchange_calendar' and status='pass';

create or replace view public.v_market_calendar as
 select metrics->'calendar' as calendar, metrics->>'evidence_sha256' as evidence_sha256
 from public.sys_event
 where kind='validation' and source='verified_exchange_calendar' and status='pass'
 order by created_at desc,id desc limit 1;
grant select on public.v_market_calendar to anon,authenticated;

create or replace function public.guard_exchange_calendar_evidence() returns trigger
language plpgsql set search_path=public,pg_temp as $$
begin
 if tg_op='TRUNCATE' then
   if exists(select 1 from sys_event where kind='validation'
     and source='verified_exchange_calendar' and status='pass') then
     raise exception 'Reviewed exchange calendar evidence is append-only';
   end if;
   return null;
 end if;
 if old.kind='validation' and old.source='verified_exchange_calendar' and old.status='pass' then
   raise exception 'Reviewed exchange calendar evidence is append-only';
 end if;
 if tg_op='DELETE' then return old; end if;
 return new;
end $$;
drop trigger if exists exchange_calendar_immutable on public.sys_event;
create trigger exchange_calendar_immutable before update or delete on public.sys_event
 for each row execute function public.guard_exchange_calendar_evidence();
drop trigger if exists exchange_calendar_no_truncate on public.sys_event;
create trigger exchange_calendar_no_truncate before truncate on public.sys_event
 for each statement execute function public.guard_exchange_calendar_evidence();
