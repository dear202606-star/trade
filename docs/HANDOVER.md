# 交接说明（给下一个 agent）

> **提交前最新状态**：以 `DELIVERY_STATUS_2026-11-02.md` 为当前交付范围和剩余验收清单。87 项回归通过，13 个真实流动性窗口实库与原始日线独立求和通过；待修数据与发布自检 5 项失败明确保留。连续资格差异及补数审计均已终结，不再沿用下方运行中/待决策快照。本轮只推送独立分支，不部署、不提升旧基线。

> **用户新决策已收到，覆盖下方待决策快照**：B 按已核验同一证券连续成分资格筛选，使用年末有效代码。102 已部署，七组映射入在线库；B 已接入、77 项回归通过，实库差异分析运行句柄 60610，日志 `/tmp/leaderquant-membership-impact.log`。发布自检现为 85/0/5，新增一项是旧产物尚无连续资格验收标记，不能手工加标记放行。不要再次询问已决定的排名、承接或连续资格规则。

> 最后更新：2026-11-02（北京；名义股价修正实施中）
> 仓库：`dear202606-star/trade`（分支 `main`）｜ 站点：<https://dear202606-star.github.io/trade/>

> **最新接续点**：用户已决定按当时实际股价执行 5 美元门槛。当前 45 项回归通过，数据库验收 22/2/0，发布自检 85/0/2（A/B 新口径历史尚未完成）。PIT 全收益 6,791/8,032=84.55%，距 95% 差 840 格。090–095 已部署，代码未提交/推送。AV 已实测可用，但滚动额度 25/25 用完。新发现旧回测会沿用并购/分拆后的缺价，已拦截；待用户决定企业行动承接算法，不能重发旧基线。最新事实和证据以 `CONVERGENCE_REVIEW_2026-11-02.md` 顶部增量为准，下文数字包含历史快照。

> **再接续增量**：096–097 已部署，2023 和 2026 免费事件快照分别新增 4,768 和 893 条原价观察，当前筛选视图实际新增可用 5,296 条；总计 96,935 条，证券越界/未证明单位均为 0。科技股 18 代码全部通过，先前 4 个超时已恢复，版本和 SHA256 已固定，重跑无重复行。47 项回归通过，A/B 完整核验仍未达标。不可把观察行数、候选格数和全收益覆盖混算。可用 `research/audit_nominal_inventory.py` 重建实时盘点；不得通过修改旧 JSON 标记放行。

> **标签及锁守卫修复**：最新 49 项回归通过。已纠正 28 个“决策标签全收益、实际源仅价格”的错误，实库剩余 0；同来源口径变化现在会刷新，锁定/冻结仍拒绝。098 已部署到真实的 `guard_frozen_pick`（050 绑定），9 项真实回滚探针通过。`locked_symbols` 实际为 `data_lock` 兼容视图，不能当作旧实体表插入测试。价格、选源、锁及旧基线未改。详见最新验收顶部和 `output/selection_*` 回执。

> **最新继续点**：用户已决定现金和全部后继股票持有到年末，不再等待此决策。已实现估值核心并核验 LB 全十二个月/CVC 现金退出的事件证据包，但未接入正式 A/B 生产事件账本。最新 56 项回归、13 项真实回滚守卫通过，数据库 22/2/0；099 防止旧客户端重新写错口径。发布自检仍 85/0/2。东方财富 fqt0 月线探测显示 NVDA/AAPL 原价正确，226 个月可用；仍须发现全量证券标识、逐票交叉核验并入库，不能使用会出现负价的 qfq1 冒充全收益。下一步优先完成事件账本接入和这个免费批量原价来源；旧基线保留，尚未提交/推送。

> **当前权威接续点（覆盖上面快照）**：用户已确认排名与持仓均按实际现金及全部后继股票承接；四条事件路径（LB/BBWI 2021、CVC 2016、WDC 2025）已接入数据库并实测 A/B/排名一致，69 项回归通过，事件账本三种不可变守卫及新版本撤回保护通过。100–101 已部署。东方财富原价视图已新增 5,304 条/101 代码，总原价观察 102,238 条；第二个 100 票批次已完成，新增 4,650 条；累计东方财富 9,954 条/191 代码、全部来源原价观察 106,888 条。差异审计仍在运行，见 `/tmp/leaderquant-impact-current.log`；不要重复启动未终结进程。未重建正式产物，未提交/推送。新增待处理：月度流动性错误使用月成交额代理日均，整年成分按代码切分可能漏同股更名证券。详情及边界见验收文档最新顶部。

> **两条发行人原价与最新审计**：PLTR/VST 2024-12-31 精确原价已核验入库，仅批准两点；总观察 106,890 条。最新差异审计已结束，34 个 A/B 年度的门槛上下界持仓均一致，但仍有 2,406 次/1,232 去重候选缺价，完整验收 0/34，不能据持仓不变宣称全数据通过。当前无运行中的采集/审计进程。待用户回答的唯一新策略问题是“已核验同股更名是否合并连续成分资格”，未答前不改 B 股票池。月度流动性仍未关闭；来源探针明确 PLTR 全年日线不完整，不能沿用先前四票均完整的误判。69 项回归、数据库 22/2/0、发布自检 85/0/4；未提交/推送。

---

## 一、这个项目在干什么

每**年末**用过去 12 个月的涨幅，在当时的标普 500 成分里选前 3 名，**持有一整年**。
约束：同行业最多 2 只、单只权重不超过 60%。数据口径是**全收益**（含分红、拆股调整）。

- 官方区间：**2012–2025**（2010–2011 因池子过小被排除）
- 当前成绩：累计 **+3678%（36.78 倍）**，年化 29.62%，最大回撤 -21.7%
- 页面展示 2013–2025（13 年，+2972%），因为起点是 2012-12

---

## 二、必须先知道的三条铁律

1. **在线数据库是唯一数据源**。`prices_monthly` / `series_pick` / `members` 等都在 Supabase。
   本地文件和 `docs/data/*.json` 都是**可重建产物**，不许当数据源。
2. **读不到库就报错，不许静默退回文件**。曾经因为这个让站点用了 3 天旧数据。
3. **锁过的数据不可改**。`data_lock` 是 append-only，改历史要插新锁版本，不能 UPDATE。

---

## 三、需要你手上有的东西（**这些不在 git 里**）

### 3.1 四份凭据（本地文件）

| 文件 | 内容 | 用途 | 缺失后果 |
|---|---|---|---|
| `output/_supabase_dbpass.txt` | Supabase 数据库口令（32 位） | 直连数据库跑脚本、DDL | **所有需要写库的脚本全部失败** |
| `output/_api_keys.local.txt` | `alphavantage_key`、`eodhd_token`（另有 email/password/kaggle_token 是历史遗留） | 补退市票全收益、交叉校验 | 覆盖率停在 83%，无法推进 |
| `output/_pat_token.txt` | GitHub PAT（fine-grained，**2027-01-25 到期**） | 触发 workflow、查运行状态 | 无法手动触发构建 |
| `~/.ssh/id_ed25519_trade` | SSH 私钥 | `git push` | 无法推送 |

**权限边界（实测）**：这个 PAT **不能**管理仓库 secrets（调用返回 403），
所以 `SUPABASE_DB_PASSWORD` 这个 secret 只能在网页上改。

### 3.2 已经配置在 GitHub 上的（不需要你再设）

- Repository secret：`SUPABASE_DB_PASSWORD`（已确认存在，长度 32，与本地口令一致）
- Pages：`build_type: workflow`，用 Actions 上传 `docs/` 产物

### 3.3 公开信息（已在 git 里，不用另找）

- Supabase 项目 ref：`fnvahsyrzmmxwovbsqpc`
- 连接串：`aws-0-ap-northeast-1.pooler.supabase.com`，端口 **6543**（事务池，日常用）/ **5432**（会话模式，大事务用）
- anon 公钥：`static/cloud.json`（公开读，写被禁）

### 3.4 在数据库里、不在 git 里的东西

| 对象 | 说明 |
|---|---|
| **pg_cron 三个任务** | job 4 `trade-dispatch`（每 2h）、job 5 `trade-dispatch-market`（盘中每 5 分钟）、job 6 `promote_staging`（每 10 分钟）。**换库要重建**，脚本在 `output/supabase_cron.sql` |
| **RLS 与 GRANT** | 17 张表 anon 只读、`prices_monthly` 开 RLS、`data_source_secret` 禁读。重建方法：`python db_security_fix.py` |
| **锁数据** | `data_lock` 961 条，是逐条积累的，**不在任何文件里**，只能从库里导 |
| **`series_pick`** | 847 条选源决策，同上 |

> ⚠️ **换 Supabase 项目 = 这些全丢**（除了能由 `prices_monthly` 重算的 `series_pick`）。
> 锁的指纹可以重算，但「已锁定」这个事实会丢，需要重新跑 `lock_symbols.py --lock`。

### 3.5 本地运行环境

```
Python: C:\Users\Administrator\.dsh\dsh-runtimes\dsh-next-primary-runtime\dependencies\python\python.exe
依赖:   pip install -r requirements.txt（含 psycopg2-binary、pyarrow、playwright）
浏览器: Chrome（playwright 用 channel='chrome'）
```

---

## 四、接手后第一件事（按顺序）

```bash
# 1. 确认能连库
python -c "import db_prices; print(len(db_prices.load_all()))"     # 应输出 ~848

# 2. 看现在缺什么
python coverage_pit.py                    # 按年份的缺口
# 或直接查库：select * from v_collect_summary;

# 3. 全量验收（7 项，必须全绿）
python selftest.py            # 85 项
python audit_b.py             # 158 项
python schema_check.py        # 25 项
python verify_locks.py        # 961 只锁指纹
python verify_tr.py           # 全收益独立复算
python realtime_check.py      # 5 分钟实时层
python db_validate.py         # 28 条校验

# 4. 继续补数据
gh workflow run daily-backfill.yml        # Alpha Vantage 25 只/天
```

---

## 五、当前剩余工作（2026-11-01 只读复核）

Supabase `series_selected` 与 `members` 的只读查询显示，2010–2025 年共需 8,032 个「标的-年」：
6,453 个有全收益数据（80.3%），1,088 个只有价格数据，491 个没有数据。
因此有 1,579 个标的年尚未满足全收益口径；达到 95% 至少还需 1,178 个全收益标的年。

此前的 **1,338** 缺口数字已过期。`output/gap_plan.json` 的 2,659 是更宽口径的「12 月标的-月」缺价数，
两者分母和要求不同，不可互换。`docs/COVERAGE_STATUS.md` 与 `docs/DATA_SOURCES.md` 仍保留旧快照，阅读时以本节最新只读查询为准。

补数自动化的阻塞与修复状态：

1. GitHub Actions 中的 `daily-backfill.yml` 目前 **0 次运行**。旧的 `backfill-prices.yml` 最近一次运行在
   2026-11-01 因 `ALPHAVANTAGE_API_KEY` 为空，在发出数据请求前退出；该静态快照通道已从本地版本移除。
2. 本地 `relay_source.py` 已改为读取工作流环境变量，并在发出 Alpha Vantage 请求前通过 `sys_event` 原子预约额度；
   数据库读取或预约失败时跳过该源，滚动 24 小时硬上限 25 次。每日补数工作流已改为一次调度，并把校验切到真实数据库的 `db_validate.py`。

上述改动仅在本地 checkout，尚未推送或在 GitHub Actions 运行。最近旧工作流的空环境值表明 Alpha Vantage 密钥尚未在可运行的 Actions 路径中得到验证；仓库里已确认的只有 `SUPABASE_DB_PASSWORD`。

公开站点的数据展示授权尚未确认。不得绕过供应商限制或复用未授权凭据。要继续取得并公开展示新增历史数据，需要确认可公开展示/再分发的数据源授权；否则只能继续用已有来源并保留缺口。取得有效历史数据后，还须逐源校验、重算覆盖和策略结果，再决定是否更新锁与发布。
请勿用未复权价格补成全收益，也不要把无法取得的数据伪造成完整；100% 不能承诺，现实目标仍是 ≥95% 且每个缺口可解释。

---

## 六、常见操作

| 想做什么 | 怎么做 |
|---|---|
| 改页面文案 | 改 `site_src/index.html` → `python site_build.py` → push。**CI 会重新构建，约 10–20 分钟后生效** |
| 手动触发构建 | `gh workflow run quotes.yml` 或 `collect-full.yml` |
| 查某只票的源 | `select * from series_pick where symbol='AAPL'` |
| 查某只票的覆盖 | `select * from v_collect_state where symbol='AAPL'` |
| 补某只票 | `python relay_source.py --symbols 1`（会自动按优先级选源） |
| 提升基线（数据修正后） | `BASELINE_UPDATE=1 python variant_b.py` —— **会改对外数字，先想清楚** |
| 看数据库结构 | `docs/DATABASE.md`、`docs/DB_SCHEMA_REDESIGN_2026-11.md` |

---

## 七、踩过的坑（都修好了，但别再犯）

| 坑 | 症状 | 现在怎么防 |
|---|---|---|
| 选源只看口径不看覆盖 | 选了 13 个月的 EODHD，丢了 189 个月的新浪 | 覆盖优先，≥100 个月才优先全收益 |
| `series_pick` INSERT 列数不符 | CI 每 6 小时全失败，拉的数据白拉 | 构建失败必然让管线停 |
| 看板 `count(*)` 当月份数 | 出现 171 > 168 的荒谬判定 | 多源表必须 `count(distinct month)` |
| 月度流程调用看板生成器缺少输出路径 | 生成看板时警告并跳过 | 已给 `dashboard.build` 传入 `output/dashboard.html` |
| 基线漂移检查太松 | +60% 的数字差点静默上线 | 漂移必须人工确认才能提基线 |
| `relay_source` 没导入 `DB` | 所有采集运行都没写进台账，被 try/except 吞掉 | 静默的异常最危险 |
| 页面改动以为推送即生效 | 实际要等下一轮 CI 完整跑完 | 推送后等 10–20 分钟再验证 |

---

## 八、文档地图

| 文件 | 内容 |
|---|---|
| `docs/HANDOVER.md` | 本文件 |
| `docs/QA_MECHANISM.md` | 六层质量闸门 + 每层验证证据 + 基线变更记录 |
| `docs/SOURCE_EXHAUSTION.md` | 30+ 数据源穷举结论 + 敏感性分析 |
| `docs/COVERAGE_STATUS.md` | 覆盖率现状与口径决策 |
| `docs/DATABASE.md` | 库表说明 |
| `docs/DB_SCHEMA_REDESIGN_2026-11.md` | 架构师的 1591 行重构设计（含 P0–P9 迁移步骤） |
| `docs/LOCKING.md` | 锁定机制 |
| `docs/DATA_SOURCES.md` | 数据源能力矩阵 |
| `sql/*.sql` | 唯一的 DDL 来源（010 守卫、020 选源、030 源表、040 维度、050 锁、060 暂存、070 事件、080 看板） |

## 2026-11-01 CC0 档案接入结果

官方 Kaggle `borismarjanovic/price-volume-data-for-all-us-stocks-etfs` v3（CC0，价格声明含分红和拆股调整）已通过 `import_kaggle_cc0.py --write` 接入：132 个候选，122 个通过，10 个跳过；13,645 条月线全部经暂存提升到 `prices_monthly`，拒收 0。`source=kaggle_cc0`，`raw_close=NULL`，档案止于 2017-11。可提供 656 个缺失年末候选点，但这不是选中序列覆盖提升。

选源预演发现 CHK/DNB/HCP/PX/S 等现有序列起点在 2020 年以后，旧档案有代码复用风险；SCG 换源也会丢失后续历史。旧版线上定时选源在 13:17 UTC 自动切入了 CHK/DNB/HCP/PX/S/SCG/UTX 共 7 个代码；本轮已在事务内恢复前 6 个原源，移除原本没有选源的 UTX 档案决策，并部署 `sql/090_archive_selection_guard.sql` 数据库约束，旧客户端也无法再次选入。档案数据保留，未更改锁和公开策略基线。不能把中间误选期间的覆盖率视为改善。

入库回执：`promote_staging(20000)` 返回 `(13645, 0)`；正式表 13,645 行/122 代码；对应暂存全部 promoted=true。代码仍为本地改动，未推送。

### 本轮收敛与剩余事项

- `audit_coverage.py` / `output/coverage_audit.json`：核验更名后全收益 6,755/8,032（84.1%）；无数据 292（含隔离）、仅价格 985，距 95% 差 876。两个档案去重可提供其中 783 格候选（2017 档案 621、2020 档案 516，有重叠），均未计入选中覆盖。
- 数据库档案拦截已实测：尝试插入档案选源触发 CheckViolation，测试事务已回滚；本地选源预演变更 0。
- 补齐 `db_select.load_all`，读取选中单源视图；修复自动写入决策时把价格源标成全收益的问题。
- `db_prices` 无法核对在线版本戳时不再按“两小时内”放行旧缓存，必须重读受保护 REST 视图或失败，避免缓存绕过新隔离。
- 修复 `site_build.py` 中历史文件和信号看板只在健康检查异常时生成的缩进错误。
- 接下来应核验发行人及证券有效期，再评估历史序列，不能把同代码新旧公司直接拼接。HCP 身份风险已有 SEC 原始材料确认，见 `docs/ARCHIVE_IDENTITY_REVIEW.md`。
- Actions 的 AV 密钥仍需实际运行验证；95% 数据目标尚未完成，不承诺完成日期。
- `sql/091_series_quarantine.sql` 已部署，隔离 BMC/CBE/COV/EP/GAS/HAR/PARA/PCL/SBNY/SCG/TIE 共 11 个待核验代码。2,603 条底层数据全部保留；`series_pick` 仍可审计原源，选中视图及新版候选查询排除隔离代码。旧采集器重新入库也无法解除隔离；确认真实事件/修正数据后须单独复核放行。更名补数后当前选中视图 837 代码/143,686 行。
- 初始 76 个大跳变中，67 个发生在连续月份，16 个位于成分月份。APA 的 2020-03 跌幅有两个源相符，不能自动当坏数据。PARA 的 HF 价格量级与 Sina 明显不符；EP 有 SEC 代码复用证据。`research/audit_selected_jumps.py` 可重查原始决策（包括已隔离代码）。
- `research/audit_quarantine_impact.py` 用现行 B 的 `pick_year/year_perf` 做只读对比：隔离上述 11 个代码前后，2010–2025 每年持仓和收益均不变，与已发布 B 结果也无差异。结果在 `output/quarantine_impact.json`；原始带版本戳数据库快照保存在本机被忽略的 `output/_quarantine_review_prices.json`。没有改写策略产物和基线。
- CC0 档案缺少独立 raw_close，不能假充原价满足现行 5 美元门槛；即使身份核验通过，也须另行取得可信原价后才可用于该排名规则。
- 本轮 BK 补数实测：Yahoo 返回 429 后停止；AV 一次网络失败、一次返回 Error Message，均未写入，已记入请求前预约额度。不能据此承诺 AV 自动补齐。旧“8 天到 96%”推断已在 `SOURCE_EXHAUSTION.md` 标为过期。
- 新环境依次执行 `sql/090_archive_selection_guard.sql`、`sql/091_series_quarantine.sql`、`sql/092_yahoo2020_archive_guard.sql`、`sql/093_promote_exact_batch.sql` 后再运行新版选源和验收。完整站点自检需先生成真实产物，不能以数据库专用 `--data-only` 替代发布验收。
- 后续只读复查发现 12 个缺口格在库内有非档案全收益候选（FOX/Q/CTRA/DAY/PSKY/WBD）；候选未被计为已覆盖，也未换源。身份边界和覆盖损失见 `EXISTING_TR_REVIEW_2026-11-02.md`，逐格候选已写入 `coverage_audit.json`，不要重复下载已有记录或重复计算 Q 的两个候选源。

### 2026-11-02 后续实际进展

用户已决定 5 美元门槛按当时实际股价修正，已恢复目标工作。新增 094 筛选观察表/视图独立于收益价和锁；缺少单位证据不得选入。WIKI 原价 62,077 条可读，130,211 条新浪观察因价格单位待核验而全部排除筛选。36 项回归通过，最新发布自检 **85 通过/2 失败**，准确拦截仍为旧筛选口径的 A/B 历史。旧 B 基线原样备份；完整 A/B 34 个口径年份候选盘点见 `output/nominal_price_impact.json`。尚未提交、推送、部署或提升正式基线。后续先补齐名义价与身份边界，再重算审查；不能只给旧 JSON 补标记让自检通过。详细依据见 `PRICE_FILTER_BASIS_DECISION.md`、`CONVERGENCE_REVIEW_2026-11-02.md`。

- `jacksoncrow/stock-market-dataset` v2 官方卡为 CC0，数据止于 2020-04-01；排除不完整 4 月，只取至 2020-03 的完整月。760 个成员代码有证券名称元数据。`import_kaggle_yahoo2020.py --write` 处理 100 个候选：88 个通过、12 个跳过，12,201 行正式提升，拒收 0、有效待处理 0，来源为 `kaggle_yahoo2020`。`Close` 仅拆股复权，不能叫当时名义原价。该源已在 092 和本地选源代码中禁止自动选入。
- Kaggle 下载器支持单文件 ZIP，并限制压缩前后大小、拒绝非请求文件，跨域跳转移除凭据；遇 429 即停止进一步请求。不会在限流后切代理重试。
- 实测发现原 `promote_staging` 会将未写入的超限行和不足 24 个月的新标的也标为 promoted。093 已部署：串行提升、校验期间阻止新插入混入未校验批次，只标记真实写入的键及其旧重复记录。`research/verify_staging_batch.py` 在回滚事务中验证上限溢出、续跑、新标的等待、NaN 拒收，测试行全部回滚。
- 初次 12,201 行提升触发超时并整体回滚，队列未丢失。执行计划显示队列只有 id/promoted 索引，逐键回写扫描大量旧行；093 增加待处理键索引并 ANALYZE 后，实批 12,201 行在 3.05 秒提升完成，返回 `(12201,0)`。没有扩大超时或跳过校验。
- 4 组同股更名 ANTM→ELV、ABC→COR、PKI→RVTY、BLL→BALL 有 SEC/发行人依据，存于 `static/verified_ticker_renames.json`。每组与独立 2017 档案比较 93 个月收益，中位偏差约 0.0027%～0.0040%，最大偏差小于 0.45%。`import_verified_renames.py` 在 B 差异为零时，精确复制既有 Yahoo 数值到旧代码暂存，648 行提升且仅变更这 4 个选源；没有新网络报价或改动原价数值。延续仅至更名当年年末，避免填充之后的旧代码年份。实际补上 50 个 PIT 格；旧源、锁、正式基线均保留。
- 最新自检：29 项回归通过（含首月无穷值、原价 NaN、非法月份和未知口径拒收）；数据库验收 22 通过/2 警告/0 失败（数量 837<840、全收益月行占比 83.5%）；站点自检 85 通过/0 警告/0 失败。新增档案没有自动进入策略。
- **新增实质阻塞：5 美元门槛使用了包含未来拆股的历史价。** NVDA 2016-12 现存 raw_close≈2.6685，而乘回 2021/2024 后续拆股因子为约 106.74。只读单例已证明会改变 2016/2017 的 B 持仓，正式结果未改。全局筛选价口径和基线处理需要用户决策，详见 `PRICE_FILTER_BASIS_DECISION.md`；不能用现行自检通过来宣称方法问题已解决。
