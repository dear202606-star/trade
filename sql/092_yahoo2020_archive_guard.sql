-- The second CC0 archive is research input until issuer/coverage review.
begin;
do $$
begin
  if not exists(select 1 from pg_constraint where conrelid='series_pick'::regclass
                and conname='series_pick_yahoo2020_review_ck') then
    alter table series_pick add constraint series_pick_yahoo2020_review_ck
      check (source <> 'kaggle_yahoo2020');
  end if;
end $$;
commit;
