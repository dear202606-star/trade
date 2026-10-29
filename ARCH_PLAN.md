# 迁移方案：数据库作为唯一事实来源（DB as System of Record）

> 三份专家报告（金融 / 技术 / 数据治理）的共同结论：**当前"JSON 文件 + git 提交"作为状态通道，
> 无法保证端到端一致**。本方案把「历史静态数据一次锁定、变化数据自动入库」落到 Supabase Postgres，
> 静态站点仍从 DB 导出 JSON 构建（保持零后端）。

## 一、为什么必须迁移（问题清单汇总）

| 编号 | 问题 | 归属 | 后果 |
|---|---|---|---|
| B1 | 净值末点掺入未完成当月的盘中价，且敞口只作用于最后一点 | 技术 | 头条收益 5 分钟一跳、可单日 −22.8% 假跌 |
| B2 | 两个 workflow 无互斥并发、用 git 当状态通道 | 技术 | 22:00 UTC 必然双跑，push 冲突静默丢弃，站点回退旧构建 |
| B3 | 4/6 步 `continue-on-error`，无失败通知，`pipeline_status` 不上线 | 技术 | 管线挂了 3 天，用户看到的是"数据没变化" |
| I1 | 增量键用北京月、基准用数据月 | 技术 | 每月 1 号前后每次 dispatch 都走 15 分钟全量 |
| I2 | Yahoo 不可达时双源核对静默退化为单源 | 技术 | 拆股漏检 → 动量与净值静默错 |
| I3 | `equity.py` 不做复权，自检无单日跳变闸 | 技术 | 持仓拆股当天画出虚假断崖 |
| I4 | `session` 靠时钟硬推（固定 UTC−4） | 技术 | 冬令时/节假日/半日市把盘中价标成收盘价 |
| S3 | 已发布数据无版本/变更留痕 | 金融 | 同一 URL 数字 6 分钟内变化，无法审计 |
| S4 | 行业约束因 Unknown 失效（已修，需固化到 DB） | 金融 | 2013/2023 曾 100% 单一行业 |
| G* | 信号链路缺运行级一致性校验 | 数据治理 | 产物间无法证明同源 |

**共因**：状态分散在 git、`output/*.json`、`docs/data/*.json`、工作流缓存四处，没有单一写入者与运行标识。

## 一之二、数据治理专家的 5 条阻断缺口（已并入设计）

| 编号 | 缺口 | 设计对策（已落到 schema） |
|---|---|---|
| G1 | 复权表为空或永久冻结：Yahoo 不可达时全市场不复权；一旦写入又永不刷新 | corporate_actions(as_of, source) + 与第三方事件核对冲突即 fail；价格行打 source，禁止二次复权 |
| G2 | 双源不一致时静默删票，门禁不在信号链路上 | signal_current.data_health{pool,fetched,failed,quarantined[],agree_rate,coverage}；quarantine_ledger 留痕 |
| G3 | 锁没有时点证明（locked_at 比排名日迟 10 个月） | picks_yearly.lock_run + locked_at + input_hashes；缺锁或时点不合规直接 fail |
| G4 | 行业约束不校验当前 official，Unknown 免约束，NaN 可写出非法 JSON | 行业缺失即剔除；picks_yearly.sector_max 入库；严格 JSON（拒 NaN） |
| G5 | 成分表用文件 mtime 判新鲜度（CI 里永远通过） | memberships.as_of 由内容决定；input_hashes.members_asof 参与校验 |

**必须落地的 6 条硬校验**（写入 selftest 与 run_log.checks）：
1. 发布收益可用入库拆股表独立复现（误差 <1e-3），且拆股条目数 ≥400。
2. 排名池覆盖 ≥98%，隔离票不得进入前 15 或正式持仓。
3. official 恰 3 只、权重和 =1、行业 ≤60%、行业不得为空/Unknown/NaN。
4. month 必须是最近完整自然月，趋势只用完整月计算。
5. 锁定记录存在、hash 可重算、locked_at ≤ 排名月末+3天、alid_from ≤ today ≤ valid_to。
6. 成分表 s_of ≥ today-35天，且 splits/members 双指纹一致。

## 二、目标架构

```
        ┌──────────── Supabase Postgres（唯一事实来源）────────────┐
        │  锁定层（一次写入，禁止改写，带校验哈希）                │
        │    instruments / memberships / corporate_actions        │
        │    prices_monthly / picks_yearly / equity_history        │
        │  变动层（每次运行幂等 upsert）                          │
        │    quote_snapshot / signal_current / equity_current      │
        │  审计层（每次运行一行）                                  │
        │    run_log(run_id, started_at, steps, status, checks)    │
        └───────────────┬─────────────────────────────────────────┘
                        │ export（只读导出，幂等）
                        ▼
        docs/data/*.json  →  GitHub Pages（静态站点，浏览器零后端）
```

- **锁定层**：一次性导入 + `CHECK`/触发器禁止 UPDATE；每行存 `sha256`，导出时校验；如需修改必须走
  `schema_migrations` 记录的新版本（v2、v3…），旧版本永久可查。
- **变动层**：只存"当天"与"当期"，按 `(trade_date)` / `(official_year)` 幂等 upsert。
- **审计层**：`run_log` 记录每次运行的步骤与校验结果；导出 `health.json` 供页面渲染状态条。

## 三、表结构（DDL 摘要）

```sql
-- 锁定层
create table instruments (ticker text primary key, name text, sector text,
  is_active boolean, updated_at timestamptz default now());

create table memberships (ticker text, start_date date, end_date date,
  primary key (ticker, start_date));            -- 时点成分表，一次导入

create table corporate_actions (ticker text, ex_date date, ratio numeric,
  kind text default 'split', primary key (ticker, ex_date));

create table prices_monthly (ticker text, ym text, adj numeric, raw numeric,
  vol numeric, primary key (ticker, ym));        -- 历史月线，锁定

create table picks_yearly (year int primary key, official_month text,
  holdings jsonb, weights jsonb, checksum text, locked_at timestamptz,
  lock_version int default 1);                   -- 年度选股，锁定

create table equity_history (trade_date date primary key, s numeric, b numeric,
  locked_at timestamptz);                        -- 已结算净值，锁定

-- 变动层
create table quote_snapshot (ticker text primary key, price numeric, prev_close numeric,
  chg numeric, session date, quote_mode text, asof_ts timestamptz, run_id uuid);

create table signal_current (month text primary key, official jsonb, picks jsonb,
  trend text, exposure numeric, spy numeric, ma10 numeric, run_id uuid, updated_at timestamptz);

create table equity_current (trade_date date primary key, s numeric, b numeric,
  run_id uuid, updated_at timestamptz);

-- 审计层
create table run_log (run_id uuid primary key, started_at timestamptz, finished_at timestamptz,
  steps jsonb, status text, checks jsonb);
```

RLS：锁定层仅 service_role 可写，匿名只读；变动层同样只读（写入全部由 Actions 用 service key）。

## 四、迁移分期（每期都有可回滚点）

| 期 | 内容 | 验收标准 |
|---|---|---|
| **P0**（1 天） | 建表 + 导入锁定层（memberships / corporate_actions / prices_monthly / picks_yearly / equity_history），并写入 `lock_version=1` | 导出 JSON 与现网逐字节一致（diff 为空） |
| **P1**（1 天） | 变动层接管 `quotes.py` / `equity.py` 的写入；产物加 `run_id`；`health.json` 上线 | 同一交易日重跑 3 次，`docs/data/*.json` 完全一致（幂等） |
| **P2**（1 天） | 收敛触发：`quotes.yml` 只写快照；`daily.yml` 单写者跑信号+净值+回测+部署；两 workflow 共用 `concurrency: pipeline` | 22:00 UTC 不再双跑；`git` 不再作为状态通道 |
| **P3**（半天） | 收盘确认闸：SPY 当日 bar 出现且与快照 `pc` 差 <0.1% 才发布；`session` 由数据反推 | 冬令时/感恩节当天 `session` 正确 |
| **P4**（半天） | 自检硬化：`run_id` 全等、单日 \|Δs\|>35% 硬闸、拆股复权、行业零违例、持仓锁一致 | 人为注入 4 类错误全部被拦 |

**分支策略**：新架构在 `db-arch` 分支开发；`main` 保持现状可回滚。P0–P4 全部通过后，
用一次 `gh-pages` 切换把线上指向 `db-arch` 的构建产物；保留 `main` 至少两个周期。

## 五、测试方案（委员会要求"绝对正确"）

1. **口径回归**：迁移前后导出的 `signal/equity/curve/strategy_history` 四份 JSON 必须**逐字段相等**
   （允许 1e-6 浮点差）；不等即阻断。
2. **幂等测试**：同一 `run_id` 重跑 3 次、不同 `run_id` 同数据重跑 1 次，产物哈希必须不变。
3. **并发测试**：同时触发 3 个 workflow，最终 `docs/data` 只能来自其中一个 `run_id`。
4. **故障注入**：Yahoo 不可达 / 新浪缺 20% 行情 / 拆股漏检 / 行业缺失，四种场景必须触发阻断且不发布。
5. **审计可回溯**：任取一天，能从 `run_log` + 锁定层复算出当日所有页面数字。

## 六、需要你确认的三件事

1. **允许线上数字再次变化**吗？（P0 会固化"行业修正后"的 5059%，此后再不变）
2. **Supabase 免费额度**够用：500MB 库、每月 5GB 流量；本方案预估 < 50MB、导出 JSON 走 GitHub 不消耗流量。
3. **回滚窗口**：`main` 保留多久？（建议 ≥ 2 个交易日、1 次月度换仓）
