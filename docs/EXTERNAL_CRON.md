# 平台外心跳（2026-11-10 已上线）

> **2026-11-10 二次更正**：本文第一版称「Supabase 定时触发已配置好」是错的；
> 第二版称「外部心跳是可选增强、尚未接入」也已过期。**现在心跳已实际安装并运行**，
> 不要再按旧版的「二选一方案」操作，也不要手工去 SQL Editor 粘贴 SQL。

## 一、为什么必须有平台外时钟

`sql/124_egress_schedule.sql` 为收敛出口流量，在 11-06 停掉了库内两个 dispatch 作业：

| 作业 | 原频率 | 现状 |
|---|---|---|
| `trade-dispatch-market` | `*/5 13-21 * * 1-5` | 已停，**不要恢复** |
| `trade-dispatch` | `20 */2 * * *` | 已停，**不要恢复** |
| `promote_staging` | `*/10 * * * *` | 保留（纯库内合并，不发网络请求） |
| `trade-reconcile-tick` | `*/20 * * * *` | **11-10 新增，见下文** |

出发点没错：2026-10 计费周期行情快照被触发 **1139 次 / 10 天**，Egress
19.928 / 5 GB，Free 档 Fair Use 被击穿，REST 网关返回 402。但副作用是
**唯一不依赖 GitHub cron 的时钟也没了**。`gh run list` 实证：

- 11-06 还有每 5 分钟规律的 `workflow_dispatch`（pg_cron 在跑）；
- 11-08 起只剩零星手动 / 看门狗派发；**11-10 盘中 13:00~15:13 UTC
  `行情快照` 一次 `schedule` 都没触发**；
- 11-10 仅有的两次 `schedule` 落在 `03:35` / `03:49 UTC`，是前一日
  `21:17` / `21:50` 收盘任务**延迟约 6 小时**到达。

GitHub Actions 免费版 scheduled events 是**尽力而为**，延迟 / 丢弃无法通过
仓库内配置消除。而看门狗（原唯一自愈入口）自己也是 schedule 触发 ——
被兜底者与兜底者同时哑火，是典型共模故障。

## 二、现在线上的四层触发源（v6.11）

| 层 | 触发源 | 频率 | 是否依赖 GitHub cron |
|---|---|---|---|
| 1 | `reconcile.yml` 的 `workflow_run`（行情快照失败 / 备库同步 / 网页发布 / 看门狗完成） | 上游跑完即触发 | 否 |
| 2 | `reconcile.yml` 独立错峰 `schedule` | 每小时 `:17,:47` + 收盘 `22:25` | 是（多条相互独立） |
| 3 | `push` / `workflow_dispatch` | 随时 | 否 |
| 4 | **Supabase pg_cron → GitHub API → 对账** | **每 20 分钟** | **否（平台外时钟）** |

判定逻辑统一在 `reconcile_sessions.py`：**不问「现在几点」，只问「浏览器真正
读到的那份 quotes 里有没有应有的场次」**。缺就补，cron 迟到数小时照样补齐；
数据已齐则零动作。对账只读浏览器同源的 **Turso 公开端点**，不连 Supabase、
不消耗 egress。

## 三、第 4 层的实际配置（已实测）

线上只读探查（`heartbeat-setup.yml` → `inspect`，运行 `42850361187`）结论：

```
extensions: pg_cron, pg_net, supabase_vault   ← 三者均可用
cron.job（安装前）: promote_staging  */10 * * * *  active
vault: gh_dispatch_token                       ← 仍在有效期
probes: repository_dispatch = 403
        workflow_dispatch   = 204             ← 可用
```

**因此心跳走 `workflow_dispatch` 而非 `repository_dispatch`**：
Vault 里的 fine-grained PAT 只有 `actions:write`，没有 `contents:write`，
而 `POST /repos/{owner}/{repo}/dispatches` 需要后者。`external_heartbeat.py`
会自动探测并降级，无需人工判断；将来换成有 `contents:write` 的 PAT，
下一次 `enable` 会自动优先切回 `repository_dispatch`。

安装 / 重装（`42850731333` / `42851393635`；重装会 unschedule 再 schedule，
jobid 会变，以 `cron.job` 实际记录为准）：

```
jobid=8  job=trade-reconcile-tick  schedule=*/20 * * * *  active=true
mode=workflow_dispatch  body={ref: main, inputs: {source: pg-cron}}
```

**自动拍点闭环实证（16:20 UTC，不是手动探测）**：

```
cron.job_run_details  16:20:00.257  status=succeeded  "1 row"
net._http_response    16:20:00.356  status=204
GitHub run            16:20:02  「场次对账 · pg-cron」 42853461788  success
  └ 对账判定 need_action=false「盘中 2026-11-10 快照已在线」，零派发
```

`verify`（`42853618466`）同时确认 `command_contains_plaintext_token=false`、
`command_references_vault=true`；CI 内 `--health` 输出
`{"healthy": true, "age_min": 0}`。即：数据库时钟按时发拍 → GitHub 收到 →
对账确认数据已齐 → 不多做一次采集，整链无冗余动作。

- **只唤起场次对账**，绝不派发采集 / 净值 / 回填；单次出站约 300 字节，
  72 次/天，与 11-06 每 5 分钟拉整条采集链的高频作业完全不同。
- PAT 只以 `vault.decrypted_secrets` 子查询的形式出现在 `cron.job.command`，
  明文不进仓库、不进日志、不进运行输出；CI 临时数据库口令用完即删。
- `reconcile.yml` 的 `run-name` 带 `source`，所以每次心跳运行的标题是
  `场次对账 · pg-cron`，可与手动派发区分。

## 四、操作手册（全部通过 GitHub Actions，无需 SQL Editor）

```bash
# 只读探查：扩展、cron.job、Vault 名称、两个端点的实测状态码
gh workflow run heartbeat-setup.yml -f action=inspect

# 幂等安装 / 重建（间隔硬限 5..60 分钟，默认 20）
gh workflow run heartbeat-setup.yml -f action=enable -f tick_minutes=20

# 查看作业状态、pg_cron 执行明细、pg_net HTTP 回执、最近心跳运行
gh workflow run heartbeat-setup.yml -f action=verify

# 摘除（只摘 trade-reconcile-tick，不碰 promote_staging）
gh workflow run heartbeat-setup.yml -f action=disable
```

`heartbeat-setup.yml` **只能手动 workflow_dispatch**，没有 schedule，
不会随 cron 误改线上调度。换机或重建时，只需在 Actions 里跑一次 `enable`。

`external_heartbeat.py --health` 已挂进 `reconcile.yml`：每次对账顺带检查
最近一次标题含 `pg-cron` 的运行，超过 90 分钟未到达就输出
`::warning::平台外心跳疑似失联`，**只告警不阻断**主流程。

## 五、凭据轮换与失效路径

| 项 | 位置 | 说明 |
|---|---|---|
| GitHub PAT | Supabase Vault `gh_dispatch_token` | 旧文档记录有效期至 **2027-01-26**；到期后 `inspect` 会显示两个端点 401，`enable` 会拒绝安装 |
| Supabase PG 口令 | GitHub Secret `SUPABASE_DB_PASSWORD` | 安装 / 检查时注入，临时文件 `output/_supabase_dbpass.txt` 用后即删 |
| 额度档 | `egress_budget.py` | 心跳不消耗它；补采仍走 `--critical` 白名单 |

轮换 PAT：在 GitHub 生成新 PAT（推荐加 `contents:write` 以便用
`repository_dispatch`），到 Supabase Vault 覆盖同名 `gh_dispatch_token`，
再跑一次 `enable`。**不要把 PAT 写进仓库或本文件。**

心跳可能失效的原因：Supabase 免费项目暂停、Vault token 过期、`pg_net`
background worker 停用、GitHub API 全局不可达。任一情况下，前 3 层触发源
仍然工作，`--health` 会在 GitHub 侧留下 warning，`verify` 可查
`cron.job_run_details` 与 `net._http_response` 定位。

## 六、边界（不夸大）

- GitHub **整体故障期间**，pg_cron 发出的请求同样会失败；本方案的价值是
  **GitHub 恢复后最多 20 分钟必然有下一拍到达**，不依赖 GitHub 自己的
  调度器恢复，随后状态驱动对账会补齐 20 小时窗口内的缺失场次。
  它不能「在 GitHub 宕机时继续产出数据」，任何方案都做不到。
- GitHub cron 仍不保证字面准点；可靠性来自
  「任意一次运行都对账 → 缺什么补什么 → 幂等去重 → 失败重试」，
  而不是某一条定时器更准。
- `collect-full.yml` / `collect-ci.yml` / `backfill-prices.yml` 的周期失败
  属历史回填链路（数据源限流 / 付费墙 / 完整历史门禁），与日更和心跳无关。
