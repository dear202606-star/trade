begin;
-- Preserve the migration anchor while allowing an explicitly reviewed replacement.
alter table public.live_strategy_anchor add column if not exists revision integer not null default 1 check(revision>0);
alter table public.live_strategy_anchor drop constraint if exists live_strategy_anchor_pkey;
alter table public.live_strategy_anchor add primary key(variant,hold_year,revision);
commit;
