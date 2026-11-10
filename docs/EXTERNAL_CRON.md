# 外部定时触发（当前状态与恢复方案）

> **2026-11-10 更正**：本文旧版结论「Supabase 定时触发已配置好、无需任何操作」
> **已经失效**，不要按旧说明操作。

## 一、真实状态：外部时钟在 11-06 被一起停掉了

`sql/124_egress_schedule.sql` 为收敛出口流量，停掉了库内两个 dispatch 作业：

| 作业 | 原频率 | 现状 |
|---|---|---|
| `trade-dispatch-market` | `*/5 13-21 * * 1-5` | **已 unschedule** |
| `trade-dispatch` | `20 */2 * * *`（每小时 :15 那份文档描述的是早期版本） | **已 unschedule** |
| `promote_staging` | 纯库内合并 | 保留，不发网络请求 |

当时的决策是「调度权统一收归 GitHub Actions（有日志、可版本化、可预算门禁）」。
出发点没错：2026-10 计费周期行情快照被触发 **1139 次 / 10 天**，
Egress 19.928 / 5 GB（Free 档 Fair Use 限额被击穿，REST 网关返回 402）。

但副作用是：**唯一一个不依赖 GitHub cron 的准点时钟也没了**。
`gh run list` 实证后续变化：

- 11-06 还有每 5 分钟规律的 `workflow_dispatch`（pg_cron 在跑）；
- 11-07 变成每 2 小时一次；
- 11-08 起只剩零星手动 / 看门狗派发，**11-10 盘中 13:00~15:13 UTC
  `行情快照` 一次 `schedule` 都没触发**；
- 11-10 仅有的两次 `schedule` 落在 `03:35` / `03:49 UTC`，是前一日
  `21:17` / `21:50` 收盘任务**延迟约 6 小时**到达；`备库同步 21:55`
  被拖到次日 `03:26`。

结论：**GitHub Actions 免费版 scheduled events 是尽力而为，延迟 / 丢弃无法
通过仓库内配置消除**。把全部调度权交给它，就形成了「cron 一延迟、采集与
自愈一起哑」的共模故障 —— 因为看门狗（唯一自愈入口）自己也是 schedule 触发。

## 二、当前可靠性架构（已落地，v6.10）

`reconcile.yml` + `reconcile_sessions.py` 把判定从「现在几点」改成「数据齐不齐」，
并用互相独立的触发源破除单点：

| 触发源 | 频率 | 是否依赖 GitHub cron 准点 |
|---|---|---|
| `workflow_run`（行情快照失败 / 备库同步 / 网页发布 / 看门狗完成） | 上游跑完即触发 | 否 |
| 独立错峰 `schedule` | 每小时 `:17,:47` + 收盘 `22:25` | 是（但多条错峰，丢弃相互独立） |
| `push` / `workflow_dispatch` | 随时 | 否 |
| `repository_dispatch: reconcile-tick` | 由外部定时器决定 | **否（平台外时钟）** |

对账只读浏览器同源的 **Turso 公开端点**（不连 Supabase、不消耗 egress），
数据已齐时零动作；缺场次才派发补采，且有 30 分钟冷却 + 同场次最多 3 次
的防自激约束。即使一整天的 GitHub schedule 全被丢弃，只要任意一次运行
（或外部心跳）到达，缺口就会被补齐。

## 三、可选增强：恢复一个「平台外」的心跳

仓库内已经把接口留好，**不需要改代码**。若希望连「GitHub 整体故障」也能兜住，
二选一即可。注意心跳只唤起**对账**，绝大多数轮次零动作，不会重演 egress 事故。

### 方案 A（推荐）：repository_dispatch，完全不碰 Supabase

任何免费外部定时器每 15~30 分钟执行一次：

```bash
curl -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer <具备 actions:write 的 GitHub PAT>" \
  https://api.github.com/repos/dear202606-star/trade/dispatches \
  -d '{"event_type":"reconcile-tick"}'
```

可用 Cloudflare Workers Cron（免费额度充足）、cron-job.org 或本机计划任务。
PAT 只存外部定时器，不进仓库、不进前端。

### 方案 B：Supabase pg_cron + pg_net

前提：Vault 里的 `gh_dispatch_token` 仍在有效期内（旧文档记录到 2027-01-26）。
在 SQL Editor 执行（**低频、幂等**）：

```sql
select cron.schedule(
  'trade-reconcile-tick', '15,45 * * * *',
  $$select net.http_post(
      url := 'https://api.github.com/repos/dear202606-star/trade/dispatches',
      headers := jsonb_build_object(
        'Accept', 'application/vnd.github+json',
        'Authorization', 'Bearer ' ||
          (select decrypted_secret from vault.decrypted_secrets
           where name = 'gh_dispatch_token' limit 1),
        'Content-Type', 'application/json'),
      body := '{"event_type":"reconcile-tick"}'::jsonb
    )$$
);
-- 停用：select cron.unschedule('trade-reconcile-tick');
-- 查看：select jobid, jobname, schedule, active from cron.job;
```

**不要**恢复 `trade-dispatch` / `trade-dispatch-market` 这两个高频派发采集的作业，
那正是 1139 次 / 10 天事故的直接放大器。

## 四、边界（不夸大）

GitHub cron 仍不保证字面准点，本方案也不承诺「某分某秒必跑」。
可靠性来自「任意一次运行都对账 → 缺什么补什么 → 幂等去重 → 失败自动重试」，
而不是某一条定时器更准。外部心跳属于可选的第 4 层冗余，不是必需项。
