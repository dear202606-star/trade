begin;

-- Validate the published baseline while preventing concurrent writes.
lock table public.site_dataset in share row exclusive mode;
do $$
declare
  item record;
  fingerprints jsonb := '{}'::jsonb;
begin
  for item in
    select key, source_sha256,
           case when key in ('curve', 'b/curve') then payload->'points'
                else payload->'years' end as entries
      from public.site_dataset
     where key in ('curve', 'b/curve', 'strategy_history', 'b/strategy_history')
  loop
    if item.source_sha256 is null or item.source_sha256 !~ '^[a-f0-9]{64}$'
       or jsonb_typeof(item.entries) is distinct from 'array' then
      raise exception 'Invalid published history: %', item.key;
    end if;
    if jsonb_array_length(item.entries) < 2 then
      raise exception 'Insufficient published history: %', item.key;
    end if;
    fingerprints := fingerprints || jsonb_build_object(item.key, item.source_sha256);
  end loop;
  if not (fingerprints ?& array['curve','b/curve','strategy_history','b/strategy_history']) then
    raise exception 'Four published history datasets are required';
  end if;
  raise notice 'Published history fingerprints: %', fingerprints;
end $$;

create or replace function public.protect_site_history()
returns trigger language plpgsql set search_path = pg_catalog as $$
begin
  if tg_op = 'TRUNCATE' then
    raise check_violation using message = 'Published strategy history is frozen';
  end if;
  if old.key in ('curve', 'b/curve', 'strategy_history', 'b/strategy_history') then
    raise check_violation using message = 'Published strategy history is frozen';
  end if;
  if tg_op = 'UPDATE' then return new; end if;
  return old;
end;
$$;

drop trigger if exists freeze_site_history_rows on public.site_dataset;
create trigger freeze_site_history_rows before update or delete on public.site_dataset
for each row execute function public.protect_site_history();
drop trigger if exists freeze_site_history_truncate on public.site_dataset;
create trigger freeze_site_history_truncate before truncate on public.site_dataset
for each statement execute function public.protect_site_history();

do $$
begin
  begin
    update public.site_dataset set payload=payload || '{"_freeze_probe":true}'::jsonb where key='curve';
    raise exception 'Frozen history accepted an update';
  exception when check_violation then
    if sqlerrm <> 'Published strategy history is frozen' then raise; end if;
  end;
  begin
    delete from public.site_dataset where key='b/strategy_history';
    raise exception 'Frozen history accepted a deletion';
  exception when check_violation then
    if sqlerrm <> 'Published strategy history is frozen' then raise; end if;
  end;
end $$;

commit;
