-- 站点展示数据统一入库：Pages 只保留界面与引导配置。
-- 2026-11-06：此前页面同时读「在线视图」与「data/*.json 静态快照」，
-- 双源不一致时会出现移动端「数据未就绪」而桌面正常。此后展示数据
-- 一律从 v_site_dataset 读取，cloud.json 作为唯一的引导配置保留。
begin;
create table if not exists public.site_dataset (
 key text primary key,
 payload jsonb not null,
 source_sha256 text not null check(source_sha256 ~ '^[0-9a-f]{64}$'),
 updated_at timestamptz not null default now()
);
alter table public.site_dataset enable row level security;
revoke all on public.site_dataset from anon,authenticated;
grant select on public.site_dataset to anon,authenticated;
drop policy if exists site_dataset_read on public.site_dataset;
create policy site_dataset_read on public.site_dataset for select to anon,authenticated using(true);
create or replace view public.v_site_dataset with(security_invoker=true) as
 select key,payload,source_sha256,updated_at from public.site_dataset;
grant select on public.v_site_dataset to anon,authenticated;
commit;
