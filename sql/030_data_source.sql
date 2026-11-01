-- M1：源注册表合并（source_registry + data_sources → data_source + data_source_secret）
--
-- 权威来源：docs/DB_SCHEMA_REDESIGN_2026-11.md §6.1
-- 原则：元数据与凭据分离；旧表改名保留（可回滚），同名视图保证读代码零改动。

-- 6.1.1 元数据表（能力矩阵 + 调度序）
create table if not exists public.data_source (
  source_id        text primary key,
  display          text,
  kind             text,
  priority         int  check (priority is null or priority > 0),
  basis            text,
  delisted         text,
  depth            text,
  frequency        text,
  quota_per_day    int  check (quota_per_day is null or quota_per_day >= 0),
  free             boolean not null default true,
  status           text not null default 'untested',
  role             text,
  access           text,
  tested           text,
  last_ok          timestamptz,
  note             text,
  credential_state text default 'none',
  credential_fingerprint text,
  vault_secret_name text,
  updated_at       timestamptz not null default now()
);

comment on table public.data_source is
  '数据源能力矩阵 + 调度序（唯一真相）。凭据不在此表：见 data_source_secret。';

-- 6.1.2 凭据表（RLS 开、不建策略 → 只有 owner 能碰）
create table if not exists public.data_source_secret (
  source_id   text primary key references public.data_source(source_id) on delete cascade,
  credential  jsonb not null,
  fingerprint text not null,
  added_at    timestamptz not null default now(),
  rotated_at  timestamptz,
  note        text
);
alter table public.data_source_secret enable row level security;
revoke all on public.data_source_secret from anon, authenticated;

-- 6.1.3 搬迁
insert into public.data_source
  (source_id, display, kind, priority, basis, delisted, depth, frequency,
   quota_per_day, free, status, role, access, tested, last_ok, note)
select source_id, display, kind, priority, basis, delisted, depth, frequency,
       quota_per_day, free, status, role, access, tested, last_ok, note
from public.source_registry
on conflict (source_id) do update set
  display=excluded.display, kind=excluded.kind, priority=excluded.priority,
  basis=excluded.basis, delisted=excluded.delisted, depth=excluded.depth,
  frequency=excluded.frequency, quota_per_day=excluded.quota_per_day,
  free=excluded.free, status=excluded.status, role=excluded.role,
  access=excluded.access, tested=excluded.tested, last_ok=excluded.last_ok,
  note=excluded.note, updated_at=now();

-- 价格源必须齐（含历史复合源值，供外键校验）
insert into public.data_source(source_id, display, kind, basis, status, note, free)
values ('sina_repaired','新浪（本地修复）','price','price_only','ok','历史复合源前缀',true),
       ('sina_repaired+eodhd','新浪+EODHD 拼接（历史遗留）','price','price_only','dead',
        '逐月换源化石：禁止新增，仅保留历史行',true),
       ('yahoo_relay','Yahoo（中转）','price','total_return','ok',null,true),
       ('hf_amirtrader','HF AmirTrader','price','total_return','ok',null,true)
on conflict (source_id) do nothing;

insert into public.data_source_secret(source_id, credential, fingerprint, added_at, note)
select distinct on (source_id) source_id, credential, coalesce(fingerprint,''), added_at, note
from public.data_sources
on conflict (source_id) do nothing;

-- 凭据状态回写
update public.data_source d set credential_state='present',
       credential_fingerprint=s.fingerprint
  from public.data_source_secret s
 where s.source_id=d.source_id;

-- 6.1.4 旧表改名 → 兼容视图（读代码零改动）
do $$ begin
  if exists (select 1 from pg_class where relname='source_registry' and relkind='r')
     and not exists (select 1 from pg_class where relname='source_registry_legacy') then
    alter table public.source_registry rename to source_registry_legacy;
  end if;
  if exists (select 1 from pg_class where relname='data_sources' and relkind='r')
     and not exists (select 1 from pg_class where relname='data_sources_legacy') then
    alter table public.data_sources rename to data_sources_legacy;
  end if;
end $$;

create or replace view public.source_registry with (security_invoker = true) as
  select source_id, display, kind, priority, basis, delisted, depth, frequency,
         quota_per_day, free, status, role, access, tested, last_ok, note, updated_at,
         row_number() over (order by source_id) as id
  from public.data_source;

create or replace view public.data_sources with (security_invoker = true) as
  select d.source_id, d.display, d.kind as capability,
         d.credential_fingerprint as fingerprint,
         d.status, d.updated_at as added_at, d.note,
         row_number() over (order by d.source_id) as id
  from public.data_source d
  where d.credential_state <> 'none';

grant select on public.source_registry to anon, authenticated;
grant select on public.data_sources to anon, authenticated;
grant select on public.data_source to anon, authenticated;
