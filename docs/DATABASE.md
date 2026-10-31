# 数据库文档（`db-arch` 分支）

> 方案见 [ARCH_PLAN](ARCH_PLAN.md)。当前线上仍用 JSON + git；本文描述迁移目标。

## 1. 设计原则

| 原则 | 实现 |
|---|---|
| 单一写入者 | 每张表只有一个模块写；跨表无并发写 |
| 锁定不可改 | `picks_yearly` / `equity_history` 加触发器，UPDATE/DELETE 抛错 |
| 可回溯 | 每次运行一行 `run_log`，产物带 `run_id` |
| 幂等 | 变动层按主键 upsert，重复运行结果不变 |
| 只读导出 | 站点从 DB 导出 JSON，浏览器仍零后端 |

## 2. 表清单

### 锁定层（一次写入，禁改）

| 表 | 主键 | 说明 |
|---|---|---|
| `instruments` | `ticker` | 代码、名称、GICS 行业、行业来源 |
| `memberships` | `(ticker, start_date)` | 时点成分表；`as_of` 记录快照日期（禁 mtime） |
| `corporate_actions` | `(ticker, ex_date)` | 拆股/分拆/并股，`ratio` 为复权比例 |
| `prices_monthly` | `(ticker, ym)` | 月线复权价 + 原始价 + 成交量 |
| `picks_yearly` | `year` | 年度选股 + `checksum` + `lock_run` + `input_hashes` |
| `equity_history` | `trade_date` | 已结算日净值（满仓 gross） |

### 变动层（幂等 upsert）

| 表 | 主键 | 说明 |
|---|---|---|
| `quote_snapshot` | `ticker` | 当前快照：价、昨收、涨跌、交易日、盘中/收盘 |
| `signal_current` | `month` | 当月信号 + `data_health`（池大小/隔离名单） |
| `equity_current` | `trade_date` | 当期日净值 |

### 审计层

| 表 | 说明 |
|---|---|
| `run_log` | 每次运行：`run_id`、`commit_sha`、步骤耗时、校验结果、状态 |
| `signal_ledger` | append-only 信号账本，链式 `prev_hash`，区分 `computed_at` / `refreshed_at` |
| `quarantine_ledger` | 被隔离的股票台账（原因、是否复核） |

## 3. 关键字段与约束

```sql
-- 锁定保护
create trigger trg_lock_picks before update or delete on picks_yearly
  for each row execute function forbid_locked_change();

-- 行业约束可审计
picks_yearly.sector_max numeric          -- 最大行业权重，必须 ≤ 0.60

-- 复权可复现
picks_yearly.input_hashes jsonb          -- {splits_sha256, members_sha256, members_asof}
picks_yearly.ranking_snapshot jsonb      -- 每只的 rank/ret12/px/dv/sector/price_source
```

## 4. 权限（RLS）

- 所有表启用 RLS。
- 匿名角色：仅 `SELECT`（站点导出用）。
- `service_role`：写入；仅在 GitHub Actions 内使用，密钥存仓库 Secrets。

## 5. 迁移与回滚

| 阶段 | 动作 | 回滚 |
|---|---|---|
| P0 | 建表 + 导入锁定层 | 删表，线上仍读 JSON |
| P1 | 变动层接管写入，产物加 `run_id` | 关闭写入开关 |
| P2 | 收敛触发（单写者） | 恢复双 workflow |
| P3 | 收盘确认闸 | 放宽为按时间发布 |
| P4 | 自检硬化 | 恢复旧门禁 |

**切换点**：P0–P4 全绿后，Pages 指向 `db-arch` 构建产物；`main` 保留 ≥2 个交易日。

## 6. 验收查询

```sql
-- 1) 锁定不可改：应报错
update picks_yearly set holdings = '[]'::jsonb where year = 2025;

-- 2) 行业约束
select year, sector_max from picks_yearly where sector_max > 0.6001;  -- 应为空

-- 3) 最近一次运行的健康度
select run_id, status, checks from run_log order by started_at desc limit 1;

-- 4) 当期收益可复算（末值 − 1）
select (select s from equity_current order by trade_date desc limit 1) - 1 as ret;
```

## 7. 容量预估

| 表 | 行数 | 估算 |
|---|---|---|
| `prices_monthly` | 503 × 200 月 | ~100k 行，~15MB |
| `memberships` | ~1.2k | <1MB |
| `equity_history` | 16 年 × 250 | 4k 行 |
| 其余 | — | <5MB |

合计 < 50MB，远低于 Supabase 免费额度 500MB。
