# 数据库重构设计（v2 草案，待专家委员会合入）

> 出发点：现有 17 张表里，**8 张是同一件事的不同阶段**（raw → selected → snapshot）、**4 组是同一概念被拆开**（锁 2 张、源元数据 2 张、事件 2 张、标的属性 3 张）。
> 目标：**按「变化频率 + 变更方式」分表，而不是按「流水线阶段」分表**；逐级计算的结果用字段表达，不复制成表。

## 一、现状问题（逐条）

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| 1 | **选源结果被物化成表** | `series_selected` 117,113 行全量拷贝 `prices_monthly` 的子集 | 同一事实两份存储，必有一致性风险（已发生过索引/策略丢失） |
| 2 | **锁被拆成两张** | `locks`（区间，0 行）+ `locked_symbols`（标的，430 行） | 两套触发器逻辑、两处门禁，DELETE 漏洞就是这么漏的 |
| 3 | **源元数据拆两张** | `source_registry`（能力，44 行）+ `data_sources`（凭据，3 行） | 同一 `source_id` 要在两处维护，优先级曾出现「两套真相」 |
| 4 | **事件拆两张** | `fetch_runs`（10）+ `validations`（279）+ `work_plans`（2） | 三类都是「有时间、有状态、有明细」，查询要 union |
| 5 | **标的属性散三处** | `symbols`（0 行）、`sectors`（513）、`members`（1262） | 查一只票要 join 三张表 |
| 6 | **快照行单独存 34.7 万行** | `dataset_rows` | 与 `prices_monthly` 重复，且无外键指向价格行 |
| 7 | **全库只有 2 个外键** | 其余靠约定 | 脏数据无法被数据库拦截 |

## 二、判据：什么时候合并、什么时候必须拆

**合并**（宁可一张表多几个字段）：
- 生命周期相同（一起产生、一起失效）
- 写入方相同（同一段代码写）
- 查询几乎总是一起取
- 1:1 或 1:N 且 N 很小（< 万级）

**必须拆**：
- 基数差异极大（`prices` 35 万 vs `symbols` 847）
- 更新频率差异极大（实时行情 5 分钟 vs 历史锁死）
- 锁粒度不同（锁「某年某月」与锁「整只票」）
- 审计需要保留历史版本（`datasets`）

## 三、目标 schema（10 张表 + 1 视图，从 17 降下来）

命名按经典分层：`dim_` 维表 / `fact_` 事实表 / `sys_` 系统表 / `app_` 应用表。

```
dim_symbol        847   标的属性 + 行业 + 选源结果（合并 symbols/sectors/series_selected 的“谁被选中”）
fact_membership  1262   成分区间（一只票多段，必须独立表）
fact_price       35万   月频价格（多源并存，事实表）
dim_source        44   数据源（能力 + 凭据，合并 registry + credentials）
fact_dataset        2   数据集版本
fact_dataset_row 34万   数据集快照（发布凭证，保留）
sys_lock         430+  锁（统一区间锁与标的锁，靠 scope_type 区分）
sys_event        291+  事件（采集台账 + 校验结论 + 作业计划，靠 kind 区分）
sys_staging        0    CI 写库缓冲区
app_holdings       0    站点云同步
v_series         view  每票单源序列（替代 series_selected 表）
```

### 关键设计决策

**1. 选源结果 → 字段，不是表**
`dim_symbol.primary_source` 记录「这只票用哪个源」，`v_series` 用 join 实时算出来：

```sql
create view v_series as
select p.symbol, p.month, p.adj_close, p.raw_close, p.source, p.basis
from fact_price p
join dim_symbol s on s.symbol = p.symbol
where p.source = s.primary_source;
```
好处：选源一变，视图立刻跟着变，**不可能出现「表里的选择」和「字段里的选择」不一致**。

**2. 锁 → 一张表，两个维度**
```sql
sys_lock(scope_type 'range'|'symbol', scope_key, status, rows, hash, locked_at, note)
```
`scope_key` 对 range 是 `'2012-01..2025-12'`，对 symbol 是 `'AAPL'`。
触发器就一个函数、一个分支判断，避免「区间锁有 DELETE 分支、标的锁没有」这类漏洞。

**3. 事件 → 一张表，kind 区分**
```sql
sys_event(kind 'fetch'|'validation'|'plan', source, subject, status, detail, metrics jsonb, started_at, finished_at)
```
采集（requested/ok/failed）、校验（check_name/pass/fail）、计划（jsonb）都能放；`metrics jsonb` 吸收各自特有字段。

**4. 快照行保留，但加外键**
`fact_dataset_row.dataset_id → fact_dataset.id`，并加 `(symbol, month)` 的 check 约束。

**5. 补外键（现在只有 2 个）**
- `fact_membership.symbol → dim_symbol.symbol`
- `fact_price.symbol → dim_symbol.symbol`
- `fact_dataset_row.symbol → dim_symbol.symbol`
- `fact_dataset_row.dataset_id → fact_dataset.id`
- `dim_symbol.primary_source → dim_source.source_id`

> 注意：价格可能先于 `dim_symbol` 到达（采集顺序），所以 `fact_price.symbol` 用 **不强制**的外键（`not valid`）或由采集端先 upsert `dim_symbol`。迁移与写入都按「先 dim 后 fact」。

## 四、逐级计算结果放哪（回答「单独表 / 单独字段 / 怎么滴」）

| 计算结果 | 放法 | 理由 |
|---|---|---|
| 每票选哪个源 | `dim_symbol.primary_source` 字段 | 1:1、随票变化、查询总要用 |
| 每票序列 | `v_series` 视图 | 完全派生，不存 |
| 逐年收益/回撤/持仓 | 不进库，算完导出 JSON | 发布产物，属于展示层 |
| 数据集内容 | `fact_dataset_row` 表 | 需要冻结与审计，不能派生 |
| 覆盖率/校验结论 | `sys_event` 行 | 有时间序列属性，需要留痕 |
| 锁 | `sys_lock` 表 | 需要不可变与审计 |
| 当前持仓 | `app_holdings` 表 | 站点实时读写 |

原则：**能派生的不存；要冻结的必须存；要审计的必须留痕。**

## 五、迁移步骤（零丢失，可回滚）

1. **建新表**（`dim_*`/`fact_*`/`sys_*`），不动旧表。
2. **灌维表**：`dim_symbol` ← `members`+`sectors`+`series_selected` 的选源结果；`dim_source` ← `source_registry`+`data_sources`。
3. **灌事实表**：`fact_price` ← `prices_monthly`（分批，按 symbol）；
   `fact_membership` ← `members`；`fact_dataset*` ← 原表；`sys_lock` ← `locks`+`locked_symbols`；`sys_event` ← 三张事件表。
4. **校验**：行数、md5、逐行差异；`v_series` 与原 `series_selected` 逐行比对必须 0 差异。
5. **切代码**：把读取改成新表/视图（`db_prices`/`db_select`/`db_ref` 各改一处入口）。
6. **观察 1 天**：CI 构建、页面数字不变。
7. **下线旧表**（重命名 `zz_old_*`，不立刻 drop，保留 7 天）。

回滚：任一步失败 → 代码切回旧表名即可；旧表在切代码前完全不动。
