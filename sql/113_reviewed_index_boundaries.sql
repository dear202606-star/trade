-- Reviewed reference events append; open ends can change only with a later
-- reviewed exit. Check at commit so an interval and its proof are atomic.
create or replace function public.guard_index_review_evidence() returns trigger
language plpgsql set search_path=public,pg_temp as $$
begin
 if tg_op='TRUNCATE' then
   if exists(select 1 from sys_event where source in ('reviewed_index_change','verified_corporate_terms') and kind='validation' and status='pass') then
     raise exception 'Reviewed index evidence is append-only';
   end if;
   return null;
 end if;
 if old.source in ('reviewed_index_change','verified_corporate_terms') and old.kind='validation' and old.status='pass' then
   raise exception 'Reviewed index evidence is append-only';
 end if;
 if tg_op='UPDATE' then
   if new.source in ('reviewed_index_change','verified_corporate_terms') and new.kind='validation' and new.status='pass' then
     raise exception 'Insert a new reviewed index evidence version';
   end if;
   return new;
 end if;
 return old;
end $$;
drop trigger if exists index_review_immutable on public.sys_event;
create trigger index_review_immutable before update or delete on public.sys_event
 for each row execute function public.guard_index_review_evidence();
drop trigger if exists index_review_no_truncate on public.sys_event;
create trigger index_review_no_truncate before truncate on public.sys_event
 for each statement execute function public.guard_index_review_evidence();

create or replace function public.check_reviewed_index_boundaries() returns trigger
language plpgsql set search_path=public,pg_temp as $$
declare affected text[]; symbol_ text; proof_ jsonb; start_ date; end_ date; day_ date;
begin
 if tg_table_name='sys_event' then
   if new.kind is distinct from 'validation' or new.source is distinct from 'reviewed_index_change' or new.status is distinct from 'pass' then return null; end if;
   affected:=array[new.metrics->'proof'->'change'->>'symbol'];
 elsif tg_op='DELETE' then affected:=array[old.symbol];
 elsif tg_op='UPDATE' then affected:=array[old.symbol,new.symbol];
 else affected:=array[new.symbol]; end if;
 foreach symbol_ in array affected loop
   for proof_ in select metrics->'proof' from sys_event
     where kind='validation' and source='reviewed_index_change' and status='pass'
       and metrics->'proof'->'change'->>'symbol'=symbol_ loop
     start_:=(proof_->>'interval_start')::date;
     day_:=(proof_->'change'->>'effective_on')::date;
     if proof_->'change'->>'action'='add' then
       select min((metrics->'proof'->'change'->>'effective_on')::date)-1 into end_
         from sys_event where kind='validation' and source='reviewed_index_change' and status='pass'
          and metrics->'proof'->'change'->>'symbol'=symbol_
          and metrics->'proof'->'change'->>'action'='remove'
          and (metrics->'proof'->'change'->>'effective_on')::date>start_;
       end_:=coalesce(end_,date '2099-12-31');
       if exists(select 1 from members where symbol=symbol_ and start_date<>start_
          and start_date<=end_ and end_date>=start_) then
         raise exception 'Overlapping reviewed index admission: %',symbol_;
       end if;
     else
       end_:=day_-1;
       if exists(select 1 from members where symbol=symbol_ and start_date<=day_ and end_date>=day_) then
         raise exception 'Reviewed index exit cannot be extended: %',symbol_;
       end if;
     end if;
     if not exists(select 1 from members where symbol=symbol_ and start_date=start_ and end_date=end_) then
       raise exception 'Reviewed index boundary mismatch: %',symbol_;
     end if;
   end loop;
 end loop;
 return null;
end $$;
drop trigger if exists reviewed_member_boundary on public.members;
create constraint trigger reviewed_member_boundary after insert or update or delete on public.members
 deferrable initially deferred for each row execute function public.check_reviewed_index_boundaries();
drop trigger if exists reviewed_member_proof on public.sys_event;
create constraint trigger reviewed_member_proof after insert on public.sys_event
 deferrable initially deferred for each row execute function public.check_reviewed_index_boundaries();

create or replace function public.guard_reviewed_members_truncate() returns trigger
language plpgsql set search_path=public,pg_temp as $$
begin
 if exists(select 1 from sys_event where kind='validation' and source='reviewed_index_change' and status='pass') then
   raise exception 'Reviewed membership cannot be truncated';
 end if;
 return null;
end $$;
drop trigger if exists reviewed_members_no_truncate on public.members;
create trigger reviewed_members_no_truncate before truncate on public.members
 for each statement execute function public.guard_reviewed_members_truncate();
