# 数据更新调度（可靠性方案）

## 结论：用 Supabase 定时触发，不依赖 GitHub 原生 schedule

GitHub 的 `schedule` 是"尽力而为"的低优先级队列：实测延迟 3～4.5 小时，
平台故障时停摆半天（2026-10-28 全天只跑了 4 次，本应 12 次）。

现在改成三层：

| 层 | 机制 | 频率 | 可靠性 |
|---|---|---|---|
| 1 | **Supabase pg_cron → GitHub API** | 每小时 :15 | 高（±1 分钟） |
| 2 | GitHub 原生 schedule | 每 2 小时 × 2 组错峰 | 中（延迟不可控） |
| 3 | 任何一次 `git push` | 即时 | 高 |

三条链路互相兜底，任一条正常数据就会更新。

## 一、Supabase 定时任务（主链路）

已配置好，无需任何操作：

- 扩展：`pg_cron`、`pg_net`、`vault`
- 任务名：`trade-dispatch`，表达式 `15 * * * *`（每小时 15 分）
- Token：存在 **Supabase Vault**（加密），不落任何业务表
- 实测：请求返回 **HTTP 204**，GitHub 上出现 `workflow_dispatch` 运行并成功

查看/修改（Supabase → SQL Editor）：

```sql
-- 看任务
select jobid, jobname, schedule, active from cron.job;

-- 看最近触发记录与结果码（204 = 成功）
select id, status_code, created
from net._http_response order by id desc limit 10;

-- 暂停
select cron.unschedule('trade-dispatch');
```

`output/_supabase_cron.sql` 是完整、可重复执行的配置脚本。

## 二、为什么不用 cron-job.org

同样可行，但需要：注册账号 → 邮箱验证 → 存一个长期 token 在第三方。
Supabase 方案少一个外部依赖、token 存在自家 Vault（加密）、零成本。

## 三、注意事项

- Supabase 免费项目**连续 7 天无活动会被暂停**；本任务每小时产生一次
  HTTP 记录，属于持续活动，正常不会暂停。若被暂停，重新打开项目后任务自动恢复。
- Token 有效期到 **2027-01-26**。到期前 GitHub 会邮件提醒，换新后执行：
  ```sql
  select vault.update_secret(
    (select id from vault.secrets where name = 'gh_dispatch_token'),
    'github_pat_新的token');
  ```
- 站点页面会显示真实抓取时间；超过 26 小时未更新时顶部出现黄色警告。
- 触发的工作流是 `quotes.yml`，它内部已完成：行情 → 信号增量 → 净值 →
  构建 → 自检 → 部署，全程约 15 秒。
