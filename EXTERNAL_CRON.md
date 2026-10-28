# 外部定时触发（准点保障）

GitHub 原生 schedule 是"尽力而为"：实测会延迟 3~4.5 小时，平台故障时停摆半天。
本方案用免费的 [cron-job.org](https://cron-job.org/) 每小时准点调用 GitHub API
触发 `quotes.yml`（该工作流内部已包含：行情 → 信号增量 → 净值 → 构建 → 自检 → 部署，
全程约 15 秒），实现 ±1 秒级准点。

GitHub 自带的 schedule 保留作为备用（每 2 小时 × 2 组错峰），两条链路互为兜底。

---

## 一、创建 GitHub Token（一次性，约 3 分钟）

1. 打开 https://github.com/settings/personal-access-tokens/new
2. 按下面填写：
   - **Token name**：`cron-job-org-trigger`
   - **Expiration**：`90 days`（到期换新，防长期泄露）
   - **Repository access**：`Only select repositories` → 选 `dear202606-star/trade`
   - **Permissions → Repository permissions → Actions**：`Read and write`
3. 点 **Generate token**，复制 `github_pat_...` 开头的字符串（只显示一次）

> 这个 Token 只有「触发本仓库 Actions」的能力，不能改代码、不能读私有内容。
> 泄露的后果仅是多跑几次流水线，随时可在同一页面 Revoke 作废。

## 二、在 cron-job.org 创建定时任务（约 3 分钟）

1. 打开 https://cron-job.org/ 注册并登录（邮箱验证）
2. 点 **Create Cronjob**，填写：

| 配置项 | 值 |
|---|---|
| Title | `trade-数据更新` |
| URL | `https://api.github.com/repos/dear202606-star/trade/actions/workflows/quotes.yml/dispatches` |
| Request method | `POST` |
| Request body | `{"ref":"main"}` |

3. **Headers**（点 Add header 加两行）：

| Header | 值 |
|---|---|
| `Authorization` | `Bearer github_pat_你的Token` |
| `Content-Type` | `application/json` |

4. **Execution schedule** → Cron 表达式：
   ```
   15 * * * *
   ```
   （每小时的 15 分触发；与 GitHub 自己的调度错开，避免同时排队）
5. **Notifications**：勾选"任务失败时发邮件"，收件邮箱填你的常用邮箱
6. 点 **Create** 完成

## 三、验证（1 分钟）

创建后等下一个整点 15 分，或直接打开刚创建的 cronjob 点 **Run now**：

- cron-job.org 显示 `Status 204`（GitHub 触发成功的标准返回码，不是 200）
- 打开 https://github.com/dear202606-star/trade/actions 能看到新的
  `行情快照` 运行记录，触发方式为 `workflow_dispatch`
- 站点数据时间会更新：https://dear202606-star.github.io/trade/

## 四、维护与安全

- **Token 到期**：GitHub 会在到期前 7 天发邮件提醒；换新 Token 后改 cron-job.org 里
  Headers 的值即可
- **收到失败邮件**：先看 https://www.githubstatus.com/ 是否平台故障；若长期失败，
  检查 Token 是否过期/被撤销
- **不需要了**：删掉 cron-job.org 任务 + 在 GitHub 撤销 Token，两步即可完全退出

---

## 附：Token 已经创建好

- 名称：cron-dispatch
- 有效期：到 2027-01-26（到期前 GitHub 会邮件提醒）
- 权限：仅 dear202606-star/trade 仓库的 **Actions: Read and write**
- 已验证：调用 workflow_dispatch 返回 **204**（成功触发了一次真实运行）
- 密文存放：output/_pat_token.txt（已在 .gitignore 中，不会提交）

**你只需要做：注册 cron-job.org → 新建任务（参数见上文）→ 把 output/_pat_token.txt
里的整串内容填到 Authorization 头，前面加 Bearer （注意有一个空格）。**
