-- ============================================================
-- LEADER QUANT 数据层（Supabase Postgres）—— 唯一事实来源
-- 设计原则：
--   1. 锁定层一次写入、禁止改写、带校验哈希（历史静态数据）
--   2. 变动层按主键幂等 upsert（每日/每期变化的数）
--   3. 审计层记录 run_id 与校验结果，任何页面数字可回溯
-- ============================================================

-- ---------- 锁定层 ----------
create table if not exists instruments (
  ticker      text primary key,
  name        text,
  sector      text not null,
  sector_src  text default 'gics',           -- 行业来源（gics/override）
  is_active   boolean default true,
  updated_at  timestamptz default now()
);

create table if not exists memberships (
  ticker      text not null,
  start_date  date not null,
  end_date    date not null default '2099-12-31',
  as_of       date not null,                 -- 该表的快照日期（禁止用 mtime 判断新鲜度）
  primary key (ticker, start_date)
);
create index if not exists idx_memberships_window on memberships (start_date, end_date);

create table if not exists corporate_actions (
  ticker      text not null,
  ex_date     date not null,
  ratio       numeric not null,              -- 复权比例（1.3311 = 分拆后需乘 1.3311）
  kind        text not null default 'split', -- split / spinoff / reverse
  source      text not null default 'manual',-- manual / yahoo / derived
  as_of       date not null,
  primary key (ticker, ex_date)
);

create table if not exists prices_monthly (
  ticker      text not null,
  ym          text not null,                 -- YYYY-MM
  adj         numeric not null,              -- 复权价（按 corporate_actions 计算）
  raw         numeric not null,
  vol         numeric,
  as_of       date not null,
  primary key (ticker, ym)
);

create table if not exists picks_yearly (
  year          int primary key,
  official_month text not null,
  holdings      jsonb not null,              -- [{ticker,name,sector,rank,ret12,weight}]
  weights_sum   numeric not null,
  sector_max    numeric not null,            -- 最大行业权重（用于审计 ≤0.6）
  checksum      text not null,               -- sha256(canonical(holdings))
  lock_version  int not null default 1,
  lock_run      uuid,                        -- 产生这把锁的运行（run_id + commit_sha）
  input_hashes  jsonb,                       -- {splits_sha256, members_sha256, members_asof}
  locked_at     timestamptz not null,
  ranking_snapshot jsonb                     -- 每只的 rank/ret12/px/dv/sector/price_source
);

create table if not exists equity_history (
  trade_date  date primary key,
  s           numeric not null,              -- 满仓 gross 净值（敞口单列，不混入）
  b           numeric not null,
  lock_version int not null default 1,
  locked_at   timestamptz not null
);

-- ---------- 变动层 ----------
create table if not exists quote_snapshot (
  ticker      text primary key,
  price       numeric,
  prev_close  numeric,
  chg         numeric,
  session     date,                          -- 由 SPY 日线反推，不靠时钟
  quote_mode  text,                          -- intraday / close
  asof_ts     timestamptz,
  run_id      uuid
);

create table if not exists signal_current (
  month        text primary key,
  official_month text not null,
  official     jsonb not null,
  picks        jsonb,
  bench        jsonb,
  trend        text,
  exposure     numeric,
  spy          numeric,
  ma10         numeric,
  data_health  jsonb,                        -- pool/fetched/failed/quarantined/agree_rate/coverage
  run_id       uuid,
  updated_at   timestamptz default now()
);

create table if not exists equity_current (
  trade_date  date primary key,
  s           numeric not null,
  b           numeric not null,
  run_id      uuid,
  updated_at  timestamptz default now()
);

-- ---------- 审计层 ----------
create table if not exists run_log (
  run_id      uuid primary key,
  started_at  timestamptz not null,
  finished_at timestamptz,
  commit_sha  text,
  steps       jsonb,                         -- [{name, rc, dur}]
  checks      jsonb,                         -- [{name, ok, detail}]
  status      text                           -- ok / degraded / failed
);

-- ---------- 锁定保护：锁定层禁止 UPDATE/DELETE ----------
create or replace function forbid_locked_change() returns trigger
language plpgsql as $$
begin
  raise exception 'locked table % 禁止改写（请新建 lock_version）', TG_TABLE_NAME;
end $$;

drop trigger if exists trg_lock_picks on picks_yearly;
create trigger trg_lock_picks before update or delete on picks_yearly
  for each row execute function forbid_locked_change();

drop trigger if exists trg_lock_equity on equity_history;
create trigger trg_lock_equity before update or delete on equity_history
  for each row execute function forbid_locked_change();

-- ---------- 权限：匿名只读，写入仅 service_role ----------
alter table instruments      enable row level security;
alter table memberships      enable row level security;
alter table corporate_actions enable row level security;
alter table prices_monthly   enable row level security;
alter table picks_yearly     enable row level security;
alter table equity_history   enable row level security;
alter table quote_snapshot   enable row level security;
alter table signal_current   enable row level security;
alter table equity_current   enable row level security;
alter table run_log          enable row level security;


-- ---------- 信号账本（append-only，任何改写都可发现） ----------
create table if not exists signal_ledger (
  id            bigserial primary key,
  month         text not null,
  computed_at   timestamptz not null,
  refreshed_at  timestamptz not null,
  payload       jsonb not null,              -- 当次完整 signal 记录
  prev_hash     text,                        -- 上一条记录的 hash（链式）
  record_hash   text not null,               -- 本条 canonical hash
  run_id        uuid
);
create index if not exists idx_signal_ledger_month on signal_ledger (month, computed_at desc);

-- 隔离票台账（可追溯、到期复核）
create table if not exists quarantine_ledger (
  as_of      date not null,
  ticker     text not null,
  reason     text not null,
  reviewed   boolean default false,
  primary key (as_of, ticker)
);
