-- Historical identity reviews are evidence, not replacement prices or locks.
create or replace function public.guard_live_identity_reviews() returns trigger language plpgsql as $$
begin
 if old.source in ('live_identity_review','live_security_directory','live_cash_date_review','historical_identity_review')
    or (tg_op='UPDATE' and new.source in ('live_identity_review','live_security_directory','live_cash_date_review','historical_identity_review')) then
   raise exception 'Reviewed identity evidence is append-only';
 end if;
 return case when tg_op='DELETE' then old else new end;
end $$;
