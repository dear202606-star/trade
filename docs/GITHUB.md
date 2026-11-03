# GitHub 自动化说明

> 项目**没有服务器**：一切由 GitHub Actions + Pages + Supabase 定时触发组成。
> 本文说明每个工作流做什么、怎么改、出问题怎么查。

## 1. 工作流一览

| 文件 | 名称 | 触发 | 作用 |
|---|---|---|---|
| `.github/workflows/quotes.yml` | 行情快照 | Supabase 定时（盘中每 5 分钟）+ 手动 | 抓行情 → 信号（同月增量）→ 净值 → 预览 → 逐年记录 → 构建 → 自检 → 部署 |
| `.github/workflows/monthly-pick.yml` | 每日全量更新 | 每日定时 + 手动 | 同上的完整版，额外做生存者偏差审计、落盘选股快照 |
| `.github/workflows/e2e.yml` | 线上 E2E 校验 | 工作流完成后 + 定时 | 对**线上真实 URL** 做端到端校验（`e2e_check.py`，历史脚本，主要覆盖口径 A） |
| `.github/workflows/data-check.yml` | 数据质量核对 | 每日 | 双源一致率、覆盖率报表 → 只记录，不再开 Issue |

## 2. 并发与部署

```yaml
concurrency:
  group: trade-pipeline      # quotes / monthly-pick / data-check 共用
  cancel-in-progress: false  # 排队而不打断
```

- 同一时刻只有一个写状态的工作流在跑，避免互相覆盖。
- 部署走 Pages 产物（`actions/upload-pages-artifact`），**不依赖 git 提交**。
- 「提交流水线状态」步骤失败只告警，不阻断部署。

## 3. 部署前门禁

`selftest.py` 是唯一的发布闸：78 项检查不过就不上传产物（其中 20+ 项专管口径 B）。

覆盖：数据无 NaN、持仓 3 只、权重和 1、行业 ≤60%、曲线=逐年复利、
持仓锁定一致、持仓为排名日成分股、年度结转规则、跨年链路自洽、
页面无写死年份/日期、健康状态存在。

## 4. Issue 策略

| 场景 | 是否开 Issue | 说明 |
|---|---|---|
| 流水线失败 | ✅ | `quotes.yml` 与 `monthly-pick.yml` 均有「失败时开 Issue」，label `pipeline` |
| 口径 B 历史漂移 | ✅ | `refresh-fixtures.yml` 检测到漂移时开 Issue，label `history-drift` |
| 月度信号推送 | ❌ 已停用 | 站点是唯一信息源，重复推送已移除 |
| 数据质量告警 | ❌ 已停用 | CI 内 Yahoo 常不可达，一致率天然偏低，噪音大于价值 |
| 历史遗留 | 3 个 open | `#1 月度信号` `#2 数据质量` `#3 流水线失败`，均已无实际意义，**待手动关闭**（当前 PAT 无 issues 权限） |

## 5. 密钥与权限

| 名称 | 用途 | 位置 |
|---|---|---|
| `gh_dispatch_token` | Supabase 调 GitHub API 触发工作流 | Supabase Vault |
| `GITHUB_TOKEN` | 工作流内部使用（Pages 部署、提交） | Actions 自动注入 |
| 本地 `output/_pat_token.txt` | 手动触发/查询运行 | 本机（已 gitignore） |

> 细粒度 PAT 需勾选 **Issues: Read and write** 才能用 API 关闭 Issue；当前 token 只有 actions/repo 读权限。

## 6. 常用命令

```bash
# 手动触发一次完整更新
gh workflow run quotes.yml -R dear202606-star/trade

# 看最近运行
gh run list -R dear202606-star/trade -L 5

# 看某个运行的失败步骤
gh run view <run_id> -R dear202606-star/trade
```

## 7. Supabase 定时

| job | 时间 | 作用 |
|---|---|---|
| `trade-dispatch-market` | `*/5 13-21 * * 1-5`（UTC） | 盘中每 5 分钟触发 |
| `trade-dispatch` | `20 */2 * * *` | 其余时段每 2 小时兜底 |

函数：`public.dispatch_trade_pipeline()`，token 存 Vault `gh_dispatch_token`。

## 8. 失效自愈

- 数据超过 26 小时未更新 → 页面顶部红条「行情已 N 小时未更新」。
- 部署被自检拦下 → Actions 里能看到具体失败项，修完重新触发即可。
- Pages 偶发 503 → 等待 1–2 分钟重试，不影响数据。

## 8. 行情数据源（2026-11 起）

| 项 | 内容 |
|---|---|
| 源 | Alpha Vantage TIME_SERIES_MONTHLY_ADJUSTED（拆股+分红复权，含退市票，最早 1999） |
| 额度 | 免费 25 次/天 |
| 回填 | etch_prices.py 每天补 22 只，约 40 天覆盖 1209 只 |
| 产物 | static/prices/<TICKER>.json（提交入库，可离线复现） |
| 工作流 | .github/workflows/backfill-prices.yml（每天 03:10 UTC） |
| 验收 | alidate_prices.py：NVDA 2021 +125% / NVDA 2024 +171% / DUK 2012-07 ≈0 / AMCR 2026 合理 / 覆盖率 ≥95% / 连续性 |
| 切换闸门 | 覆盖率 <95% 时整体回退旧快照，避免新旧混算 |
| Secret | ALPHAVANTAGE_API_KEY |

| 第二源 | EODHD（免费仅近 13 个月）：每日校验持仓与预览前 5 名的月度收益，偏差 >2% 自动覆盖并重接历史 |
| Secret | ALPHAVANTAGE_API_KEY、EODHD_API_TOKEN |
