# Supabase/Postgres 数据层迁移审计与零停机迁移方案

> 审计对象：`C:\Users\Administrator\Desktop\量化`（git `e1856ca`）
> 数据库：Postgres 17.11 / Supabase `fnvahsyrzmmxwovbsqpc` / 137 MB / 17 张表
> 审计方式：只读 catalog + `pg_stat_statements` + `pg_cron` + 全事务内 `ROLLBACK` 实验
> 基线证据：`output/migration_baseline.json`（指纹 + 页面数字 + 文件 sha）
> 探针脚本：`output/_mig_audit_probe.py`、`_mig_audit_probe2.py`、`_mig_probe3b.py`、`_mig_baseline.py`、`_mig_cleanup.py`

---

## 0. 六条推翻题设的实测结论（先读这段）

| # | 题设 | 实测 | 影响 |
|---|---|---|---|
| 1 | 「pooler 有语句超时，单条大语句会被 cancel」 | 默认 `statement_timeout = 2min`；但**全表 351,889 行 `INSERT…SELECT` 只要 757 ms**，整库 137 MB | 超时**不会**触发。分批的理由是缩短持锁时间，不是躲超时 |
| 2 | 「COPY 在事务池模式不可用」 | **成立的是另一件事**：6543 上 `autocommit=True` 直发 COPY 会**立即提交且无法回滚**（我实测时因此写入了一行 `__copytest__`，已删除并复核指纹回到基线）。`BEGIN; COPY…; COMMIT` 在 6543 与 5432 **都成功** | 用显式事务包住 COPY 即可；或用同主机的 **5432 会话模式**（实测 COPY/临时表/`SET` 粘住/pid 稳定全部可用） |
| 3 | 「长事务锁表」 | 真正的杀手不是长事务，而是**普通 SELECT（ACCESS SHARE）就足以卡住 `ALTER TABLE`**：实测在一个仅有 `select count(*)` 的未提交事务存在时，`ADD COLUMN` 被 `lock_timeout=2s` 打断（2069 ms）。而 DDL 在锁队列里等待时会**堵住其后所有新读** | 任何 DDL 必须带 `SET LOCAL lock_timeout` + 重试；绝不允许排队 |
| 4 | 「触发器在批量迁移时误触发」 | 更糟：**430 只已锁标的让"往旧表灌数"根本不可能成功**——实测 `update prices_monthly where symbol='A'` 直接 `raise: symbol A is locked`。且触发器开销实测 **7.4×**（200 只 / 83,802 行：开 1,230 ms vs 关 167 ms） | 正解是**灌进无触发器的新表**，不是 disable 旧表触发器 |
| 5 | 「RLS 丢失」 | 不是"会丢"，是**已经丢了**：`prices_monthly` 的 `relrowsecurity = false`、0 条策略、而 `anon` 仍有 `INSERT/UPDATE/DELETE` 权限。`db_hardening.py` 的目标状态**一条都没落地** | 迁移必须顺手把权限面收掉，否则新表也默认全开（见下） |
| 6 | 「CI 在迁移中途读到半成品」 | 已经在发生：`db_select.py` 的 drop→rename 是**6 条独立 autocommit 语句**，中间存在真实窗口 `series_selected` 不存在。实测该表在 **01:08:47 UTC 被重建**（`last_autoanalyze` 时间戳），我在 01:00 观测到一张 `series_selected_new`（带列/主键/索引/`public_read` 策略、anon 可读）在 01:12 已消失 | 迁移方案必须消灭这个窗口，否则"迁移"只是重复现有事故 |

**另一条隐藏事实**：库里有 **3 个 `pg_cron` 任务**在 GitHub Actions 之外驱动写入（见 1.4）。`quotes.yml` 不是「每 2 小时」，而是**美股盘中每 5 分钟**被库内 `pg_net` 派发一次。近 7 天 job 5 跑了 **196 次**。

---

## ① 依赖清单

### 1.1 表的实测形态（catalog 实测，非文档推断）

| 表 | 行数 | 堆/总大小 | PK | 触发器 | RLS | 策略数 | anon 写 |
|---|---:|---:|---|---|---|---:|---|
| `prices_monthly` | 351,889 | 40 MB / 76 MB | `(symbol,month,source)` | `trg_guard_months` BEFORE I/U/D | **关** | **0** | **全开** |
| `dataset_rows` | 347,833 | 27 MB / 39 MB | `(dataset_id,symbol,month)` | `trg_guard_rows` BEFORE I/U/D | 开 | **0** | 全开 |
| `series_selected` | 117,113 | 10 MB / 15 MB | `(symbol,month)` | — | 开 | 1 | 全开 |
| `members` | 1,262 | 72 KB | `(symbol,start_date)` | — | 开 | 1 | 全开 |
| `sectors` | 513 | 40 KB | `(symbol)` | — | 开 | 1 | 全开 |
| `locked_symbols` | 430 | 48 KB | `(symbol)` | — | 开 | 1 | 全开 |
| `source_registry` | 44 | 16 KB | `(id)` + UQ `source_id` | — | 开 | 1 | 全开 |
| `validations` | 279 | 32 KB | `(id)` | — | 开 | 1 | 全开 |
| `datasets` | 2 | 8 KB | `(id)` + UQ `name` | — | 开 | 1 | 全开 |
| `locks` | **0** | 8 KB | `(id)` + UQ `(kind,scope_from,scope_to)` | —（append-only 未落地） | 开 | **0** | 全开 |
| `fetch_runs` | 11 | 8 KB | `(id)` | — | 开 | 1 | 全开 |
| `work_plans` | 2 | 8 KB | `(id)` | — | 开 | **0** | 全开 |
| `data_sources` | 3 | 8 KB | `(id)` + UQ `source_id` | — | 开 | **0** | 全开 |
| `ingest_staging` | 0 | 8 KB | `(id)` | — | 开 | 2（ins+sel） | ins/sel |
| `symbols` | 0 | 0 B | `(symbol)` | — | 开 | **0** | 全开 |
| `corporate_actions` | 0 | 0 B | `(symbol,ex_date,kind)` | — | 开 | **0** | 全开 |
| `holdings` | 0 | 8 KB | `(id)` | `trg_touch_holdings` BEFORE U | 开 | 3 | 全开 |

**关键列（迁移必须逐列对齐）**

```
prices_monthly   (symbol, month, adj_close, raw_close, source, quality, fetched_at, basis)
series_selected  (symbol, month, adj_close, raw_close, source, basis)
dataset_rows     (dataset_id, symbol, month, adj_close, source, basis, quality, raw_close)
members          (symbol, start_date, end_date, source)
sectors          (symbol, sector, source, updated_at)
locked_symbols   (symbol, locked_at, months, hash, note)
locks            (id, kind, scope_from, scope_to, status, rows, symbols, hash, monthly_only, locked_at, note)
datasets         (id, name, created_at, symbols, months, source_mix, input_hash, status, locked_at, notes)
```

> ⚠ `db/schema.sql` 曾是与线上表完全不同名的废稿，2026-11-06 已归档至 `_archive/2026-11-06/db/schema.sql`。线上结构一律以 `sql/*.sql` 为准。

### 1.2 代码 → 表 矩阵

| 表 | 读 | 写 | 关键列 | 来源文件（行号） |
|---|---|---|---|---|
| **series_selected** | `db_select.load_all` → `db_prices.load_all`；`lock_stage.measure`；`lock_symbols.candidates`；`db_dataset.build`；`db_validate`；`variant_b._db_sha`；`db_rest.load_selected` | `db_select.build_table`（drop→create→rename） | `symbol,month,adj_close,raw_close,source,basis` | db_select.py:68,121-122,145；db_prices.py:143-145；lock_stage.py:80,89；lock_symbols.py:109,120；db_dataset.py:62；db_validate.py:75；variant_b.py:51-53；db_rest.py:76 |
| **prices_monthly** | `db_prices.series/symbols/coverage/stats/_db_stamp/_load_all_legacy`；`variant_b._db_sha`；`lock_stage._measure_raw`；`relay_source`/`plan_gaps`/`allocate_work`/`fetch_stockanalysis`/`backfill_yahoo`/`audit_membership` 的取候选集 | `db_prices.write_rows`；`relay_source`（execute_values）；`fetch_stockanalysis`；`promote_staging()`（库内函数，pg_cron） | `symbol,month,adj_close,raw_close,source,quality,basis,fetched_at` | db_prices.py:66,83,91,101,139,218；variant_b.py:47-49；lock_stage.py:99-113；db_select.py:59；db_staging.py:98 |
| **dataset_rows** | `db_dataset.build`（trace 检查）；历史上有一条 `select symbol,month,adj_close from dataset_rows where dataset_id=$1`（`pg_stat_statements` 实测 **mean 15.1 s / 458,228 行**，当前代码库已不存在，属历史冷路径） | `db_dataset.build`；`db_init.load_baseline`；`update dataset_rows set adj_close`（pgsS 实测 2 次） | `dataset_id,symbol,month,adj_close,source,basis,raw_close` | db_dataset.py:62-86；db_init.py:160 |
| **datasets** | `db_prices.dataset_id`；`db_dataset.listing`；`db_validate` | `db_dataset.build`（`delete` 会 **CASCADE 删 dataset_rows**，实测删了 229,115 行）；`db_init` | `id,name,status,symbols,months,input_hash` | db_dataset.py:56-79；db_prices.py:113；db_init.py:153 |
| **validations** | 无（只写） | `db_prices.log_validation`；`db_validate.main`；`db_dataset.build` | `dataset_id,check_name,status,detail,ran_at` | db_prices.py:120；db_validate.py:171；db_dataset.py:88 |
| **members** | `db_ref.load_members`（DB→REST 兜底） | `db_ref.bootstrap`（仅表空时） | `symbol,start_date,end_date` | db_ref.py:109,120 |
| **sectors** | `db_ref.load_sectors`（DB→REST 兜底） | `db_ref.bootstrap` | `symbol,sector` | db_ref.py:133,140 |
| **locked_symbols** | `guard_locked_months()`（**每行写入都查**）；`lock_symbols.main/--list` | `lock_symbols.main`（`on conflict do nothing`） | `symbol,months,hash` | lock_symbols.py:59,153,170 |
| **locks** | `guard_locked_months()`（**每行写入都遍历**） | `lock_stage.lock` | `scope_from,scope_to,status,kind` | lock_stage.py:38,148 |
| **source_registry** | `relay_source.plan_sources`（调度唯一真相） | `db_sources.seed` | `source_id,priority,basis` | relay_source.py:86；db_sources.py:134 |
| **data_sources** | 无（CLI 打印） | `db_credentials.store` | `source_id,credential` | db_credentials.py:94 |
| **ingest_staging** | `promote_staging()` | `staging_write.py`（**REST POST，anon key**）；`promote_staging()` 标记 | `symbol,month,adj_close,raw_close,source,basis,promoted,reject_reason` | staging_write.py:44；db_staging.py:75-114 |
| **fetch_runs** | 无 | `db_prices.log_run` | `source,requested,ok,failed,started_at,finished_at,note` | db_prices.py:251 |
| **work_plans** | 无 | `allocate_work` | `plan jsonb` | allocate_work.py:146,149 |
| **holdings** | `live_audit`（REST 读 `/rest/v1/holdings`） | 站点云同步（REST） | `id,payload,updated_at` | live_audit.py:24 |
| `symbols` / `corporate_actions` | — | — | — | **0 行，无人使用**（可删） |

**不碰库的文件**（重要，别误伤）：`quotes.py`、`monthly_pick.py`、`equity.py`、`site_build.py`、`strategy_history.py`、`preview.py`、`datafeed.py`、`audit_b.py`（只经 `db_prices`）——它们吃 `output/*.json` / `docs/data/*.json` 产物。`audit_b.py` 与 `selftest.py` 是例外：它们**直接**调 `db_prices.load_all()`。

### 1.3 热路径分级

| 级别 | 定义 | 清单 |
|---|---|---|
| 🔴 **P0 每次构建必跑** | 被 `quotes.yml`（每 2 h + 盘中每 5 min）或 `collect-full.yml`（每 6 h）直接调用 | `db_prices.load_all → db_select.load_all → series_selected`（全表读）<br>`db_prices._db_stamp`（**mean 1,780 ms**，每次 load_all 都跑）<br>`db_select.build_table`（drop+rename，每 6 h）<br>`db_validate`（写 validations，每次）<br>`lock_stage --check`（每次）<br>`variant_b._db_sha`（每次）<br>`db_rest.load_selected`（CI 无口令时的兜底，**PostgREST 实测 31,200 次调用**） |
| 🟠 **P1 定时写** | `collect-full.yml` | `relay_source` → `prices_monthly` 写入<br>`fetch_stockanalysis` → `prices_monthly` 写入<br>`lock_symbols --lock` → `locked_symbols` + **重写 `guard_locked_months()`**<br>`promote_staging()`（pg_cron 每 10 min）→ `prices_monthly` upsert |
| 🟡 **P2 手动/低频** | 手动或一次性 | `db_dataset.build`、`db_hardening`、`db_staging.setup`、`db_sources`、`db_credentials`、`allocate_work`、`plan_gaps`、`backfill_yahoo`、`audit_membership`、`db_init` |

### 1.4 库外写入方（grep 不到，但真实存在）

| 来源 | 调度 | 动作 | 近 7 天 |
|---|---|---|---|
| `cron.job` #4 `trade-dispatch` | `20 */2 * * *` | `dispatch_trade_pipeline()` → `net.http_post` 打 GitHub API，**dispatch `quotes.yml``** | 40 次 |
| `cron.job` #5 `trade-dispatch-market` | `*/5 13-21 * * 1-5` | 同上 → **美股盘中每 5 分钟触发一次 `quotes.yml`** | **196 次** |
| `cron.job` #6 `promote_staging` | `*/10 * * * *` | `promote_staging()` → **upsert 进 `prices_monthly`** | 3 次 |
| PostgREST (`authenticator`, 8 s/8 s 超时) | 常驻 | `anon` 只读 `/rest/v1/series_selected` 等 | `series_selected` **31,200 次** |
| `anon` 角色（3 s 超时） | — | 有 `prices_monthly` 全写权限（RLS 关） | — |

### 1.5 `pg_stat_statements` 实测调用量（最硬的依赖证据）

```
series_selected   PostgREST 分页查询            15,975 + 13,688 + 1,180 + 357 次
prices_monthly    _db_stamp（全表聚合）            163 次, mean 1,780 ms, 累计 290 s
prices_monthly    load_all 分片 (any(array))        57 次, 1,314,901 行, mean 67 ms
prices_monthly    write_rows (execute_values)       45 次, 225,000 行, mean 81 ms
dataset_rows      INSERT…SELECT from series_sel      1 次, 118,719 行, mean 5,207 ms
dataset_rows      md5(string_agg) 全表               1 次, mean 4,362 ms
dataset_rows      可追溯性反连接                      1 次, mean 3,600 ms
locked_symbols    INSERT on conflict do nothing      860 次
guard_locked_months  被 CREATE OR REPLACE            9 次（lock_stage 版）+ 5 次（lock_symbols 版）+ 1 次（db_hardening 版）
```

**结论**：线上触发器函数体是 **`lock_symbols.py` 的版本**（不是 `db_hardening.py` 的），而且 `collect-full.yml` 每次跑 `lock_symbols.py --lock` 都会 `CREATE OR REPLACE` 重写它 —— 任何手工加固都会被流水线覆盖回去。

**索引扫描 Top（说明谁在挨着谁）**

```
prices_monthly_pkey                1,046,262 次   ← upsert / 触发器不存在，是 upsert 主键探测
datasets_pkey                        924,806 次   ← guard_locked_dataset() 每行一次
locks (kind,scope_from,scope_to)     314,035 次   ← guard_locked_months() 每行一次
series_selected PK                   118,755 次
locked_symbols_pkey                    2,119 次   ← guard_locked_months() 每行一次
idx_prices_basis                          25 次   ← 低基数，几乎无用
idx_staging_pending                        0 次   ← 完全无用
```

---

## ② 风险表

| # | 风险 | 触发条件 | 实测证据 | 规避 |
|---|---|---|---|---|
| R1 | **DDL 锁队列把站点点死** | 任何 `ALTER TABLE`/`RENAME` 在有人读时发出，`lock_timeout=0`（默认）→ 无限排队，**排队期间所有新读被堵** | 一个只有 `select count(*)` 的未提交事务，就让 `ADD COLUMN` 在 2,069 ms 被 `lock_timeout` 打断 | 每次 DDL 前 `SET LOCAL lock_timeout='3s'`；失败即重试（指数退避）；**绝不裸发 DDL** |
| R2 | **430 只锁定标的一写就炸** | 往 `prices_monthly` 灌任何含已锁 symbol 的行 | `update prices_monthly set quality=quality where symbol='A'` → `symbol A is locked (locked_symbols)` | 灌进**无触发器的新表**；旧表只在最后做 rename（不写数据） |
| R3 | **触发器把 351k 行灌数拖慢 7.4×** | 在旧表上 `INSERT…SELECT` | 200 只 / 83,802 行：开 1,230 ms vs 关 167 ms（≈12.7 µs/行） | 同上。影子表无触发器，等价 167 ms |
| R4 | **disable trigger 造成永久裸奔** | `DISABLE TRIGGER` 与灌数不在同一事务，进程崩溃/超时中断 → 没人 `ENABLE` | `guard_locked_months` 是目前唯一挡 anon 改写的屏障；它一旦长期关闭，`anon` 可直接篡改 430 只已锁历史 | 见 3.8：**只允许**在单事务内 disable+写+enable；或干脆不用 |
| R5 | **RLS 默认全开，新表立刻裸奔** | 在 `public` 建任何表 | `pg_default_acl`: `postgres@public r → anon=arwdDxtm`（含 TRUNCATE） | `CREATE TABLE` 同事务内 `REVOKE ALL … FROM anon, authenticated` + `ENABLE ROW LEVEL SECURITY` |
| R6 | **`prices_monthly` 现在对全网可写** | 拿公开 anon key 发 REST POST/PATCH/DELETE | `relrowsecurity=false`、0 策略、anon 有 I/U/D | 迁移 Phase 1 顺手 `REVOKE` + `ENABLE RLS` + `public_read` 策略；**这是现存 P0，与迁移解耦也要修** |
| R7 | **`series_selected` 重建窗口 = 真实停服** | `collect-full.yml` 每 6 h 跑 `db_select.py` | drop 与 rename 是 2 条 autocommit 语句；pgsS `drop table if exists series_selected` ×6、`rename` ×6；实测该表在 01:08:47 UTC 被重建，01:00 时残留的 `series_selected_new` 01:12 已不存在 | 改成**单事务原子切换**（3.5），或改用"代际表 + 视图指针" |
| R8 | **PostgREST 预编译语句失效 → CI 404/500** | 表被 rename/drop 时 PostgREST 仍持有 prepared statement | `db_rest.get()` 有 4 次重试（3/6/9 s 退避）→ 能自愈；但 `db_select.load_all()` 首次分片失败就 `raise`，**不自愈** | 切前后 `NOTIFY pgrst, 'reload schema'`；直连读者必须自己加重试 |
| R9 | **REST 单页被 3 s / 8 s 打死** | 兼容视图不是"单表投影"，或加了 JOIN | `anon` 角色 `statement_timeout=3s`；`authenticator` `8s` | 兼容视图只做 `select <列>` 直投影；需要 JOIN 就物化成表 |
| R10 | **`promote_staging` 在你切表时写旧表** | pg_cron #6 每 10 min 跑一次，无感知 | 3 次/7 天，但随机落在迁移窗口 | Phase 0 `cron.alter_job(active:=false)` 暂停 #4/#5/#6，Phase 6 恢复 |
| R11 | **`datasets` 的 CASCADE 会清空 `dataset_rows`** | `db_dataset.build` 里 `delete from datasets where name=…` | 实测 `dataset_rows` 累计 delete **229,115** 行 | 迁移期间禁止跑 `db_dataset.build`；或先改代码用 `status='archived'` |
| R12 | **`dataset_rows` 已与 `series_selected` 漂移** | 任何"数据集必须一致"的验收断言 | 实测：dataset 1 漂移 **206,126** 行、dataset 2 漂移 **7,759** 行 | 不要把 `dataset_rows` 当基线；验收只认 `series_selected` ↔ `prices_monthly` |
| R13 | **长连接被池化器/网络掐断** | 客户端在两条语句之间空闲数十秒，或网络抖动 | 实测 1 次连接失败 + 1 次 `SSL SYSCALL error: EOF detected`（空闲 20 s 后） | 所有迁移脚本必须有 **重连 + 幂等重放**；进度落 `_mig_progress` 表 |
| R14 | **`idle_in_transaction_session_timeout=0`** | 脚本异常退出留下未提交事务 | 实测为 0（永不清理） | 迁移脚本 `finally: rollback/close`；开工前查 `pg_stat_activity` 清场 |
| R15 | **`locks` 唯一键冲突** | `lock_stage.lock` 用 `on conflict (kind,scope_from,scope_to)` | 表当前 0 行，且有 UQ | 迁移不改 `locks` 语义；若重建表必须重建该 UQ，否则 `lock_stage` 直接报错 |
| R16 | **加固脚本被流水线覆盖** | 手工跑 `db_hardening.py` 后，下一次 `collect-full` 跑 `lock_symbols.py --lock` | pgsS：`create or replace function guard_locked_months()` 的 lock_symbols 版本被调用 5 次 | 加固必须做成**幂等迁移脚本**并在 Phase 6 后固化；或让 `lock_symbols.py` 不再重建函数 |

---

## ③ 分阶段脚本骨架

> 目标形态（推荐）
> - `prices_monthly` 保持"多源事实表"语义不变，只做**权限收口 + 影子表原子换名**。
> - `series_selected` 从"被反复 drop/rename 的物理表"改为 **代际物理表 + 视图指针**，切换是 `CREATE OR REPLACE VIEW`（纯 catalog，毫秒级，名字永不消失）。
> - 理由：`series_selected` 对**所有**消费者都是只读（写它的只有 `db_select.py`），所以视图完全够用；而 `prices_monthly` 有写入方，必须是可写实体表。

### 3.0 公共设施（所有阶段复用）

```python
# mig_common.py —— 重连、锁超时、进度落库、guard
import io, os, time, contextlib
import psycopg2

PW = io.open("output/_supabase_dbpass.txt", encoding="utf-8").read().strip()
DSN = dict(host="aws-0-ap-northeast-1.pooler.supabase.com",
           user="postgres.fnvahsyrzmmxwovbsqpc", password=PW,
           dbname="postgres", connect_timeout=10, sslmode="require")

def conn(port=5432):                     # ← 迁移用 5432（会话模式，COPY/临时表/SET 都稳）
    for i in range(6):
        try:
            c = psycopg2.connect(port=port, **DSN)
            c.autocommit = False
            with c.cursor() as cur:
                cur.execute("set application_name = 'migration'")   # 出事能一眼找到自己
            return c
        except Exception as e:
            if i == 5: raise
            time.sleep(min(2 ** i, 20))

@contextlib.contextmanager
def step(lock_to="3s", stmt_to="600s", label=""):
    """每个 DDL 单元：短锁超时 + 长语句超时 + 自动重试 5 次。
    关键：lock_timeout 保证我们绝不进锁队列堵住站点。"""
    for attempt in range(6):
        c = None
        try:
            c = conn()
            with c.cursor() as cur:
                cur.execute("set local lock_timeout = %s", (lock_to,))
                cur.execute("set local statement_timeout = %s", (stmt_to,))
                yield c, cur
            c.commit()
            return
        except psycopg2.errors.LockNotAvailable:
            if c: c.rollback()
            wait = 1.5 * (attempt + 1)
            print("  [%s] 抢锁失败，%.1fs 后重试（第 %d 次）" % (label, wait, attempt + 1))
            time.sleep(wait)
            if attempt == 5:
                raise RuntimeError("%s: 连续 6 次拿不到锁，迁移中止（站点未受影响）" % label)
        except Exception:
            if c: c.rollback()
            raise
        finally:
            if c:
                try: c.close()
                except Exception: pass
```

```sql
-- 进度表：断点续传 + 互斥（同时只允许一个迁移在跑）
create table if not exists _mig_progress (
  job        text primary key,
  last_key   text,
  done       bool default false,
  note       text,
  updated_at timestamptz default now()
);
revoke all on _mig_progress from anon, authenticated;
alter table _mig_progress enable row level security;
```

```sql
-- 互斥：拿不到就说明有别的迁移在跑
select pg_try_advisory_lock(987654321) as got_lock;   -- 必须在同一连接上持有
```

---

### Phase 0 — 冻结写入方 + 固化基线（5 分钟，可秒级回退）

```python
# phase0_freeze.py
import json, time, psycopg2
from mig_common import step, conn

JOBS = [4, 5, 6]      # trade-dispatch / trade-dispatch-market / promote_staging
with step(label="freeze-cron") as (c, cur):
    for j in JOBS:
        cur.execute("select cron.alter_job(job_id := %s, active := false)", (j,))
    # 记录原状态，回滚要用
    cur.execute("select jobid, jobname, schedule, active from cron.job where jobid = any(%s)", (JOBS,))
    print("已暂停：", cur.fetchall())

# 基线快照（见 output/_mig_baseline.py，产物 output/migration_baseline.json）
```

**同时**：GitHub 侧暂停 `collect-full.yml` / `quotes.yml`（`gh workflow disable`），否则 `lock_symbols.py` 会重写触发器、`db_select.py` 会重建表。

```bash
gh workflow disable "采集与锁定（CI 主力，写库）" -R dear202606-star/trade
gh workflow disable "行情快照" -R dear202606-star/trade
```

**回滚**：`cron.alter_job(job_id := 4, active := true)` × 3 + `gh workflow enable`。

---

### Phase 1 — 建新表（**权限原子化**，这是最容易漏的一步）

```sql
-- 一条事务里把「表 + 权限 + RLS」做完，杜绝 anon 看见半成品
begin;
set local lock_timeout = '3s';

-- 1) 代际表（series_selected 的下一代）
create table if not exists series_selected_g20261101 (
  symbol    text not null,
  month     text not null,
  adj_close numeric,
  raw_close numeric,
  source    text,
  basis     text,
  constraint series_selected_g20261101_pk primary key (symbol, month)
);

-- 2) 立刻收权限（public schema 默认会把 anon 加上 arwdDxtm）
revoke all on series_selected_g20261101 from anon, authenticated;
alter table series_selected_g20261101 enable row level security;

commit;
```

```sql
-- 3) 索引：建在灌数之后（先建索引会让 351k 行插入慢 1.1 倍，实测 824 ms vs 757 ms）
create index idx_ss_g_month on series_selected_g20261101 (month);
```

> **不要**建 `(symbol,month)` 主键在 `prices_monthly` 上 —— 实测失败：
> `could not create unique index: Key (symbol, month)=(RSG, 2013-08) is duplicated`。
> 多源分布实测：1 源 130,263 组 / 2 源 107,918 组 / 3 源 1,930 组 = 240,111 组 vs 351,889 行。
> 任何"一行一票一月"的折叠都会丢 **111,778 行** 的多源信息。

---

### Phase 2 — 分批灌数

**实测耗时参考（同一库、同一网络）**

| 粒度 | 行数 | 耗时 | 备注 |
|---|---:|---:|---|
| 按 symbol 50 只 | 21,396 | **104 ms** | 最小批，适合边跑边观测 |
| 按 symbol 200 只 | 83,217 | **173 ms** | 推荐批大小 |
| 按 symbol 400 只 | 164,608 | **265 ms** | 4 批跑完 |
| 按 year 单年 | 13–15 k | **175–190 ms** | 20 批 |
| **全表一次** | 351,889 | **757 ms** | ✅ 完全可行 |
| 全表 + 建索引 | 351,889 | 824 ms | |
| 200 只 / 开触发器 | 83,802 | **1,230 ms** | 灌旧表才有这个代价 |
| md5 全表指纹 | 351,889 | 2,112 ms | 校验成本 |
| 全表双向 EXCEPT | 351,889 | ~3–4 s | 校验成本 |

**结论：这个库不需要分批来躲超时（120 s 上限 vs 757 ms）。分批的唯一价值是"每批一个短事务、失败只回滚一批、可断点续传"。**

```python
# phase2_load.py —— 按 symbol 分片、幂等、可断点续传
import psycopg2.extras
from mig_common import conn, step

BATCH = 200
JOB = "ss_g20261101"

SELECTION = """
with cand as (
  select symbol, source,
         max(case when basis = 'total_return' then 0 else 1 end) as basis_rank,
         count(*) filter (where month between '2010-01' and '2025-12') as cov,
         case source when 'alphavantage' then 1 when 'yahoo_relay' then 2
              when 'eodhd' then 3 when 'hf_amirtrader' then 3
              when 'hf_ayushkhaire' then 4 when 'sina_repaired+eodhd' then 4
              else 5 end as src_rank
  from prices_monthly group by symbol, source
), pick as (
  select symbol, source from (
    select symbol, source,
           row_number() over (partition by symbol
                              order by basis_rank, src_rank, cov desc) rn
    from cand) y where rn = 1
)
"""

def load_one_batch(lo, hi):
    sql = SELECTION + """
    insert into series_selected_g20261101 (symbol, month, adj_close, raw_close, source, basis)
    select p.symbol, p.month, p.adj_close, p.raw_close, p.source, p.basis
    from prices_monthly p
    join pick on pick.symbol = p.symbol and pick.source = p.source
    where p.symbol > %s and p.symbol <= %s
      and p.month >= '2008-01' and p.adj_close is not null
    on conflict (symbol, month) do update
      set adj_close = excluded.adj_close, raw_close = excluded.raw_close,
          source = excluded.source, basis = excluded.basis
    """
    with step(label="batch %s..%s" % (lo, hi)) as (c, cur):
        cur.execute(sql, (lo, hi))
        return cur.rowcount

# 取分片边界（一次，很便宜：848 个 symbol）
c = conn()
with c.cursor() as cur:
    cur.execute("select distinct symbol from prices_monthly order by 1")
    syms = [r[0] for r in cur.fetchall()]
c.close()

for i in range(0, len(syms), BATCH):
    lo = syms[i - 1] if i else ""
    hi = syms[min(i + BATCH, len(syms)) - 1]
    while True:
        try:
            n = load_one_batch(lo, hi)
            print("  %-6s..%-6s  %6d 行" % (lo or "∅", hi, n))
            with step(label="progress") as (c2, cur2):
                cur2.execute("""insert into _mig_progress(job, last_key, updated_at)
                                values (%s,%s,now())
                                on conflict (job) do update
                                set last_key = excluded.last_key, updated_at = now()""",
                             (JOB, hi))
            break
        except Exception as e:          # R13：网络断了就重来这一批（幂等）
            print("  批 %s..%s 失败，重试：%s" % (lo, hi, str(e)[:80]))
```

> **为什么不灌进旧表**：实测 430 只已锁标的会让任何含它们的写入 `raise`；且触发器让 83,802 行从 167 ms 变 1,230 ms。影子表没有触发器，天然免疫 R2/R3。

> **如果用游标（server-side cursor）**：`psycopg2` 的 named cursor 在事务池模式下会报
> `cursor "xxx" does not exist`，因为下一条语句可能落到别的后端。**必须用 5432 会话模式**，
> 或干脆用上面的"按 symbol 分片"（推荐，无状态）。

---

### Phase 3 — 一致性校验（四层，逐层可独立判定）

```sql
-- 3.1 行数 + 标的数 + 口径构成（30 ms 级）
select 'old' side, count(*) n, count(distinct symbol) syms,
       count(*) filter (where basis='total_return') tr, min(month), max(month)
from series_selected
union all
select 'new', count(*), count(distinct symbol),
       count(*) filter (where basis='total_return'), min(month), max(month)
from series_selected_g20261101;
-- 期望 old = new = (117113, 848, 94993, '2008-01', '2026-10')
```

```sql
-- 3.2 内容指纹（顺序无关；实测 2.1 s）
select 'old' s, md5(string_agg(h,'|' order by h)) from (
  select symbol||'|'||month||'|'||coalesce(adj_close::text,'')||'|'||
         coalesce(raw_close::text,'')||'|'||coalesce(source,'')||'|'||coalesce(basis,'') h
  from series_selected) z
union all
select 'new', md5(string_agg(h,'|' order by h)) from (
  select symbol||'|'||month||'|'||coalesce(adj_close::text,'')||'|'||
         coalesce(raw_close::text,'')||'|'||coalesce(source,'')||'|'||coalesce(basis,'') h
  from series_selected_g20261101) z;
-- 期望两侧都是 86369418dd12a88b4bf1b7ddec896bc8（迁移前基线）
```

```sql
-- 3.3 逐行双向比对（EXCEPT，任一方向都必须 0 行）
select 'old-not-in-new' d, count(*) from (
  select symbol,month,adj_close,raw_close,source,basis from series_selected
  except
  select symbol,month,adj_close,raw_close,source,basis from series_selected_g20261101) a
union all
select 'new-not-in-old', count(*) from (
  select symbol,month,adj_close,raw_close,source,basis from series_selected_g20261101
  except
  select symbol,month,adj_close,raw_close,source,basis from series_selected) b;
-- 期望 0 / 0
```

```sql
-- 3.4 抽样比对（1% 系统抽样，定位"哪几行不一样"，比 EXCEPT 好排查）
with s as (select * from series_selected tablesample system (1))
select s.* from s
full join series_selected_g20261101 n using (symbol, month)
where n.symbol is null
   or s.adj_close is distinct from n.adj_close
   or s.raw_close is distinct from n.raw_close
   or s.source    is distinct from n.source
   or s.basis     is distinct from n.basis
limit 50;
```

```sql
-- 3.5 业不变式（这两条基线现在就是 0，迁移后必须还是 0）
-- (a) 每一行 series_selected 都能在 prices_monthly 找到"同源同月同值"
select count(*) from series_selected_g20261101 s
where not exists (select 1 from prices_monthly p
                  where p.symbol=s.symbol and p.month=s.month
                    and p.source=s.source and p.adj_close=s.adj_close);
-- 期望 0

-- (b) 落表选源 == 现场重算选源（防"表里是旧算法、代码是新算法"）
with cand as (
  select symbol, source,
         max(case when basis = 'total_return' then 0 else 1 end) basis_rank,
         count(*) filter (where month between '2010-01' and '2025-12') cov,
         case source when 'alphavantage' then 1 when 'yahoo_relay' then 2
              when 'eodhd' then 3 when 'hf_amirtrader' then 3
              when 'hf_ayushkhaire' then 4 when 'sina_repaired+eodhd' then 4
              else 5 end src_rank
  from prices_monthly group by symbol, source),
pick as (select symbol, source from (select symbol, source,
         row_number() over (partition by symbol order by basis_rank, src_rank, cov desc) rn
         from cand) y where rn = 1)
select count(*) from series_selected_g20261101 s
left join pick p on p.symbol = s.symbol
where p.source is distinct from s.source;
-- 期望 0（迁移前基线也是 0）
```

```sql
-- 3.6 每个 symbol 的独立指纹（最细粒度；可存库做长期回归）
select symbol, count(*) n,
       md5(string_agg(month||'|'||coalesce(adj_close::text,''), ';' order by month)) h
from series_selected_g20261101 group by symbol order by symbol;
```

**校验闸门**（Python 侧）：3.1/3.2/3.3/3.5 任一不满足 → **立即中止，不切视图**。

---

### Phase 4 — 原子切视图（**核心：把"表"换成"指针"**）

```sql
-- 4.1 先给新表建好 anon 只读策略（必须在切换前完成，否则切换后 anon 读到 0 行）
begin;
set local lock_timeout = '3s';
create policy public_read on series_selected_g20261101
  for select to anon, authenticated using (true);
commit;
```

```sql
-- 4.2 切换：CREATE OR REPLACE VIEW 保留对象 OID → 既有 GRANT 与依赖自动延续
--     纯 catalog 操作，毫秒级，无数据锁，"series_selected" 这个名字永不消失
begin;
set local lock_timeout = '3s';

create or replace view series_selected as
  select symbol, month, adj_close, raw_close, source, basis
  from series_selected_g20261101;

commit;

notify pgrst, 'reload schema';
```

> **前提**：`series_selected` 当前必须是**表**，且第一次切换要先把它改名让出名字：
>
> ```sql
> begin;
> set local lock_timeout = '3s';
> alter table series_selected rename to series_selected_old_20261101;   -- 实测 103 ms
> create view series_selected as select symbol,month,adj_close,raw_close,source,basis
>   from series_selected_g20261101;
> grant select on series_selected to anon, authenticated;
> commit;
> ```
>
> 这一次切换有 **~100–200 ms 的 ACCESS EXCLUSIVE**（两次 rename 的实测值）。这是**唯一**不可避免的短锁。
> 之后所有换代都走 `CREATE OR REPLACE VIEW`，**零数据锁**。

**PostgREST 注意**：`anon` 的 `statement_timeout=3s`。视图必须是单表直投影——实测这个视图的
`select ... where month between '2015-01' and '2015-12'` 走 `Index Only Scan`，25 ms 内返回，
分 118 页拉全表（`db_rest` 的 `PAGE=1000`）完全够用。**加 JOIN 就会超 3 s，REST 兜底立刻失效。**

---

### Phase 5 — 切代码

**5.1 `db_select.py` 改成"代际构建 + 原子指针"**（消灭 R7 的停服窗口）

```python
# db_select.py（替换 build_table）
GEN = "series_selected_g%s" % time.strftime("%Y%m%d%H%M%S")

def build_table():
    # 1) 建代际表（继承 DDL 模板）—— 无触发器、无数据锁
    # 2) 灌数（单事务或按 symbol 分批，见 Phase 2）
    # 3) 校验（Phase 3 的 3.1/3.2/3.3/3.5；任一不过 → drop 代际表并抛错）
    # 4) 单事务切指针
    with step(lock_to="3s", label="swap-view") as (c, cur):
        cur.execute("""create or replace view series_selected as
                       select symbol, month, adj_close, raw_close, source, basis
                       from %s""" % GEN)
        cur.execute("notify pgrst, 'reload schema'")
    # 5) 记录一代（数据集/校验台账）
    # 6) 只保留最近 2 代，第 3 代起 drop（延迟到下一轮，留出回滚窗口）
```

**5.2 `db_prices.load_all` 的缓存戳必须跟着改**

`_db_stamp()` 现在跑 `select count(*), max(fetched_at), count(distinct symbol) from prices_monthly`
—— 实测 **mean 1,780 ms**，是每次构建最贵的单条查询。既然 `series_selected` 变成视图，
指纹应改为读**代际表的元数据行**（在切视图时写一行）：

```sql
create table if not exists db_stamp (
  id int primary key default 1,
  rows bigint, max_fetched text, symbols int,
  ss_rows bigint, ss_symbols int, ss_sum numeric, ss_gen text,
  updated_at timestamptz default now()
);
revoke all on db_stamp from anon, authenticated;
alter table db_stamp enable row level security;
```
`_db_stamp()` 改为 `select * from db_stamp where id=1`（< 1 ms），把 1.8 s 降成 1 ms。

**5.3 其他调用点**（全部只读，零改动）
`lock_stage.py`、`lock_symbols.py`、`db_dataset.py`、`db_validate.py`、`variant_b.py`、
`audit_b.py`、`selftest.py`、`db_rest.py` 都只用 `select … from series_selected`，视图透明兼容。
**唯一要改的是 `db_select.py`（写方）**。

**5.4 `lock_symbols.py` 不能再重建触发器**（R16）
把 `ensure()` 里的 `cur.execute(TRIGGER)` 移到独立的、显式带版本的迁移脚本；或加
`if not _trigger_is_current():` 守卫。

---

### Phase 6 — 删旧表（**延迟 ≥ 7 天**，跨过至少一次 `collect-full` 与一次周一 `monthly-pick`）

```sql
-- 6.1 观察期内确认没有读者（索引扫描计数归零是硬证据）
select relname, idx_scan, seq_scan, n_live_tup
from pg_stat_user_tables s join pg_stat_user_indexes i using (relid)
where relname like 'series_selected_old%' or relname like 'series_selected_g%';

-- 6.2 正式下线
begin;
set local lock_timeout = '3s';
revoke all on series_selected_old_20261101 from anon, authenticated;
drop table series_selected_old_20261101;
drop table if exists series_selected_g20260901;   -- 更老的一代
commit();

-- 6.3 顺手清理死索引（实测 idx_prices_basis 只被扫 25 次、idx_staging_pending 0 次）
--     注意：idx_prices_symbol 与 PK(symbol,month,source) 前缀重复，删前先看 plan
begin;
set local lock_timeout = '3s';
drop index if exists idx_staging_pending;   -- 0 次扫描
commit;
```

---

### 3.8 触发器 disable 的正确姿势与风险（专章）

**选项排序（从最好到最差）**

| 方案 | 做法 | 锁 | 风险 | 评价 |
|---|---|---|---|---|
| **A. 不碰旧表**（推荐） | 灌进无触发器的影子表；旧表只做最后 rename（不写数据） | 仅 rename 的 ~100 ms | 无 | ✅ **唯一真正的零停机方案** |
| B. 单事务 disable+写+enable | `BEGIN; ALTER TABLE … DISABLE TRIGGER trg_guard_months; <全部写入>; ALTER TABLE … ENABLE TRIGGER trg_guard_months; COMMIT;` | **ACCESS EXCLUSIVE 贯穿整个事务**（351k 行 ≈ 0.6 s 关触发器 + 开关本身），期间**所有读被堵** | 可接受（1–5 s），但必须公告 | ⚠ 只在必须原地修数据时用 |
| C. 事务外 disable，跑完再 enable | 先 disable（提交），再分批写，最后 enable | 短 | **灾难级**：真空期从几十秒到几小时；进程崩溃/CI 超时 → 永久裸奔；`anon` 此刻可任意篡改 430 只已锁历史 | ❌ 禁止 |
| D. `session_replication_role = replica` | 全局关触发器 | 无 | 需要 **superuser**；实测 `postgres` 的 `usesuper = false` → 会直接报错。且会连外部约束一起关掉 | ❌ 不可用 |

**B 方案的完整可复制骨架**（含"崩溃自愈"）

```python
# 只有在必须原地改 prices_monthly 时才用
from mig_common import step

with step(lock_to="3s", stmt_to="900s", label="inplace-fix") as (c, cur):
    # 1) 先写一条"护栏已摘"的标记，供看门狗兜底
    cur.execute("""insert into _mig_progress(job, note, updated_at)
                   values ('trigger_disabled_at', now()::text, now())
                   on conflict (job) do update set note = excluded.note, updated_at = now()""")

    # 2) 摘护栏（需要 ACCESS EXCLUSIVE，已被 lock_timeout 保护）
    cur.execute("alter table prices_monthly disable trigger trg_guard_months")

    # 3) 全部写入 —— 注意：这里必须是"能在一个事务里跑完"的量
    #    （351,889 行关触发器约 0.6 s，完全跑得下）
    cur.execute("""update prices_monthly set quality = 'ok'
                   where quality is null""")

    # 4) 立刻装回去
    cur.execute("alter table prices_monthly enable trigger trg_guard_months")
    cur.execute("delete from _mig_progress where job = 'trigger_disabled_at'")
# step() 里 commit；任何异常都会 rollback → 触发器自动恢复（因为 DDL 是事务性的）
```

**看门狗**（防止"提交了 disable 却忘了 enable"，即 C 方案的残留）：

```sql
-- 一条随时可跑的体检
select tgname, tgenabled
from pg_trigger where tgrelid = 'public.prices_monthly'::regclass and not tgisinternal;
-- 期望恰好 1 行、tgenabled = 'O'；出现 'D' 立即：
--   alter table prices_monthly enable trigger trg_guard_months;
```

**要点**：`ALTER TABLE … DISABLE/ENABLE TRIGGER` 是**事务性 DDL**，rollback 会自动撤销。这就是
"B 安全、C 危险"的根本原因 —— 安全边界不在语法，而在**是否用事务包住**。

---

### 3.9 失败回滚脚本骨架

```python
# rollback.py —— 按 Phase 逆序，每步幂等
from mig_common import step

def rb_view():
    """视图已切回：把 series_selected 变回实体表"""
    with step(lock_to="3s", label="rb-view") as (c, cur):
        cur.execute("select table_type from information_schema.tables "
                    "where table_schema='public' and table_name='series_selected'")
        kind = cur.fetchone()
        if kind and kind[0] == 'VIEW':
            cur.execute("drop view series_selected")
        cur.execute("""do $$
        begin
          if exists (select 1 from pg_class c join pg_namespace n on n.oid=c.relnamespace
                     where n.nspname='public' and c.relname='series_selected_old_20261101') then
            alter table series_selected_old_20261101 rename to series_selected;
          end if;
        end $$""")
        cur.execute("notify pgrst, 'reload schema'")

def rb_cron():
    with step(label="rb-cron") as (c, cur):
        for j in (4, 5, 6):
            cur.execute("select cron.alter_job(job_id := %s, active := true)", (j,))

def rb_drop_gen(gen):
    with step(lock_to="3s", label="rb-drop-gen") as (c, cur):
        cur.execute("drop table if exists %s" % gen)   # gen 名必须来自白名单，不可拼接外部输入
```

**回滚判定顺序**：`rb_view()` → `rb_cron()` → `rb_drop_gen()`。
**永远不要**在回滚里 `DELETE`/`UPDATE` 数据 —— 只做 catalog 反转，数据侧靠"保留旧表"兜底。

---

## ④ 验收清单（逐条可执行）

> 全部对照 `output/migration_baseline.json`（`captured_at 2026-11-01T09:13:28+08:00`, git `e1856ca`）。
> 判定规则：**每条都必须给出"期望值 = 基线值"**，任一不符即**回滚**，不讨论。

### A. 数据库不变式

| # | 判据 | 可执行命令 | 期望 |
|---|---|---|---|
| A1 | `series_selected` 行数 | `select count(*) from series_selected` | **117,113** |
| A2 | 内容指纹 | 见 3.2 | **`86369418dd12a88b4bf1b7ddec896bc8`** |
| A3 | 标的数 / 全收益行 / 覆盖 | `select count(distinct symbol), count(*) filter (where basis='total_return'), min(month), max(month) from series_selected` | **848 / 94,993 / 2008-01 / 2026-10** |
| A4 | 逐行可追溯 | 见 3.5(a) | **0** |
| A5 | 选源与重算一致 | 见 3.5(b) | **0** |
| A6 | 双向 EXCEPT | 见 3.3 | **0 / 0** |
| A7 | `prices_monthly` 指纹**不变** | 见附录 B | **`ffd63ae8f4069a1a8b343e0318a3fa65`** / **351,889** |
| A8 | `locked_symbols` 仍 **430** 只、hash 不变 | 见下方 SQL-A8 | **430 / `45642bcb563a3009b9edf9f9a7ec4ff5`** |
| A9 | `members` / `sectors` 不变 | 附录 B | **1,262 / `d04cc808…`**、**513 / `d2813713…`** |
| A10 | 触发器在岗且唯一 | 见 3.8 体检 | **1 行，`tgenabled='O'`** |
| A11 | `locks` 语义未破坏（UQ 存在） | `select count(*) from pg_constraint where conname='locks_kind_scope_from_scope_to_key'` | **1**（否则 `lock_stage.py` 直接崩） |

```sql
-- SQL-A8：locked_symbols 指纹（必须与迁移前逐字节一致）
select count(*) n, md5(string_agg(symbol||'|'||coalesce(hash,''), '|' order by symbol)) h
from locked_symbols;
-- 期望 n=430, h=45642bcb563a3009b9edf9f9a7ec4ff5
```

### B. 权限面（迁移必须"变好"，不能变差）

| # | 判据 | 命令 | 期望 |
|---|---|---|---|
| B1 | `prices_monthly` 不再对 anon 可写 | `select has_table_privilege('anon','prices_monthly','INSERT'), has_table_privilege('anon','prices_monthly','UPDATE'), has_table_privilege('anon','prices_monthly','DELETE')` | **f / f / f**（当前是 t/t/t） |
| B2 | `prices_monthly` RLS 已开 | `select relrowsecurity from pg_class where oid='prices_monthly'::regclass` | **t**（当前 f） |
| B3 | `series_selected` anon 只读可用 | `set role anon; select count(*) from series_selected; reset role;` | **117,113**（不能 0、不能 permission denied） |
| B4 | 新表/视图无多余写权限 | `select has_table_privilege('anon','series_selected','INSERT')` | **f** |
| B5 | REST 兜底仍能拉全表 | `python -c "import db_rest;print(len(db_rest.load_selected()))"` | **848**（当前 848） |

### C. 站点数字（"和迁移前一模一样"）

| # | 判据 | 文件 / 字段 | 期望 |
|---|---|---|---|
| **C0** | **DB 聚合戳（最有价值的一条）** | `docs/data/b/strategy_history.json` → `input_sha`（也出现在 `b/signal.json`） | **`546d20e1666b33a8`** |
| C1 | 累计 | `docs/data/b/strategy_history.json` → `summary.total` | **22.8013** |
| C2 | 年化 / 最深回撤 / 基准 | 同上 → `cagr` / `worst_intra` / `benchmark` | **0.2541 / -0.2173 / 5.9566** |
| C3 | 14 年逐年收益 | 同上 → `years[].ret` | 见基线 JSON（2012..2025，14 条全等） |
| C4 | 14 年持仓 | 同上 → `years[].holdings` | 见基线 JSON（含 2025: NVDA/NFLX/RCL） |
| C5 | 曲线点数 + 末点 | `docs/data/b/curve.json` | **168** 点；末点 `{d:"2025-12", s:23.801255, b:5.430206}`；`stats.total` **22.8013** |
| C6 | 当期持仓 | `docs/data/b/equity.json.position` == `signal.json.official` | **["MU","STX","NEM"]**，month **2026-11** |
| C7 | **静态输入文件 sha256 逐个相等** | `static/members_frozen.csv`、`static/sector_frozen.json`、`static/cloud.json`、`docs/data/cloud.json` | 见附录 B |
| C7b | 产物**按字段**比对（**不是** sha） | `docs/data/b/*.json`、`docs/data/quality.json` | 除 `generated_at` / `run_id` / `updated` / `checked_at` / `date` 外，所有字段逐个相等 |
| C8 | 质量快照 | `docs/data/quality.json` → `total_return_ratio` / `summary` | **0.8111** / `{pass:25,warn:3,fail:0}` |

> **C0 为什么最有价值**：`variant_b._db_sha()` 会把 `count(*)`、`max(fetched_at)`、`count(distinct symbol)`、
> `count(distinct source)`（`prices_monthly`）+ `count(*)`、`sum(adj_close)`、`count(distinct symbol)`
> （`series_selected`）压成 16 位十六进制写进 `input_sha`。**这 7 个聚合值任何一个变了，C0 立刻变**，
> 而且是站点页面自己显示的值，不需要额外工具。迁移前后都必须等于 `546d20e1666b33a8`。
>
> **C7 / C7b 为什么必须分开**：实测 `docs/data/b/strategy_history.json` 里有 `generated_at`、
> `equity.json` 里有 `updated`／`updated_tz`、`quality.json` 里有 `generated_at`、`health.json`/`quotes.json`
> 里有 `checked_at`／`quotes_ts`。**每次构建这些都会变，sha 永远不会相等**——用 sha 当判据会得到"永远失败"。
> 只有 `static/*` 与 `docs/data/cloud.json` 是无时间戳的静态输入，才适用 sha 相等。

### D. 运行时不回归

| # | 判据 | 命令 | 期望 |
|---|---|---|---|
| D1 | 构建仍能跑通 | `python runner.py variant_b.py` | rc=0，无 `db:unavailable` |
| D2 | 选源重建仍能跑通 | `python db_select.py` | 输出行数 = 117,113 |
| D3 | 校验仍能跑通 | `python db_validate.py` | 无 `fail`（warn ≤ 3） |
| D4 | 锁定就绪度不退化 | `python lock_stage.py --check 2010,2025` | 2025-12 标的 **654**（门槛 480） |
| D5 | 独立审计仍通过 | `python runner.py audit_b.py` | `问题 0 项` |
| D6 | 自检仍通过 | `python runner.py selftest.py` | rc=0 |
| D7 | REST 兜底耗时 | `time python -c "import db_rest;db_rest.load_selected()"` | **< 120 s**（118 页 × 3 s 超时上限） |
| D8 | 无锁残留 | `select count(*) from pg_locks l join pg_class c on c.oid=l.relation where c.relname like 'series_selected%'` | **0**（迁移脚本已释放） |
| D9 | `_db_stamp` 变快 | 迁移后跑一次 `load_all()` 计时 | 从 **~1.8 s** 降到 **< 10 ms** |

### E. 观察期（≥ 7 天，跨过 1 次 `collect-full` + 1 次周一 `monthly-pick`）

| # | 判据 | 期望 |
|---|---|---|
| E1 | `validations` 中无新增 `fail` | 0 |
| E2 | `locked_symbols` 仍在增长（说明 `lock_symbols` 正常） | ≥ 430 |
| E3 | 旧表索引扫描归零 | `idx_scan = 0` |
| E4 | 在线 `sys_event` 的 `pipeline_status` 回执及对应 Actions 运行连续 N 次成功 | 无 `degraded` 新增；步骤回执不代替发布验收 |
| E5 | 站点数值零漂移 | C1–C7 再核一次 |

---

## ⑤ 回滚手册

### 5.1 回滚触发条件（任一命中立即回滚）

- A1/A2/A6/A7 任一不符，且无法在 15 分钟内定位
- B3 失败（anon 读 `series_selected` 返回 0 行或报错）→ **CI REST 兜底立即失效，站点会退回旧文件**
- C1/C5/C6 任一数值变化
- `db_validate.py` 出现新的 `fail`
- 迁移窗口内 `pg_stat_activity` 出现 > 30 s 的锁等待

### 5.2 分级回滚

| 级别 | 场景 | 动作 | 恢复时间 |
|---|---|---|---|
| **L0 取消当前步** | 某一批灌数失败 / 抢锁超时 | 什么都不做（`step()` 已 rollback）；修参数重跑 | 秒级 |
| **L1 撤销指针** | 视图已切、校验发现问题 | `drop view series_selected; alter table series_selected_old_20261101 rename to series_selected; notify pgrst,'reload schema';` | **< 1 s** |
| **L2 撤销新表** | 代际表有问题 | L1 + `drop table series_selected_g20261101` | 秒级 |
| **L3 恢复写入方** | 迁移整体放弃 | `cron.alter_job(4/5/6, active:=true)` + `gh workflow enable` ×2 | 分钟级 |
| **L4 灾难（数据被写坏）** | 有人绕过护栏改了 `prices_monthly` | 用 `output/migration_baseline.json` 的 md5 定位；从 `series_selected_g*` 反向重建（因 A4 保证 series 每行可追溯到 prices 同值，反向不完全可逆 → **必须靠迁移前的 `prices_monthly` 物理备份**） | 小时级 |

> **L4 的前提**：迁移开始**前**必须做一次逻辑备份。这个库只有 137 MB，`pg_dump` 完全可行：
> ```bash
> pg_dump "host=aws-0-ap-northeast-1.pooler.supabase.com port=5432 \
>   user=postgres.fnvahsyrzmmxwovbsqpc dbname=postgres sslmode=require" \
>   --no-owner --no-privileges -Fc -f output/pg_backup_premig.dump
> ```
> **用 5432（会话模式）**：`pg_dump` 依赖"单连接 + 导出快照"的语义。6543 事务池会把语句分发到不同后端，
> 快照可能拿不到或不一致（本库未实测 `pg_dump`，按 6543 的池化语义推断；迁移脚本的 COPY/临时表已实测确认 5432 更稳）。
> 备份后**立刻做一次恢复演练**：`pg_restore -l output/pg_backup_premig.dump` 确认可读，再核对 `prices_monthly` 行数 351,889。

### 5.3 回滚后必须复核

```sql
-- 一屏判定是否回到基线
select 'prices_monthly' t, count(*) n, md5(string_agg(h,'|' order by h)) from (
  select symbol||'|'||month||'|'||coalesce(adj_close::text,'')||'|'||coalesce(raw_close::text,'')||
         '|'||coalesce(source,'')||'|'||coalesce(basis,'') h from prices_monthly) z
union all
select 'series_selected', count(*), md5(string_agg(h,'|' order by h)) from (
  select symbol||'|'||month||'|'||coalesce(adj_close::text,'')||'|'||coalesce(raw_close::text,'')||
         '|'||coalesce(source,'')||'|'||coalesce(basis,'') h from series_selected) z;
-- 期望：
--   prices_monthly  | 351889 | ffd63ae8f4069a1a8b343e0318a3fa65
--   series_selected | 117113 | 86369418dd12a88b4bf1b7ddec896bc8
```
再跑 `python -c "import json;print(open('output/migration_baseline.json').read())"` 对照 C 组数值。

---

## 附录 A — 连接/超时/锁 实测矩阵

| 项 | 6543（事务池） | 5432（会话模式） |
|---|---|---|
| `show statement_timeout` | `2min` | `2min` |
| `set statement_timeout='600s'` 后 `show` | `10min` ✅（同连接生效） | `15min` ✅ |
| 显式事务内 `set local` | ✅ 生效，rollback 后恢复 | ✅ |
| `COPY … FROM STDIN`（autocommit） | ⚠️ **成功并立即提交，无法回滚** | 同左 |
| `COPY`（`BEGIN;…;ROLLBACK`） | ✅ 成功 | ✅ 成功 |
| `create temp table` | 未测（有 `pg_temp_*` 残留 schema） | ✅ |
| `pg_backend_pid()` 连续 3 次 | 相同（369,990） | 相同（369,984） |
| 空闲 20 s 后继续 | ❌ `SSL SYSCALL error: EOF detected` | — |
| 建议用途 | 应用运行时 | **迁移 / `pg_dump` / 游标 / COPY** |

| 锁实验 | 结果 |
|---|---|
| 有未提交 `select count(*)` 时 `ADD COLUMN` | ❌ `canceling statement due to lock timeout`（2,069 ms，`lock_timeout='2s'`） |
| 有未提交行锁时 `ADD COLUMN` | ❌ 同上（2,065 ms） |
| `DISABLE TRIGGER trg_guard_months`（表 owner） | ✅ 允许 |
| `DISABLE TRIGGER USER` / `ALL` | ✅ 允许（注意 `ALL` 文档要求 superuser，本库实测通过） |
| 视图改名 `ALTER TABLE … RENAME` | ✅ 103 ms |
| 对视图 `has_table_privilege('anon',…,'SELECT')` | ✅ `true`（`pg_default_acl` 自动授权） |
| 迁移用 `postgres` 角色 `usesuper` | ❌ `false` → `session_replication_role` 不可用 |

---

## 附录 B — 迁移前基线（`output/migration_baseline.json` 摘要）

**数据库指纹**

| 对象 | 行数 | md5 |
|---|---:|---|
| `prices_monthly` | 351,889 | `ffd63ae8f4069a1a8b343e0318a3fa65` |
| `series_selected` | 117,113 | `86369418dd12a88b4bf1b7ddec896bc8` |
| `members` | 1,262 | `d04cc8086a69abcd8817a8788c336657` |
| `sectors` | 513 | `d2813713baa4c1c07ee279d397417c6c` |
| `locked_symbols` | 430 | `45642bcb563a3009b9edf9f9a7ec4ff5` |
| `dataset_rows` | 347,833 | `581d0154fd749a80b3c2e18a2e8565de`（⚠ 已与 series 漂移） |

**不变式**

```
check_selection_mismatch = 0        # 表里的选源 == 现场重算
check_series_orphan      = 0        # series 每行都能在 prices 找到同源同值
check_dataset_drift      = {1: 206126, 2: 7759}   # ← 已经是坏的，不能当基线
dec2025_symbols          = 654      # lock_stage 门槛 480 ✔
series_basis             = 117113 行 / 94,993 全收益 (81.11%)
prices_basis             = 351,889 行 / 95,336 全收益 (27.09%)
```

**页面数字**

```
summary    total 22.8013 | cagr 0.2541 | worst_intra -0.2173 | benchmark 5.9566
years      14 条（2012..2025），逐年收益与持仓见 JSON
curve      168 点，末点 {d:"2025-12", s:23.801255, b:5.430206}
当前信号   month 2026-11，持仓 ["MU","STX","NEM"]（equity 与 signal 一致）
quality    total_return_ratio 0.8111, degraded true, 25 pass / 3 warn / 0 fail
```

**产物 sha256（节选，全部 21 个见 JSON）**

```
docs/data/b/strategy_history.json  8297f481d0756f81d773155be2ccd3c7a4d95348f25d6fdd561f910d3fc7efbc
docs/data/b/curve.json             9695b08610b0ebfa611a846cf0590ca6729c8f76655cf770b26a141be1226ed2
docs/data/b/equity.json            b4266d31b7e06d7633a2c2b4050963ef6771fd4d2e8b26e876d03911b789bfc1
docs/data/b/signal.json            ac99e7722f3f2c62f0d9d8a2decf2893ded4ba7899a0ef6d511b7e412a9a6582
docs/data/strategy_history.json    800a5b60ad0fd3bb279c533730e6f214223a711dc221f230a5a44dee89f65f90
static/members_frozen.csv          c9da7210aea687d4b89f370d024ba1ba8ce80e7c7761b1373d2976b84502a939
static/sector_frozen.json          84135bafb5aba1812e59cd8bba23ed0008159b78a7f5b2f1164473f311fc596e
```

---

## 附录 C — 与本次审计无关、但必须一起修的 P0（不修就不算迁移成功）

1. **`prices_monthly` 对全网可写**（RLS 关 + anon 有 I/U/D）。任何拿到站点 `cloud.json` 里 publishable key 的人都能改行情表。
2. **`data_sources.credential` 明文存 API key**（含 Alpha Vantage / EODHD / Kaggle 三套真实凭据），且 `data_sources` 的 RLS 开了但**0 策略**——目前靠"无策略=拒绝"兜住，一旦有人补一条 `public_read` 就全泄。建议改存 `vault`（`supabase_vault` 扩展已装）。
3. **`db_hardening.py` 从未落地**（`trg_locks_append`、`trg_guard_truncate`、`revoke … from anon` 全都不存在）。
4. **`db_select.py` 的非原子 drop→rename**（本方案 Phase 4/5 直接消灭）。
5. **`db_dataset.build` 的 CASCADE 删除**（`delete from datasets` 连带清空 229k 行 `dataset_rows`）。
6. **死索引**：`idx_staging_pending`（0 次扫描）、`idx_prices_basis`（25 次）、`series_selected` 上重复的 `idx_series_month_new`。
