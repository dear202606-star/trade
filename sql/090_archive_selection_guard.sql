-- CC0 archive is retained for research, but ticker/issuer identity is not yet
-- verified. Enforce this in the database as older clients also refresh picks.
-- Run after restoring any existing archive picks; never silently replace them.
begin;
do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'series_pick'::regclass
      and conname = 'series_pick_archive_review_ck'
  ) then
    alter table series_pick add constraint series_pick_archive_review_ck
      check (source <> 'kaggle_cc0');
  end if;
end $$;
commit;
