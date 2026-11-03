-- M6：事件层合并（fetch_runs + validations + work_plans → sys_event）
--
-- 权威来源：docs/DB_SCHEMA_REDESIGN_2026-11.md §6.6
-- 三类记录本质相同：有时间、有状态、有明细。用 kind 区分，各自特有字段进 metrics jsonb。

create table if not exists public.sys_event (
  id          bigint generated always as identity primary key,
  kind        text not null check (kind in ('fetch','validation','plan')),
  source      text,                 -- 采集源 / 作业名
  subject     text,                 -- 检查项名 / 作业主题
  status      text,                 -- ok/pass/fail/warn/...
  detail      text,
  metrics     jsonb,                -- requested/ok/failed/duration_ms/plan 等
  dataset_id  int references public.datasets(id) on delete set null,
  run_id      text,
  started_at  timestamptz,
  finished_at timestamptz,
  created_at  timestamptz not null default now()
);

create index if not exists sys_event_kind_time on public.sys_event(kind, created_at desc);
create index if not exists sys_event_source on public.sys_event(source);
create index if not exists sys_event_status on public.sys_event(status) where status in ('fail','warn');

-- 搬数据（三张旧表 → 一张）
do $$ begin
  if exists (select 1 from pg_class where relname='fetch_runs' and relkind='r') then
    insert into public.sys_event(kind, source, subject, status, detail, metrics,
                                 started_at, finished_at, created_at)
    select 'fetch', source, 'run',
           case when coalesce(failed,0) > 0 then 'warn' else 'ok' end,
           note,
           jsonb_build_object('requested', requested, 'ok', ok, 'failed', failed,
                              'duration_ms',
                              round(extract(epoch from coalesce(finished_at, started_at) - started_at)*1000)),
           started_at, finished_at, coalesce(finished_at, started_at, now())
    from public.fetch_runs;
  end if;
end $$;

do $$ begin
  if exists (select 1 from pg_class where relname='validations' and relkind='r') then
    insert into public.sys_event(kind, source, subject, status, detail, dataset_id,
                                 finished_at, created_at)
    select 'validation', 'db_validate', check_name, status, detail, dataset_id,
           ran_at, coalesce(ran_at, now())
    from public.validations;
  end if;
end $$;

do $$ begin
  if exists (select 1 from pg_class where relname='work_plans' and relkind='r') then
    insert into public.sys_event(kind, source, subject, status, metrics, created_at)
    select 'plan', 'allocate_work', 'daily', 'ok', plan, generated
    from public.work_plans;
  end if;
end $$;

-- 旧表改名，兼容视图保留（读代码零改动）
do $$ begin
  if exists (select 1 from pg_class where relname='fetch_runs' and relkind='r')
     and not exists (select 1 from pg_class where relname='fetch_runs_legacy') then
    alter table public.fetch_runs rename to fetch_runs_legacy;
  end if;
  if exists (select 1 from pg_class where relname='validations' and relkind='r')
     and not exists (select 1 from pg_class where relname='validations_legacy') then
    alter table public.validations rename to validations_legacy;
  end if;
  if exists (select 1 from pg_class where relname='work_plans' and relkind='r')
     and not exists (select 1 from pg_class where relname='work_plans_legacy') then
    alter table public.work_plans rename to work_plans_legacy;
  end if;
end $$;

create or replace view public.fetch_runs with (security_invoker = true) as
  select id, source, started_at, finished_at,
         (metrics->>'requested')::int as requested,
         (metrics->>'ok')::int        as ok,
         (metrics->>'failed')::int    as failed,
         detail as note
  from public.sys_event where kind = 'fetch';

create or replace view public.validations with (security_invoker = true) as
  select id, dataset_id, subject as check_name, status, detail,
         coalesce(finished_at, created_at) as ran_at
  from public.sys_event where kind = 'validation';

create or replace view public.work_plans with (security_invoker = true) as
  select id, created_at as generated, metrics as plan
  from public.sys_event where kind = 'plan';

alter table public.sys_event enable row level security;
grant select on public.sys_event to anon, authenticated;
grant select on public.fetch_runs to anon, authenticated;
grant select on public.validations to anon, authenticated;
grant select on public.work_plans to anon, authenticated;
