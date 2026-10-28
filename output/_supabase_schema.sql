-- 持仓云同步：一人一行，id = 同步码，payload = 客户端加密密文
create table if not exists public.holdings (
  id          text primary key,
  payload     text not null,
  updated_at  timestamptz not null default now()
);

comment on table  public.holdings is '个人持仓云同步（客户端 AES-GCM 加密，服务端只见密文）';
comment on column public.holdings.id is '同步码（客户端生成的随机串，等同密钥的一半）';

alter table public.holdings enable row level security;

-- 允许匿名按 id 读写：id 是随机同步码，payload 已加密，泄露的是密文
drop policy if exists holdings_select on public.holdings;
drop policy if exists holdings_insert on public.holdings;
drop policy if exists holdings_update on public.holdings;

create policy holdings_select on public.holdings
  for select to anon, authenticated using (true);

create policy holdings_insert on public.holdings
  for insert to anon, authenticated with check (true);

create policy holdings_update on public.holdings
  for update to anon, authenticated using (true) with check (true);

-- 自动维护 updated_at
create or replace function public.touch_holdings() returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists trg_touch_holdings on public.holdings;
create trigger trg_touch_holdings before update on public.holdings
  for each row execute function public.touch_holdings();
