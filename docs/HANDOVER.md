# 交接说明（给下一个 agent）

> 最后更新：2026-11-01 18:10（北京）｜ 调度章节更新于 2026-11-11 00:xx（v6.11）
> 仓库：`dear202606-star/trade`（分支 `main`）｜ 站点：<https://dear202606-star.github.io/trade/>

---

## 零、先看这条：自动执行为什么不稳，以及现在的容错骨架

**不要再把「修某条 cron」当作解法。** GitHub Actions 免费版 `schedule` 是
尽力而为，实测可延迟约 6 小时甚至整批丢弃；而 11-06 为收敛 egress 执行
`sql/124_egress_schedule.sql` 时，把 Supabase `pg_cron` 的
`trade-dispatch` / `trade-dispatch-market` 也一起停了，调度权从此 100%
落在 GitHub cron 上。看门狗（原唯一自愈入口）自己也是 schedule 触发，
于是「被兜底者 + 兜底者」共模故障。

现在的判定基准是**数据状态而不是时钟**：`reconcile_sessions.py` 只读浏览器
同源的 Turso 端点，用已复核 NYSE 日历算出应有场次，缺就补（盘中实时 /
收盘按 `as_of` 逻辑时间）。`reconcile.yml` 由 `workflow_run` + 错峰 cron +
手动 / push + **平台外心跳**四重独立源唤起，带冷却、在途、同场次 3 次
上限防自激。`quotes.yml` 每次采集成功都立即派发 `db-sync.yml` 推 Turso，
定时同步只作兜底。

**平台外心跳已上线（v6.11，勿当「待办」）**：Supabase `cron.job`
`trade-reconcile-tick`（当前 jobid 8，重装会变，`*/20 * * * *`）用 `pg_net` 打 GitHub API
唤醒对账。因 Vault 的 `gh_dispatch_token` 只有 `actions:write`
（`repository_dispatch` 实测 403），实际走
`workflow_dispatch + inputs.source=pg-cron`（204）。安装 / 探查 / 摘除 /
验证**只用** `gh workflow run heartbeat-setup.yml -f action=...`，
不要手工去 SQL Editor 粘 SQL，也不要把 PAT 写进仓库。凭据在 Supabase
Vault，轮换后重跑一次 `enable`。每次对账会顺带跑
`external_heartbeat.py --health`，心跳 >90 分钟未到达只出 warning。
完整实证、操作手册与失效边界见 `docs/EXTERNAL_CRON.md`、
`docs/CHANGELOG.md` v6.11。

⚠️ 勿回退：`quotes.yml` 的同步条件、`db-sync.yml` 的
`cancel-in-progress: false`、`publish_ui.py` 的 5xx 重试降级。
⚠️ 勿恢复：`trade-dispatch` / `trade-dispatch-market` 两个高频派发采集的作业
（1139 次 / 10 天、egress 399% 超额的放大器）。

`collect-full.yml` / `collect-ci.yml` / `backfill-prices.yml` 是历史回填链路，
因 Yahoo 补缺未完成 / HTTP 402 付费墙 / Alpha Vantage 25 次每日限流而
周期性失败，**与日更链路无关**，不要为了「让 CI 全绿」去绕过数据门禁。

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

## 五、当前唯一的剩余工作

```
缺口 1,338 个「标的-年」，其中 1,288 个（96%）在退市票上
只有 Alpha Vantage 有退市票的全收益历史 → 25 次/天 → 约 7 天
```

已穷举 30+ 个源（见 `docs/SOURCE_EXHAUSTION.md`），免费世界没有第二家。

**注意**：2010 年前破产、从未被数字化的成分股，任何免费源都没有记录，
所以 **100% 不可达**。现实目标是 ≥95% 且每个缺口可解释。

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
| 看数据库结构 | `sql/` 下的现行迁移脚本、`docs/DB_SCHEMA_REDESIGN_2026-11.md` |

---

## 七、踩过的坑（都修好了，但别再犯）

| 坑 | 症状 | 现在怎么防 |
|---|---|---|
| 选源只看口径不看覆盖 | 选了 13 个月的 EODHD，丢了 189 个月的新浪 | 覆盖优先，≥100 个月才优先全收益 |
| `series_pick` INSERT 列数不符 | CI 每 6 小时全失败，拉的数据白拉 | 构建失败必然让管线停 |
| 看板 `count(*)` 当月份数 | 出现 171 > 168 的荒谬判定 | 多源表必须 `count(distinct month)` |
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
| `sql/*.sql` | 线上库表结构的唯一事实来源（21 个迁移脚本） |
| `docs/DB_SCHEMA_REDESIGN_2026-11.md` | 架构师的 1591 行重构设计（含 P0–P9 迁移步骤） |
| `docs/LOCKING.md` | 锁定机制 |
| `docs/DATA_SOURCES.md` | 数据源能力矩阵 |
| `sql/*.sql` | 唯一的 DDL 来源（010 守卫、020 选源、030 源表、040 维度、050 锁、060 暂存、070 事件、080 看板） |
