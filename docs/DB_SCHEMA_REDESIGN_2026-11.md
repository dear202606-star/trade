# Supabase/Postgres 17 数据模型重构设计（只读审计 + 目标 schema + 分阶段迁移）

> 审计对象：`fnvahsyrzmmxwovbsqpc`（ap-northeast-1，PG 17，17 张表）
> 审计时间：2026-11-01
> 审计方式：**只读**。未修改仓库任何既有文件，未在生产库执行任何 DDL。
> 为使论断可被复核，做了 3 类最小侵入实验，全部已回滚/已清理：
> ① 用公开 anon key 插 1 行 `__AUDIT_PROBE__` 到 `prices_monthly` 后立即删除（行数已复原 351,889）；
> ② 对 13 张表各插 1 行探测行再删（除 `prices_monthly` 外全部被 RLS 拒绝，无一残留）；
> ③ 全程未做 UPDATE 既有行、未 TRUNCATE、未建临时 schema（本机到 5432/6543 不可达，详见 §1.2）。

---

## 0. 结论摘要（先看这 8 条）

| # | 结论 | 依据 | 动作 |
|---|---|---|---|
| **C1** | **`prices_monthly` 可被公开 anon key 直接 INSERT/DELETE**（RLS 策略写成了 `for all`/缺 `for select` 语义），"历史不可篡改"目前只靠行级触发器兜住"已锁月份"，未锁月份可被任意重写 | 实测 INSERT 201 → 回读 200 → DELETE 204，行数复原 | **P0 立刻修**（§7 P0，30 分钟，可秒级回滚） |
| **C2** | **标的级锁把"当年热层"一起冻死了**：`locked_symbols` 命中即拦截该标的**所有月份**的写入。430 只已锁标的覆盖了当年 13,186 行中的 8,640 行（65%），锁定后这些标的的 2026 月线**一次都没更新过** | 锁时间 00:51–00:53Z；2026 行 `fetched_at > locked_at` 计数 = **0** | **P0** 改触发器语义：锁必须带 `month_from/month_to` 范围（§7 P1） |
| **C3** | **锁定承诺对"站点实际读的那张表"不成立**：`series_selected` 是每 6 小时 `DROP TABLE` + `RENAME` 重建的物化副本，既无锁也无保护；而站点导出、`variant_b`、`db_validate` 全部读它 | `db_select.build_table()`；`collect-full.yml` 每 6 小时 | 改为**视图/物化视图**（§7 P4），从根上消除副本 |
| **C4** | 两套锁、两张源表、三层价格副本：`locks`(0 行，从未用) 与 `locked_symbols`(430)；`source_registry`(44) 与 `data_sources`(3，凭据)；`prices_monthly`(351,889) → `series_selected`(117,113) → `dataset_rows`(347,833) | 见 §3 实测 | 合并锁→`data_lock`；合并源→`data_source`；副本降级为 view/snapshot |
| **C5** | **`locks` 的 append-only 触发器与 `lock_stage.lock()` 的 upsert 逻辑互斥**：`locks` 一旦有行，第二次 `--lock` 必然抛 `locks is append-only`。现在没炸只因为 `locks` 还是空表 | `db_hardening.py` vs `lock_stage.py` 第 148 行 | 改"作废-新建"版本链（§6.5） |
| **C6** | **审计层"有表无链"**：305 条 `validations` 中 304 条 `dataset_id is null`；无 `run_id`、无被校验数据的指纹 → 无法回答"这条 pass 是通过哪一版数据"。`fetch_runs` 的 `requested`/`ok` 单位混用（标的 vs 行数） | 实测 ST 段 | 加 `run_id` + `fingerprint` + 单位拆分（§6.6） |
| **C7** | **`corporate_actions` 空表，而全收益的原料正在被下载后丢弃**：`relay_source.fetch_yahoo_relay()` 明确请求 `events=div,split`，解析时只取 `adjclose/close`，`d["events"]` 直接扔掉 | `relay_source.py` L208–L232 | 落 `corporate_action` → 反推 `adj_factor`，让"全收益"可证（§6.3） |
| **C8** | **不建议分区**（当前 35 万行，估算 ~30–55 MB 堆 + ~25 MB 索引）。分区的真正价值是把"冻结"变成 `REVOKE` 分区写权限的 O(0) 操作，但按当前规模用触发器 + `data_lock` 已足够；触发分区的量化阈值见 §11 | 容量估算 + 访问模式 | 只留 `month_start` 生成列作为**未来**分区锚点 |

---

## 1. 审计方法与证据等级

### 1.1 三级证据

| 等级 | 含义 | 本文用法 |
|---|---|---|
| **E1 实测** | 本次直接对生产库执行只读查询/REST 采样得到 | 行数、分布、锁定时间、写权限探测、2026 热层更新情况 |
| **E2 代码对账** | 线上表结构无法直连读取（见 §1.2），用仓库内 DDL + 线上"列数/索引数/触发器数"交叉验证 | 17/17 张表的列数、索引数与仓库 DDL **完全吻合**（`prices_monthly` 4 索引 = PK+3；`series_selected` 2；`locks` 2；`datasets` 2；其余 1 或 2），据此认定**仓库 DDL 即线上 DDL** |
| **E3 推断** | 由代码/行为反推，需在生产上复核（本文标注"待复核"并给出验证 SQL） | 如 `prices_monthly` 的策略具体写成什么、`trg_guard_truncate` 是否存在 |

### 1.2 本次无法做的两件事（以及替代做法）

1. **直连 Postgres 不可达**：本机到 `aws-0-ap-northeast-1.pooler.supabase.com:5432/6543` 与 `db.<ref>.supabase.co:5432` 均被对端关闭（TCP 通、TLS 握手被拒；443 正常）。因此 `pg_class`/`pg_policies`/`pg_stat_user_indexes`/`EXPLAIN` 一类系统目录查询**没跑成**。
   替代：REST(anon) 精确行数 + 逐表写权限探测 + 仓库 DDL 交叉验列数/索引数。
2. **无法读 `dataset_rows` 等 3 张表**：RLS 已启用但**没有 anon 的 SELECT 策略**，PostgREST 返回 0 行。这本身是发现（见 P1-06）。

> ⚠️ 迁移执行时必须在**直连/会话池（5432）**或 Supabase SQL Editor 里跑，不要在 6543 事务池里跑 DDL：`SET LOCAL` 不跨语句、`CREATE INDEX CONCURRENTLY` 不能进事务块、advisory lock 行为不同。

---

## 2. ①现状问题表

### 2.1 逐表评估

| 表 | 列/索引/触发器 | 行数(E1) | 判定 | 关键问题 | 严重度 |
|---|---|---|---|---|---|
| `prices_monthly` | 8/4/1 | 351,889 | **保留，重构** | 见 2.2 清单：无 FK、无 CHECK、2 个废索引、`source` 无外键、`month` 是 text、缺 `fetch_run_id`/`adj_factor`；**anon 可写** | **P0** |
| `series_selected` | 6/2/0 | 117,113 | **降级为视图** | 是 `prices_monthly` 的派生副本（117k 行 = 33% 冗余），却**无锁、无 quality、无 run_id**；用 `DROP+RENAME` 重建（非事务），且**锁不覆盖它** | **P0** |
| `dataset_rows` | 8/1/1(guard) | 347,833 | **保留，限用途** | 229,114 行属于 `baseline_v1`（status=draft，永不锁定）→ 死重量；守卫触发器**从未生效**（无任何 dataset 处于 locked）；anon 读不到 → 审计 SQL 在 CI 里跑不了 | P1 |
| `datasets` | 10/2/0 | 2 | **保留，补列** | `months` 列实际存**行数**、`symbols` 存文件数；`status` 永远停在 draft；无 `row_count/definition/parent_id/locked_by`；无状态机约束 | P1 |
| `locks` | 11/2/0 | **0** | **合并进 `data_lock`** | 从未启用；`monthly_only` 死列；与 append-only 触发器冲突（C5）；`scope_from` 曾被忽略（`lock_stage.py` 第 38 行只取 `max(scope_to)`） | **P0** |
| `locked_symbols` | 5/1/0 | 430 | **合并进 `data_lock`** | 只有标的、**没有月份范围** → 冻死热层（C2）；`hash` 是"sha256 前 16 位"，与 `datasets.input_hash`(md5)、`locks.hash`(md5) 三种算法并存且**都不可独立复算** | **P0** |
| `members` | 4/1/0 | 1,262 / 1,209 标的 | **保留，补约束** | 无 FK→`symbol`；`2099-12-31` 哨兵代替 NULL（503 只在册）；缺 `as_of`（G5 要求的快照日）；PK(symbol,start_date) 合理 | P1 |
| `sectors` | 4/1/0 | 513 | **并入 `symbol`** | 只有当前快照（无 PIT），覆盖 **42.4%** 的 1,209 只成分股（696 只无行业），而 60% 行业上限依赖它；与 `symbols.gics_sector` 重复 | P1 |
| `symbols` | 8/1/0 | **0** | **启用为维度表** | 空表但定义了 `gics_sector`（与 sectors 重复）、`first_month/last_month`（可由事实表派生 → 必然过期）；无任何 FK 指向它 | P1 |
| `corporate_actions` | 5/1/0 | **0** | **保留并落数据** | 空表；缺 `adj_factor`/`source_id`/`verified_at`；全收益口径无法复算（C7） | **P0**（业务上） |
| `fetch_runs` | 8/1/0 | 11 | **保留，补列** | `requested`=标的数、`ok`=**行数**（relay: requested=186, ok=17,317）→ 成功率不可计算；`note` 里塞 JSON 且**被截断到 300 字符** → 不可解析；无 `run_id/status/duration_ms` | P1 |
| `validations` | 6/1/0 | 305 | **保留，补列** | 304/305 的 `dataset_id` 为 NULL；`check_name` 自由文本（47 个取值，无字典表）；无 `run_id`、无被校验数据指纹；`status` 无 CHECK | P1 |
| `source_registry` | 18/2/0 | 44 | **合并为 `data_source`** | 把"价格源/成分源/校验源/付费排除"混在一张表（`kind` 自由文本）；10 个可用价格源 + 30 个 excluded；**无 FK 从 `prices_monthly.source` 指过来** | P1 |
| `data_sources` | 9/2/0 | 3（RLS 屏蔽） | **拆为 `data_source_secret`** | 与 `source_registry` 同一主键语义（source_id）却互不引用；凭据是**明文 jsonb**；RLS 无策略（正向），但无 Vault、无轮换记录 | P1 |
| `ingest_staging` | 11/2/0 | 0 | **保留，修函数** | `promote_staging()` 是 `security definer` 且**没有 `set search_path`**；第 3 步 `update ... where (symbol,month,source) in (select ... from prices_monthly)` 会**误标**任何"键恰好在正式表里存在"的行，并把已存在键的行反复全表扫描（O(n²)）；无唯一键 → 可重复塞同键 | **P0**（安全）/P1 |
| `holdings` | 3/1/1 | 0 | **移出仓库 schema** | 站点用户笔记云同步（浏览器端加密 payload），与行情仓库无关；被授予 anon `insert,update,select`，是公开可写表（滥用/存储 DoS 面） | P2 |
| `work_plans` | 3/1/0 | 2（RLS 屏蔽） | **保留，加保留期** | 每天整份计划 JSON 追加一行，无去重、无 TTL、无读取者；RLS 无策略 → CI 里读不到 | P2 |

### 2.2 跨表问题（比单表更致命）

| # | 问题 | 证据 | 后果 | 级别 |
|---|---|---|---|---|
| **P0-01** | **匿名可写事实表** | anon POST `prices_monthly` → 201；GET 可回读；DELETE → 204 | 公开 key 就在 `docs/cloud.json`（随站点发布）→ 任何人可改未锁月份、可删行。RLS 对其他 12 张表都拦住了（42501），只有这张表漏 | **P0** |
| **P0-02** | **锁粒度错误：标的锁 = 永久全区间锁** | `locked_symbols` 命中即抛，不判断月份；2026 年 8,640 行锁定后 0 次更新 | 当年层停更；`relay_source`/`staging` 写入被拒 → 采集"失败但不报错"（`log_run` 记 failed） | **P0** |
| **P0-03** | **锁保护错了对象** | 触发器只挂在 `prices_monthly`；站点读 `series_selected`（6 小时重建一次） | 锁定后仍可通过"重跑选源/改选源"改变已发布序列 → **换源造假涨幅**这个原始风险没被堵住 | **P0** |
| **P0-04** | `locks` append-only ↔ `lock_stage` upsert 互斥 | `db_hardening.py` 建 `trg_locks_append`；`lock_stage.py` 用 `on conflict do update` | 第一次锁成功、第二次改锁必失败；运维会以为"锁坏了" | **P0**（潜在） |
| **P0-05** | `series_selected` 重建非原子 | `db_select.build_table()` 用 autocommit：`drop table` → `alter rename` 两条独立事务 | 连接在两条之间断（池化器常态）→ **导出表消失**，`db_rest` 404，整条流水线红 | **P0** |
| **P0-06** | `promote_staging()` 无 `search_path` + 误标 | `security definer` 未 `set search_path`；`update ... in (select ... from prices_monthly)` | 函数在超级用户下执行 → search_path 劫持可提权；误标导致"已合并"假象，行**永久滞留** | **P0** |
| P1-01 | 数据集锁是死代码 | 2 个 dataset 全 `draft`；`guard_locked_dataset` 只在 `status='locked'` 触发 | "数据集不可变"从未被验证过 | P1 |
| P1-02 | 审计结论未绑定数据版本 | 304/305 `dataset_id is null`；无指纹 | 无法回答"这版数字过了哪些检查" | P1 |
| P1-03 | 源优先级有 **5 份**副本 | `db_prices.PRIORITY`、`db_select.BUILD` 的 CASE、`relay_source._DEFAULT`、`db_sources.SEED`、`static/source_matrix.json` | 改一处不生效；"唯一真相=source_registry"被代码架空 | P1 |
| P1-04 | 核心表零外键/零 CHECK | 17 张表只有 2 个 FK（都是 `→datasets`）；`month` 无格式约束、`basis/quality/status` 无枚举 | 只能靠 Python 保证，`staging` 与 REST 直写可绕开 | P1 |
| P1-05 | `source` 是自由文本且出现"复合源"值 | `'sina_repaired+eodhd'` 205 行（价格）/0 行（选源）；7 个取值在 `source_registry` 里找不到 | 无法 FK、无法统计"某源贡献"、语义是"逐月换源"的化石 | P1 |
| P1-06 | 3 张表 anon 读不到（RLS 无策略） | `dataset_rows`/`data_sources`/`work_plans` REST 返回 0 行（`data_sources` 真实 3 行、`work_plans` 2 行） | CI 用 anon → `dataset_rows` 的可追溯性校验只能在 service 角色下跑；`work_plans` 写了没人读得到 | P1 |
| P1-07 | 三种哈希不可复算 | `datasets.input_hash`=md5(...)；`locks.hash`=md5(string_agg)；`locked_symbols.hash`=sha256[:16]（Python 里 `%.10f` 格式化） | 三者无法互相校验；换实现即失效 | P1 |
| P1-08 | 文档与现实脱节 | `docs/DATABASE.md` 描述的是 `instruments/memberships/picks_yearly/equity_history/signal_current/run_log` 等 **14 张线上不存在的表** | 新人按文档开发必然踩空；审计时无法确认"哪个才是设计意图" | P1 |
| P2-01 | 冗余/无用索引 | `idx_prices_symbol` 被 PK `(symbol,month,source)` 前缀完全覆盖；`idx_prices_basis` 只有 2 个取值（27%/73%） | 每次 upsert 多维护 2 个索引（~30% 写放大）、多占 ~20 MB | P2 |
| P2-02 | `month` 用 text 且 1972 起有数据 | 范围 1972-08 → 2026-10；业务只用 2008+ | 无日期语义、无法分区、无法用日期函数 | P2 |
| P2-03 | 命名/语义漂移 | `datasets.months`=行数、`datasets.symbols`=文件数、`locks.rows`、`fetch_runs.ok`=行数、`locks.monthly_only` 死列 | 误读风险 | P2 |
| P2-04 | `series_selected` 缺 `quality`/`fetched_at` | 选源后丢失质量标记 | 下游看不到 suspect 行 | P2 |

---

## 3. 实测事实附录（供复核）

```
prices_monthly   351,889 行  basis: total_return 95,336 (27.1%) / price_only 256,553 (72.9%)
                 源: sina_repaired 227,565 | yahoo_relay 89,915 | hf_ayushkhaire 27,440
                     stockanalysis 2,763 | eodhd 2,514 | alphavantage 1,487 | sina_repaired+eodhd 205
                 quality: ok 350,342 / suspect 1,343（其余 204 行在两次采样间被 CI 写入，属漂移）
                 月份 1972-08 → 2026-10；2026 年起 13,186 行 / 661 只
series_selected  117,113 行  basis: total_return 94,993 (81.1%) / price_only 22,120 (18.9%)
                 源: yahoo_relay 89,909 | sina_repaired 22,120 | stockanalysis 2,763 | eodhd 2,201 | alphavantage 120
dataset_rows     347,833 = baseline_v1 229,114 + b_2026_10_31 118,719（后者与 series_selected 近似 1:1 复制）
datasets         1 baseline_v1(840 只/draft/hash 843f661e) 2 b_2026_10_31(847 只/draft/hash 08df22e1...)
locked_symbols   430 只，locked_at 2026-11-01T00:51:22Z ~ 00:53:42Z，months 160~168
                 → 当年 13,186 行中 8,640 行属已锁标的；fetched_at > locked_at 的行数 = 0
locks            0 行（从未启用）
members          1,262 行 / 1,209 只；503 只在册（end_date=2099-12-31）；end<start 0 例
sectors          513 行 / 11 个行业；覆盖 42.4% 成分（696 只无行业）
validations      305 行（pass 225 / warn 78 / fail 2）；dataset_id is null 304；47 个 check_name
fetch_runs       11 行；requested/ok 单位混用（186/17,317）
source_registry  44 行：price 可用 10 + 被排除 30 + membership/sector/validation 4
data_sources     3 行（RLS 屏蔽，anon 不可见）
```

---

## 4. 合并 vs 拆分的判据（不是结论，是尺子）

任何"要不要合并"的争论都应落到这 6 条上，逐条打分。**同一张表必须同时满足"生命周期一致 + 写者一致 + 粒度一致"才允许合并**。

| 判据 | 关键问句 | 反例（不可合并） | 正例（应合并） |
|---|---|---|---|
| **J1 写放大** | 一个逻辑事实会被写几次？合并后每次写要touch多少行？ | 把"选源结果"做成 `prices_monthly.is_selected` 列 → 每次换源要 UPDATE 该标的全部月份（~140 行/只，且撞锁） | 两把锁合并：锁这件事每次只写 1 行 |
| **J2 读路径** | 热点查询要几次 join？能不能走一个索引？ | 把 `sectors` 拆成 `sector` + `sector_source` 两张表 → 每次选股多 1 次 join，而它只有 11 个取值 | `sectors` 并入 `symbol`（1:1，主键相同） |
| **J3 生命周期 / 锁粒度** | 这些行的"可变性"是否一致？能否一起冻结？ | `prices_monthly`(可写到冻结日) 与 `dataset_rows`(写一次永不变) 生命周期不同 → 合并会让每次价格修正都变成"改写审计快照" | `locks` + `locked_symbols`：都是"冻结声明"，同一时刻冻结、同一时刻作废 |
| **J4 审计需求** | 上游被修正后，历史结论还必须成立吗？ | `dataset_rows` 必须能证明"当初发布的就是这些数" → 不能改成视图 | `series_selected` 不需要独立留痕（它必须恒等于"按规则从 prices 选出来的结果"），做成视图反而**消除了漂移** |
| **J5 基数/关系** | 1:1 / 1:N / N:M？ | `members`(1:N 区间) 与 `sector`(1:1) 关系不同，不能同表 | `data_sources.credential`(1:1) 与 `source_registry`(1:1 同主键) → 同表（但见 J6） |
| **J6 权限边界** | 两张表的读者/授权是否不同？ | 凭据表必须"只有 service 能读"，能力矩阵要"匿名可读" → **同一张表做不到列级 RLS**，必须拆 | —— |

### 4.1 逐项裁决

| 争议 | 裁决 | 判据 |
|---|---|---|
| `prices_monthly` / `series_selected` / `dataset_rows` 三层价格能否合成一张？ | **不能全合**：prices=可变事实（J3 不一致）；dataset_rows=写一次快照（J4 要求独立）；series_selected=纯派生 → **降级为视图** | J3+J4；J1 反证：把 is_selected 做成列会让换源变成 O(全部月份) 的 UPDATE，且与冻结锁冲突 |
| 能否用"一张 prices 表 + `is_selected`/`pick_rank` 多字段"替掉 `series_selected`？ | **不能**。选源是**跨行的批决策**（比较同一 symbol 的多个 source），把它做成行属性 → ① 采集写入时必须知道选源规则（把批处理耦合进逐行写路径）；② 换源要 UPDATE 大量已冻结行；③ 派生状态会静默漂移 | J1 + J3 |
| `locks` 与 `locked_symbols` | **合并为 `data_lock`**（scope 维度：month_range / symbol / symbol_month），旧名保留为视图 | J3 + J1 |
| `source_registry` 与 `data_sources` | **合并元数据 + 拆出凭据**：`data_source`（匿名可读）+ `data_source_secret`（RLS 无策略，或 Vault） | J5 正例 + J6 反例 |
| `sectors` 与 `symbols` | **合并**到 `symbol`（`sector`, `sector_asof`, `sector_source`），`sectors` 保留为兼容视图 | J2 + J5 |
| `members` 与 `symbols` | **不合并**：`members` 是 1:N 区间（1,209 只 / 1,262 段），`symbol` 是 1:1 维度 | J5 |
| `validations` 与 `fetch_runs` | **不合并**：粒度不同（run 1:N check）；用 `run_id` FK 连接 | J5 |
| `work_plans` 与 `fetch_runs` | **不合并**：计划是"意图"，台账是"事实" | J3 |
| `holdings` 与行情仓库 | **移出 `public`** → `app.notebook` | J6（匿名可写）+ 领域不同 |

---

## 5. ②目标 ER

```
                          ┌──────────────────────────┐
                          │ data_source (44)         │  源注册表（唯一调度真相）
                          │ source_id PK             │  priority/basis/status/role/quota
                          └────┬──────────────┬──────┘
                               │1:1           │1:N
                    ┌──────────▼─────┐   ┌────▼──────────────────────────┐
                    │data_source_    │   │ corporate_action (0→N)        │  拆股/分红/合并
                    │secret (3)      │   │ (symbol,ex_date,kind,source)  │  → adj_factor 可复算
                    │RLS 无策略/Vault│   └────┬──────────────────────────┘
                    └────────────────┘        │N:1
                                              │
  ┌───────────────┐  1:N   ┌─────────────┐    │      ┌────────────────────────────┐
  │ symbol (~1.2k)│◄───────┤ membership  │    └─────►│ prices_monthly (351,889)   │
  │ symbol PK     │        │ (1,262)     │           │ PK(symbol,month,source)    │
  │ sector*       │        │ (symbol,    │           │ + month_start(date,生成列) │
  │ is_delisted   │        │  start_date)│           │ + fetch_run_id  FK         │
  └───┬───────────┘        └─────────────┘           │ + quality/basis CHECK      │
      │1:1                                          └──────┬───────────┬─────────┘
      │                                                      │1:N        │N:1
  ┌───▼─────────────┐        ┌──────────────────┐            │           │
  │ series_pick(847)│        │ data_lock        │◄───────────┘           │
  │ symbol PK       │        │ (合并两把锁)      │  守卫读它              │
  │ source_id FK    │        │ scope/symbol/    │                        │
  │ basis/from_month│        │ month_from/to    │                        │
  └───┬─────────────┘        └──────────────────┘                        │
      │1:N                                                               │
  ┌───▼──────────────────────────┐         ┌───────────────────────────┐ │
  │ series_selected  【VIEW】    │         │ datasets (2)              │ │
  │ = prices ⋈ series_pick       │         │ + row_count/definition/   │ │
  │   (锁定后自然只读)            │         │   parent_id/status 状态机 │ │
  └──────────────────────────────┘         └──────────┬────────────────┘ │
                                                      │1:N               │
  ┌──────────────────────┐   ┌──────────────────┐     │                  │
  │ validation_check     │◄──┤ validations(305) │     │                  │
  │ (字典，47+ 键)        │   │ + run_id/subject │     │                  │
  └──────────────────────┘   └────────┬─────────┘     │                  │
                                      │N:1            │                  │
  ┌───────────────────────────────────▼────────────┐  │  ┌───────────────▼────────┐
  │ fetch_runs (11)                                │  │  │ dataset_rows (347,833) │
  │ + run_id uuid / status / symbols_* / rows_*    │  └─►│ 仅"锁定时的快照"        │
  └────────────────────────────────────────────────┘     │ + row_hash / snapshot_at│
                                                          └────────────────────────┘
  ┌──────────────────────┐        ┌──────────────────────┐
  │ ingest_staging (0)   │        │ ingest_plan (2)      │      ┌──────────────────┐
  │ + batch_id/claimed_at│        │ = work_plans         │      │ app.notebook (0) │
  │ + 唯一键/claim 语义   │        │ + plan_date UNIQUE   │      │ = holdings 迁出  │
  └──────────────────────┘        └──────────────────────┘      └──────────────────┘
```

**表数变化**：17 张表 → **15 张表 + 6 个兼容视图 + 1 个可选物化视图**（净减 2 张，但删掉 1 张 11.7 万行副本、1 个死锁表、1 个死维度表的沉余；新增 2 张：`series_pick`、`validation_check`）。

| 保留（改列） | 合并/迁移 | 新增 | 降级为视图 | 移出 |
|---|---|---|---|---|
| prices_monthly, datasets, dataset_rows, members, validations, fetch_runs, source_registry→data_source, ingest_staging, work_plans→ingest_plan, symbols→symbol | locks + locked_symbols → **data_lock**；sectors → **symbol.sector**；data_sources.credential → **data_source_secret** | **series_pick**, **validation_check**, data_lock, data_source_secret | series_selected, source_registry(兼容), data_sources(兼容), sectors(兼容), locks(兼容), locked_symbols(兼容), symbols(兼容) | holdings → **app.notebook** |

---

## 6. ③目标 DDL

### 6.0 三条硬约定（先立规矩，再改表）

| 约定 | 内容 | 理由 |
|---|---|---|
| **D1 不改表名** | `prices_monthly` / `datasets` / `dataset_rows` / `members` / `sectors` / `validations` / `fetch_runs` / `source_registry` / `data_sources` / `ingest_staging` / `work_plans` / `symbols` / `locks` / `locked_symbols` 这些**旧名一律保留为视图或实体**，新名只用于新增表 | 17 张表被 20 个 Python 模块硬编码；改名是纯 churn，且每次改名都要与 2 小时一次的 CI 抢窗口。ER 图中的 `ingest_plan` **即物理表 `work_plans`**，不新增改名 |
| **D2 只加列，不改类型** | `month` 永远是 `text`；需要日期语义就加**生成列** `month_start` | `ALTER TYPE` 会重写全表 + 排他锁；6 个模块和 PostgREST 过滤器都按 text 比较 |
| **D3 每个 DDL 必须可重入** | 全部 `if not exists` / `drop ... if exists` / `not valid` 两步走 | CI 不停机，迁移可能被中断重跑 |
| **D4 holdings 暂不迁出 public** | 见 §10-D6 | PostgREST 只暴露 `public`；迁 schema 会**直接断掉站点云同步** |

执行前置（在**直连 5432 / SQL Editor** 里跑，不要在 6543 事务池）：

```sql
-- M0 迁移账本 + 权限基线（一次性，5 分钟，无表锁）
create table if not exists public.schema_migration (
  id          bigint generated always as identity primary key,
  version     text not null unique,          -- '20261101_01_prices_month_start'
  applied_at  timestamptz not null default now(),
  applied_by  text not null default current_user,
  checksum    text,                          -- md5(脚本内容)
  note        text
);

-- 会话级安全阀：任何 DDL 抢不到锁就放弃，绝不阻塞 CI
set lock_timeout = '3s';
set statement_timeout = '30s';

-- 1) 未来新建的表默认不给 anon 写权限（不追溯）
alter default privileges for role postgres in schema public
  revoke insert, update, delete, truncate on tables from anon, authenticated;

-- 2) 现状纠偏：先把匿名写权限收干净（幂等）
revoke insert, update, delete, truncate on all tables in schema public from anon, authenticated;
grant select on all tables in schema public to anon, authenticated;
-- holdings 是唯一例外（站点云同步），见 §10-D6
grant insert, update, select on public.holdings to anon, authenticated;
```

### 6.1 M1 — 源注册表合并（`source_registry` + `data_sources` → `data_source` + `data_source_secret`）

```sql
-- 6.1.1 元数据表（1:1 合并，主键不变）
create table if not exists public.data_source (
  source_id        text primary key,
  display          text,
  kind             text check (kind in ('price','membership','sector','corporate_action','validation','bulk')),
  priority         int  check (priority is null or priority > 0),   -- 数字越小越先调度
  basis            text check (basis in ('total_return','price_only','returns','n/a','unknown')),
  delisted         text,
  depth            text,
  frequency        text,
  quota_per_day    int  check (quota_per_day is null or quota_per_day >= 0),
  free             boolean not null default true,
  status           text not null default 'untested'
                   check (status in ('ok','partial','blocked','untested','dead','excluded_paid')),
  role             text,
  access           text,
  tested           text,
  last_ok          timestamptz,
  note             text,
  credential_state text default 'none' check (credential_state in ('none','present','expired','revoked')),
  credential_fingerprint text,          -- sha256 前 12 位，可核对不可反推
  vault_secret_name text,               -- 有 Vault 时用它；没有则用 data_source_secret
  updated_at       timestamptz not null default now()
);
comment on table public.data_source is
  '数据源能力矩阵 + 调度序（唯一真相）。凭据不在此表：见 data_source_secret / Supabase Vault。';

-- 6.1.2 凭据表（1:1，RLS 开且不建任何策略 → 只有表 owner/service 能碰）
create table if not exists public.data_source_secret (
  source_id   text primary key references public.data_source(source_id) on delete cascade,
  credential  jsonb not null,           -- 过渡期；目标态迁 Vault，此列置空
  fingerprint text not null,
  added_at    timestamptz not null default now(),
  rotated_at  timestamptz,
  note        text
);
alter table public.data_source_secret enable row level security;
revoke all on public.data_source_secret from anon, authenticated;

-- 6.1.3 搬迁（44 行 + 3 行，秒级）
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

-- 价格源必须齐（含历史"复合源"值，否则下面的 FK 无法验证）
insert into public.data_source(source_id, display, kind, basis, status, note, free)
values ('sina_repaired','新浪（本地修复）','price','price_only','ok','历史复合源前缀',true),
       ('sina_repaired+eodhd','新浪+EODHD 拼接（历史遗留）','price','price_only','dead',
        '逐月换源化石：禁止新增，仅保留历史 205 行',true),
       ('yahoo_relay','Yahoo（中转）','price','total_return','ok',null,true),
       ('hf_amirtrader','HF AmirTrader','price','total_return','ok',null,true)
on conflict (source_id) do nothing;

insert into public.data_source_secret(source_id, credential, fingerprint, added_at, note)
select source_id, credential, coalesce(fingerprint,''), added_at, note
from public.data_sources
on conflict (source_id) do nothing;

-- 6.1.4 旧表改名 → 兼容视图（同一事务内，ACCESS EXCLUSIVE 仅毫秒）
alter table public.source_registry rename to source_registry_legacy;
alter table public.data_sources    rename to data_sources_legacy;

create or replace view public.source_registry with (security_invoker = true) as
  select source_id, display, kind, priority, basis, delisted, depth, frequency,
         quota_per_day, free, status, role, access, tested, last_ok, note, updated_at,
         row_number() over (order by source_id) as id
  from public.data_source;

create or replace view public.data_sources with (security_invoker = true) as
  select d.source_id, d.display, d.kind as capability, d.credential_fingerprint as fingerprint,
         d.status, d.updated_at as added_at, d.note,
         row_number() over (order by d.source_id) as id
  from public.data_source d
  where d.credential_state <> 'none';          -- 只暴露"有凭据"的源，永不暴露 secret
```

> **为什么还要 `data_source_secret` 而不是直接 Vault**：`vault.secrets` 只能按 name 取、不能 join、不能做 CHECK；过渡期保留 jsonb 便于轮换脚本读写。目标态：`vault.create_secret()` + `data_source.vault_secret_name`，`data_source_secret` 只留 `fingerprint`。项目已经在用 Vault 存 GH PAT（`output/supabase_cron.sql`），有先例。

### 6.2 M2 — 维度层（`symbols` 启用 + `sectors` 并表 + `members` 补约束）

```sql
-- 6.2.1 维度表：把 symbols / sectors 合成一处
alter table public.symbols
  add column if not exists sector        text,
  add column if not exists sector_asof   date,
  add column if not exists sector_source text,
  add column if not exists updated_at    timestamptz not null default now(),
  add column if not exists delisted_date date;
-- 派生字段下线：first_month/last_month 会过期，改由视图算（见 6.2.4）
comment on column public.symbols.first_month is 'DEPRECATED：由 v_symbol_coverage 派生，勿写';
comment on column public.symbols.last_month  is 'DEPRECATED：同上';

insert into public.symbols(symbol, sector, sector_asof, sector_source)
select s.symbol, s.sector, current_date, s.source from public.sectors s
on conflict (symbol) do update
  set sector = excluded.sector, sector_source = excluded.sector_source, updated_at = now();

insert into public.symbols(symbol)
select distinct symbol from public.members
union select distinct symbol from public.prices_monthly
on conflict (symbol) do nothing;

-- 先核对再验证（空集才 validate；S&P 有过 BRK.B / BF.B 这类带点代码）
select symbol from public.symbols where symbol !~ '^[A-Z0-9][A-Z0-9.\-]{0,9}$';
alter table public.symbols add constraint symbols_symbol_ck check (symbol ~ '^[A-Z0-9][A-Z0-9.\-]{0,9}$') not valid;
-- 上一条查询返回 0 行后再执行：
-- alter table public.symbols validate constraint symbols_symbol_ck;

-- 6.2.2 sectors 降级为视图
alter table public.sectors rename to sectors_legacy;
create or replace view public.sectors with (security_invoker = true) as
  select symbol, sector, sector_source as source, sector_asof::timestamptz as updated_at
  from public.symbols where sector is not null;

-- 6.2.3 members：补 FK 与 CHECK（1,262 行，毫秒级）
alter table public.members
  add constraint members_end_ge_start_ck check (end_date >= start_date),
  add constraint members_symbol_fk foreign key (symbol) references public.symbols(symbol) not valid,
  add column if not exists as_of date default current_date;
alter table public.members validate constraint members_symbol_fk;
-- 哨兵 2099-12-31 保留（改 NULL 会打断 db_ref.load_members 的 .isoformat()），另给"在册"视图
create or replace view public.v_membership_open with (security_invoker = true) as
  select m.* from public.members m where m.end_date >= current_date;

-- 6.2.4 覆盖度改为派生视图（不落冗余列）
create materialized view if not exists public.mv_symbol_coverage as
  select symbol, min(month) as first_month, max(month) as last_month,
         count(*) as months, count(*) filter (where basis='total_return') as tr_months
  from public.prices_monthly group by symbol;
create unique index if not exists mv_symbol_coverage_pk on public.mv_symbol_coverage(symbol);
```

### 6.3 M3 — 事实层（`prices_monthly` 加固 + `corporate_action` 落数据）

```sql
-- 6.3.1 加列（PG17 加可空列 = 仅元数据，毫秒；生成列对 35 万行需要一次重写，约 2–4 秒）
alter table public.prices_monthly
  add column if not exists fetch_run_id uuid,
  add column if not exists adj_factor   numeric,
  add column if not exists month_start  date
      generated always as (
        case when month ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'
             then make_date(substr(month,1,4)::int, substr(month,6,2)::int, 1)
        end) stored;

-- 6.3.2 约束：先 NOT VALID（只对新行生效，不扫表），空闲时再 VALIDATE
alter table public.prices_monthly
  add constraint prices_month_ck  check (month ~ '^[0-9]{4}-(0[1-9]|1[0-2])$') not valid,
  add constraint prices_adj_ck    check (adj_close is null or adj_close > 0) not valid,
  add constraint prices_raw_ck    check (raw_close is null or raw_close > 0) not valid,
  add constraint prices_basis_ck  check (basis in ('total_return','price_only')) not valid,
  add constraint prices_quality_ck check (quality in ('ok','verified','suspect','patched')) not valid,
  add constraint prices_source_fk foreign key (source) references public.data_source(source_id) not valid;
-- 注意：fetch_run_id 的外键必须等 M6 给 fetch_runs 加上 run_id 之后再建（见 M6 末尾）

-- 逐条验证（SHARE UPDATE EXCLUSIVE，不阻塞读写）
alter table public.prices_monthly validate constraint prices_month_ck;
alter table public.prices_monthly validate constraint prices_adj_ck;
alter table public.prices_monthly validate constraint prices_raw_ck;
alter table public.prices_monthly validate constraint prices_basis_ck;
alter table public.prices_monthly validate constraint prices_quality_ck;
alter table public.prices_monthly validate constraint prices_source_fk;

-- 6.3.3 索引瘦身（CONCURRENTLY：不阻塞读写；不能放在事务块里）
drop index concurrently if exists public.idx_prices_symbol;   -- 被 PK(symbol,month,source) 前缀覆盖
drop index concurrently if exists public.idx_prices_basis;    -- 2 个取值，规划器不会用
create index concurrently if not exists idx_prices_export
  on public.prices_monthly (symbol, month)
  include (adj_close, raw_close, source, basis, quality);
create index concurrently if not exists idx_prices_month_start
  on public.prices_monthly (month_start);

-- 6.3.4 公司行动：让"全收益"可复算
alter table public.corporate_actions
  add column if not exists source_id   text references public.data_source(source_id),
  add column if not exists adj_factor  numeric,      -- 复权因子：adj = raw × cumprod(factor)
  add column if not exists amount      numeric,      -- 分红金额
  add column if not exists currency    text default 'USD',
  add column if not exists verified_at timestamptz,
  add column if not exists raw_payload jsonb;        -- 原始事件，留证
alter table public.corporate_actions
  add constraint ca_kind_ck check (kind in ('split','dividend','merger','ticker_change','spin_off')) not valid,
  add constraint ca_factor_ck check (adj_factor is null or adj_factor > 0) not valid;
alter table public.corporate_actions validate constraint ca_kind_ck;
create unique index if not exists ca_uq on public.corporate_actions (symbol, ex_date, kind, coalesce(source_id,''));
```

> **为什么这一节是业务 P0**：`relay_source.fetch_yahoo_relay()` 已经请求 `events=div,split`（L208）却把 `d["events"]` 丢掉（L220–L232 只取 adjclose/close）。也就是说**全收益的原料每天都在下载然后扔掉**，而 `corporate_actions` 是 0 行 → `adj_close` 无法用任何独立数据复算。落这张表 + `adj_factor` 之后，验收标准才能从"看数字对不对"升级为"逐月因子可复算"（§12-A6）。

### 6.4 M4 — 选源层（`series_selected` 由表降级为视图）

```sql
-- 6.4.1 选源决策表（847 行，替代 11.7 万行副本）
create table if not exists public.series_pick (
  symbol      text primary key references public.symbols(symbol),
  source_id   text not null references public.data_source(source_id),
  basis       text not null check (basis in ('total_return','price_only')),
  from_month  text not null default '2008-01' check (from_month ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
  months      int,
  coverage    numeric,                       -- 覆盖月数 / 期望月数
  rule        text default 'basis_first_then_priority_then_coverage',
  decided_at  timestamptz not null default now(),
  decided_by  text default current_user,
  note        text
);
comment on table public.series_pick is
  '每票单源（选源粒度为 symbol，永不逐月换源）。series_selected 是本表 ⋈ prices_monthly 的视图。';

-- 6.4.2 选源史（append-only；换源=新版本，可审计）
create table if not exists public.series_pick_history (
  id bigint generated always as identity primary key,
  symbol text not null, source_id text not null, basis text not null,
  superseded_at timestamptz not null default now(),
  reason text, decided_by text default current_user
);

-- 6.4.3 首次填充：与旧 db_select.BUILD 完全同规则（口径 → 源等级 → 覆盖月数）
insert into public.series_pick (symbol, source_id, basis, months)
select symbol, source, max(basis), count(*)
from public.prices_monthly p
where month >= '2008-01'
  and source = (
    select p2.source from public.prices_monthly p2
     where p2.symbol = p.symbol and p2.month >= '2008-01'
     group by p2.source
     order by max(case when p2.basis='total_return' then 0 else 1 end),
              coalesce((select d.priority from public.data_source d where d.source_id=p2.source), 99),
              count(*) desc
     limit 1)
group by symbol, source
on conflict (symbol) do nothing;

-- 6.4.4 视图：同名替换（读路径零改动）
create or replace view public.series_selected with (security_invoker = true) as
  select p.symbol, p.month, p.adj_close, p.raw_close, p.source, p.basis,
         p.quality, p.fetched_at
  from public.prices_monthly p
  join public.series_pick k
    on k.symbol = p.symbol and k.source_id = p.source
  where p.month >= k.from_month;

revoke all on public.series_selected from anon, authenticated;
grant select on public.series_selected to anon, authenticated;
```

**视图 vs 物化视图 → 判据（不要无脑选一边）**

| | 视图（推荐先上） | 物化视图（可选升级） |
|---|---|---|
| 正确性 | 恒等于"按规则选出来的结果"，**零漂移** | 刷新间隔内可能落后 |
| 读成本 | 35 万行 hash join 847 行：`EXPLAIN` 预期 ~60–120 ms（导出 6 次分片 ≈ 0.5–1 s） | 与今天相同（直接读 11.7 万行） |
| 维护 | 无 | `REFRESH ... CONCURRENTLY` 需要唯一索引，2× IO |
| 锁 | 无（视图不可写 → 天然只读） | `CONCURRENTLY` 不锁读 |
| 选它的条件 | **默认**。现有导出就是"全量扫一遍"，多 30–80 ms 可接受 | 当 `explain analyze` 实测 > 800 ms，或 REST 兜底路径超时率上升 |

> 升级路径：`create materialized view series_selected_mv ... ; refresh ... concurrently;` → `begin; alter view series_selected rename to series_selected_v; alter materialized view series_selected_mv rename to series_selected; commit;`（毫秒级切换，读路径不变）。**注意物化视图不能开 RLS**，只能靠 `grant select`，且 `security definer` 刷新函数必须 `set search_path`。

### 6.5 M5 — 锁层合并（`locks` + `locked_symbols` → `data_lock`）

```sql
-- 6.5.1 统一锁表：一把锁 = 一条不可变声明
create table if not exists public.data_lock (
  id            bigint generated always as identity primary key,
  scope         text not null check (scope in ('month_range','symbol','symbol_month')),
  symbol        text,
  month_from    text check (month_from ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
  month_to      text check (month_to   ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
  status        text not null default 'locked' check (status in ('locked','void')),
  row_count     bigint,
  symbol_count  int,
  fingerprint   text,                      -- 统一用 series_fingerprint()
  fingerprint_algo text not null default 'sha256',
  run_id        uuid,
  locked_at     timestamptz not null default now(),
  locked_by     text not null default current_user,
  supersedes_id bigint references public.data_lock(id),
  note          text,
  constraint data_lock_shape_ck check (
      (scope='month_range'  and symbol is null     and month_from is not null and month_to is not null)
   or (scope='symbol'       and symbol is not null and month_from is null)
   or (scope='symbol_month' and symbol is not null and month_from is not null)),
  constraint data_lock_range_ck check (month_to is null or month_from is null or month_to >= month_from)
);

create unique index if not exists data_lock_uq
  on public.data_lock (scope, symbol, month_from, month_to) nulls not distinct
  where status = 'locked';
-- 守卫用索引（两个，对应两种 scope）
create index if not exists data_lock_by_symbol on public.data_lock (symbol)
  where status='locked' and scope in ('symbol','symbol_month');
create index if not exists data_lock_by_month on public.data_lock (month_from, month_to)
  where status='locked' and scope='month_range';

-- 6.5.2 append-only：作废靠"插新行 + supersedes_id"，永不 UPDATE（解决 C5）
create or replace function public.data_lock_append_only() returns trigger
language plpgsql set search_path = public, pg_temp as $$
begin
  raise exception 'data_lock is append-only (attempted %)；作废请插入 status=void + supersedes_id', tg_op;
end $$;
drop trigger if exists trg_data_lock_append on public.data_lock;
create trigger trg_data_lock_append before update or delete on public.data_lock
  for each row execute function public.data_lock_append_only();

-- 6.5.3 有效锁判定（一处定义，全库复用）
create or replace function public.lock_is_effective(p_id bigint) returns boolean
language sql stable set search_path = public, pg_temp as $$
  select exists (select 1 from public.data_lock l
                  where l.id = p_id and l.status = 'locked'
                    and not exists (select 1 from public.data_lock x
                                     where x.supersedes_id = l.id and x.status = 'void'));
$$;

-- 6.5.4 唯一守卫：按月 × 按标的，两者都尊重 month 范围（修 P0-02）
create or replace function public.guard_frozen_prices() returns trigger
language plpgsql set search_path = public, pg_temp as $$
declare v_sym text; v_mon text; v_hit bigint;
begin
  if tg_op = 'DELETE' then v_sym := old.symbol; v_mon := old.month;
  else v_sym := new.symbol; v_mon := new.month; end if;

  if tg_op = 'UPDATE' and new.month <> old.month then
    raise exception 'month is immutable (% -> %)', old.month, new.month;
  end if;

  select l.id into v_hit
    from public.data_lock l
   where l.status = 'locked'
     and not exists (select 1 from public.data_lock x where x.supersedes_id = l.id and x.status='void')
     and ( (l.symbol = v_sym
            and (l.month_from is null or v_mon >= l.month_from)
            and (l.month_to   is null or v_mon <= l.month_to))
        or (l.scope = 'month_range' and v_mon between l.month_from and l.month_to) )
   limit 1;

  if v_hit is not null then
    raise exception 'frozen: % % is covered by data_lock#%（改历史请新建锁版本）', v_sym, v_mon, v_hit;
  end if;
  return case when tg_op = 'DELETE' then old else new end;
end $$;

drop trigger if exists trg_guard_months on public.prices_monthly;
create trigger trg_guard_months before insert or update or delete on public.prices_monthly
  for each row execute function public.guard_frozen_prices();

-- 6.5.5 选源也要锁（修 P0-03：否则锁定后换源=重写历史）
create or replace function public.guard_frozen_pick() returns trigger
language plpgsql set search_path = public, pg_temp as $$
declare v_sym text := coalesce(new.symbol, old.symbol);
begin
  if exists (select 1 from public.data_lock l
              where l.status='locked' and l.symbol = v_sym
                and not exists (select 1 from public.data_lock x
                                 where x.supersedes_id = l.id and x.status='void')) then
    raise exception '选源已冻结：% 存在有效锁，换源必须插入新锁版本并留痕', v_sym;
  end if;
  return coalesce(new, old);
end $$;
drop trigger if exists trg_guard_pick on public.series_pick;
create trigger trg_guard_pick before update or delete on public.series_pick
  for each row execute function public.guard_frozen_pick();

-- 6.5.6 统一指纹函数（替掉 md5/sha256[:16] 三套算法）
create extension if not exists pgcrypto with schema extensions;
create or replace function public.series_fingerprint(p_from text default '0000-01', p_to text default '9999-12')
returns text language sql stable set search_path = public, pg_temp as $$
  select 'sha256:' || encode(
           extensions.digest(
             coalesce(string_agg(symbol||'|'||month||'|'||coalesce(adj_close::text,''),
                                 E'\n' order by symbol, month), ''),
             'sha256'), 'hex')
  from public.series_selected
  where month between p_from and p_to;
$$;

-- 6.5.7 旧锁表 → 迁移 + 兼容视图
insert into public.data_lock(scope, symbol, month_from, month_to, status, row_count, symbol_count,
                             fingerprint, fingerprint_algo, locked_at, note)
select 'month_range', null, scope_from, scope_to, 'locked', rows, symbols, hash, 'md5', locked_at, note
from public.locks where status = 'locked';

insert into public.data_lock(scope, symbol, month_from, month_to, status, symbol_count,
                             fingerprint, fingerprint_algo, locked_at, note)
select 'symbol_month', symbol, '2012-01', '2025-12', 'locked', 1, hash, 'sha256-16', locked_at, note
from public.locked_symbols;

alter table public.locks          rename to locks_legacy;
alter table public.locked_symbols rename to locked_symbols_legacy;
create or replace view public.locks with (security_invoker = true) as
  select row_number() over (order by id)::int as id, 'monthly_prices' as kind,
         month_from as scope_from, month_to as scope_to, status, row_count as rows,
         symbol_count as symbols, fingerprint as hash, true as monthly_only, locked_at, note
  from public.data_lock where scope='month_range';
create or replace view public.locked_symbols with (security_invoker = true) as
  select symbol, locked_at, 168 as months, fingerprint as hash, note
  from public.data_lock where scope='symbol_month' and status='locked';
```

> **代价**：`guard_frozen_prices` 是行级触发器，每行 2 次索引查找（~2–5 µs）。5000 行批量 upsert ≈ 25 ms —— 可接受。若要 0 开销，唯一办法是"分区 + 对冻结分区 `REVOKE`"，见 §11。

### 6.6 M6 — 数据集与审计层

```sql
-- 6.6.1 datasets：补语义 + 状态机
alter table public.datasets
  add column if not exists definition jsonb,        -- {from,to,sources[],rule,expected_symbols}
  add column if not exists parent_id  int references public.datasets(id),
  add column if not exists locked_by  text,
  add column if not exists locked_fingerprint text,
  add column if not exists row_count int generated always as (months) stored;  -- months 旧名=行数，兼容旧代码
alter table public.datasets
  add constraint datasets_status_ck check (status in ('draft','verified','locked','superseded')),
  add constraint datasets_lock_ck check ((status='locked') = (locked_at is not null)) not valid;
alter table public.datasets validate constraint datasets_lock_ck;

-- 状态机：draft→verified→locked 单向；locked 只能被 superseded 取代
create or replace function public.guard_dataset_status() returns trigger
language plpgsql set search_path = public, pg_temp as $$
begin
  if old.status = 'locked' and new.status not in ('locked','superseded') then
    raise exception 'dataset % is locked; create a new version', old.id;
  end if;
  return new;
end $$;
drop trigger if exists trg_dataset_status on public.datasets;
create trigger trg_dataset_status before update on public.datasets
  for each row execute function public.guard_dataset_status();

-- 6.6.2 dataset_rows：只做"锁定时刻的快照"，补哈希
alter table public.dataset_rows
  add column if not exists quality text,
  add column if not exists row_hash text,
  add column if not exists snapshot_at timestamptz not null default now();
comment on table public.dataset_rows is
  '写一次永不变的快照。只允许在 dataset 转为 locked 时materialize；draft 数据集不再落全量副本。';

-- 6.6.3 校验字典 + 校验结论（补 run_id / 指纹 / 阈值）
create table if not exists public.validation_check (
  check_key text primary key,
  title     text not null,
  severity  text not null default 'P1' check (severity in ('P0','P1','P2')),
  owner     text,                 -- 采集 / 加工 / 展现
  blocks_publish boolean not null default false,
  note      text
);
insert into public.validation_check(check_key, title)
select distinct check_name, check_name from public.validations where check_name is not null
on conflict (check_key) do nothing;

alter table public.validations
  add column if not exists run_id     uuid,
  add column if not exists check_key  text references public.validation_check(check_key),
  add column if not exists subject_kind text check (subject_kind in ('dataset','lock','run','series','artifact')),
  add column if not exists subject_id text,
  add column if not exists severity   text,
  add column if not exists observed   text,
  add column if not exists expected   text,
  add column if not exists threshold  text,
  add column if not exists fingerprint text,          -- 被校验数据的 series_fingerprint()
  add column if not exists duration_ms int;
update public.validations set check_key = check_name where check_key is null;
alter table public.validations add constraint validations_status_ck check (status in ('pass','fail','warn','skip')) not valid;
alter table public.validations validate constraint validations_status_ck;

-- 6.6.4 fetch_runs：拆单位 + 补 run_id（旧列保留 2 个迭代）
alter table public.fetch_runs
  add column if not exists run_id uuid default gen_random_uuid(),
  add column if not exists status text check (status in ('running','ok','partial','failed')),
  add column if not exists symbols_requested int,
  add column if not exists symbols_ok int,
  add column if not exists symbols_failed int,
  add column if not exists rows_written bigint,
  add column if not exists stats jsonb,
  add column if not exists duration_ms int,
  add column if not exists host text,
  add column if not exists git_sha text;
create unique index if not exists fetch_runs_run_id_uq on public.fetch_runs(run_id);

-- 现在才建 prices_monthly → fetch_runs 的外键（M3 留的口子）
alter table public.prices_monthly
  add constraint prices_run_fk foreign key (fetch_run_id) references public.fetch_runs(run_id) not valid;

-- 归一化视图：把旧的"ok=行数"语义显式标注出来，避免误读
create or replace view public.fetch_runs_clean with (security_invoker = true) as
  select id, run_id, source, started_at, finished_at, status,
         coalesce(symbols_requested, requested)                      as symbols_requested,
         coalesce(symbols_ok,  case when ok > requested then null else ok end) as symbols_ok,
         coalesce(rows_written, case when ok > requested then ok   else null end) as rows_written,
         coalesce(symbols_failed, failed)                            as symbols_failed,
         stats, duration_ms, note
  from public.fetch_runs;

-- 6.6.5 计划表：去重 + 保留期（避免每天一份整包 JSON 无限增长）
alter table public.work_plans add column if not exists plan_date date default current_date;
create unique index if not exists work_plans_date_uq on public.work_plans(plan_date);
comment on table public.work_plans is '采集分工计划（每日一份，唯一）。ER 图中的 ingest_plan 即本表。';
```

### 6.7 M7 — 接入层（`ingest_staging` 与 `promote_staging` 重写）

```sql
alter table public.ingest_staging
  add column if not exists batch_id uuid default gen_random_uuid(),
  add column if not exists claimed_at timestamptz,
  add column if not exists promoted_at timestamptz,
  add column if not exists client_ip inet;
create index if not exists idx_staging_pending2
  on public.ingest_staging (id) where promoted = false and reject_reason is null;

-- 重写：① 固定 search_path（防劫持）② claim→merge→mark 三段式（杜绝"误标 promoted"）
--       ③ SKIP LOCKED（并发安全）④ 15 分钟回收陈旧 claim
-- 返回列由 2 变 3，create or replace 不能改返回类型 → 必须先 drop
drop function if exists public.promote_staging(int);
create or replace function public.promote_staging(max_rows int default 20000)
returns table(inserted int, rejected int, claimed int)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare n_ins int := 0; n_rej int := 0; n_cla int := 0;
begin
  -- ① 结构校验
  update public.ingest_staging s
     set reject_reason = case
           when s.month !~ '^[0-9]{4}-(0[1-9]|1[0-2])$' then 'bad_month'
           when s.adj_close is null or s.adj_close <= 0 then 'bad_price'
           when s.basis not in ('total_return','price_only') then 'bad_basis'
           when s.source is null then 'bad_source'
         end
   where s.promoted = false and s.reject_reason is null
     and (s.month !~ '^[0-9]{4}-(0[1-9]|1[0-2])$'
          or s.adj_close is null or s.adj_close <= 0
          or s.basis not in ('total_return','price_only')
          or s.source is null);
  select count(*) into n_rej from public.ingest_staging
   where promoted = false and reject_reason is not null;

  -- ② claim → merge → mark
  with claimed as (
    select s.id from public.ingest_staging s
     where s.promoted = false and s.reject_reason is null
       and (s.claimed_at is null or s.claimed_at < now() - interval '15 minutes')
     order by s.id
     limit max_rows
     for update skip locked
  ), picked as (
    update public.ingest_staging s set claimed_at = now()
      from claimed c where s.id = c.id
    returning s.id, s.symbol, s.month, s.adj_close, s.raw_close, s.source, s.quality, s.basis
  ), ins as (
    insert into public.prices_monthly(symbol, month, adj_close, raw_close, source, quality, basis)
    select distinct on (symbol, month, source)
           symbol, month, adj_close, raw_close, source, coalesce(quality,'ok'), basis
      from picked
     order by symbol, month, source, id desc
    on conflict (symbol, month, source) do update
      set adj_close = excluded.adj_close, raw_close = excluded.raw_close,
          quality = excluded.quality, basis = excluded.basis, fetched_at = now()
    returning 1
  )
  select count(*) into n_ins from ins;

  -- ③ 只标记"本次真正处理过"的行（picked 里出现的 id，靠 claimed_at 精确定位）
  update public.ingest_staging s
     set promoted = true, promoted_at = now()
   where s.promoted = false and s.claimed_at is not null
     and s.claimed_at >= now() - interval '2 minutes'
     and s.reject_reason is null;
  get diagnostics n_cla = row_count;

  return query select n_ins, n_rej, n_cla;
end $$;

revoke all on function public.promote_staging(int) from public;
grant execute on function public.promote_staging(int) to postgres;
```

### 6.8 M8 — RLS / GRANT 目标矩阵

| 表 / 视图 | RLS | anon SELECT | anon 写 | service 写 | 说明 |
|---|---|---|---|---|---|
| `prices_monthly` | ✅ | ✅ | ❌ | ✅ | **必须只留 SELECT 策略**（当前漏的就是它） |
| `series_selected`(view) | 由底层表决定 | ✅ | ❌（视图不可写） | ✅ | `security_invoker = true` |
| `series_pick` / `series_pick_history` | ✅ | ✅ | ❌ | ✅ | 选源可审计 |
| `data_lock` | ✅ | ✅ | ❌ | ✅ | append-only 触发器兜底 |
| `dataset_rows` / `datasets` | ✅ | ✅ | ❌ | ✅ | **修 P1-06：补 anon SELECT 策略**，否则 CI 无法做可追溯性校验 |
| `validations` / `validation_check` | ✅ | ✅ | ❌ | ✅ | |
| `fetch_runs` | ✅ | ✅ | ❌ | ✅ | |
| `data_source` | ✅ | ✅ | ❌ | ✅ | 能力矩阵公开 |
| `data_source_secret` | ✅ | ❌（无策略=全拒） | ❌ | ✅ | 凭据 |
| `members` / `symbols` / `sectors`(view) | ✅ | ✅ | ❌ | ✅ | 参考数据公开 |
| `ingest_staging` | ✅ | ✅ | **INSERT only** | ✅ | 免 secret 通道；不给 UPDATE/DELETE |
| `work_plans` | ✅ | ✅ | ❌ | ✅ | 补 SELECT 策略（P1-06） |
| `holdings` | ✅ | ✅ | INSERT/UPDATE | ✅ | 站点云同步的唯一例外 |
| `schema_migration` | ✅ | ❌ | ❌ | ✅ | 运维账本 |

```sql
-- 逐表落实（幂等；policy 先删后建，避免"for all"残留）
do $$
declare r record;
begin
  for r in select tablename, policyname from pg_policies
            where schemaname='public'
              and tablename in ('prices_monthly','series_selected','dataset_rows','datasets',
                                'validations','fetch_runs','data_source','members','symbols',
                                'locks','locked_symbols','sectors','source_registry','data_sources')
  loop execute format('drop policy if exists %I on public.%I', r.policyname, r.tablename);
  end loop;
end $$;

do $$
declare t text;
begin
  foreach t in array array['prices_monthly','series_selected','dataset_rows','datasets',
                           'validations','validation_check','fetch_runs','data_source',
                           'members','symbols','locks','locked_symbols','sectors',
                           'source_registry','data_sources','series_pick','series_pick_history','data_lock']
  loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists public_read on public.%I', t);
    execute format('create policy public_read on public.%I for select to anon, authenticated using (true)', t);
    execute format('revoke insert, update, delete, truncate on public.%I from anon, authenticated', t);
    execute format('grant select on public.%I to anon, authenticated', t);
  end loop;
end $$;

-- staging：只给 INSERT（+ SELECT 便于回读校验）
drop policy if exists staging_insert on public.ingest_staging;
create policy staging_insert on public.ingest_staging for insert to anon, authenticated with check (true);
drop policy if exists staging_read on public.ingest_staging;
create policy staging_read on public.ingest_staging for select to anon, authenticated using (true);
revoke update, delete, truncate on public.ingest_staging from anon, authenticated;
grant insert, select on public.ingest_staging to anon, authenticated;
```

### 6.9 索引清单（前 → 后）

| 表 | 现状 | 目标 | 净变化 |
|---|---|---|---|
| `prices_monthly` | PK + idx_prices_symbol + idx_prices_month + idx_prices_basis | PK + idx_prices_month + **idx_prices_export**(覆盖) + **idx_prices_month_start** | 4 → 4，但去掉 2 个废索引、加 2 个有用索引，写放大 −30% |
| `series_selected`(view) | PK + idx_series_month（表） | 随表消失（视图走 `prices_monthly` 的索引） | −2 |
| `dataset_rows` | PK | PK + **idx_dataset_rows_symbol** `(dataset_id, symbol) include (month)`（可选） | +0/+1 |
| `validations` | PK | PK + **idx_validations_subject** `(check_key, ran_at desc)` | +1 |
| `fetch_runs` | PK | PK + unique(run_id) + **idx_fetch_runs_source_time** `(source, started_at desc)` | +2 |
| `data_lock` | — | 2 个部分索引（守卫用） | +2 |
| `series_pick` | — | PK only | 0 |
| `ingest_staging` | PK + partial(promoted) | PK + **partial(id) where pending** | +0（替换） |

> 索引不是越多越好：`prices_monthly` 每多 1 个索引，每次 `execute_values` 批量 upsert（CI 每 6 小时数万行）就要多写一棵 B-tree。删掉 2 个废索引 ≈ 写入吞吐 +25~30%，并省下约 15–20 MB（免费额度 500 MB，见 §11）。

<!-- PART3 -->
