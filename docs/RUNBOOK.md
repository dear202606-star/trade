# 运维手册（RUNBOOK）

## 1. 日常检查（1 分钟）

```bash
python selftest.py          # 78 项门禁，必须全绿（含口径 B 专项）
```

页面看三处：
- 总览底部：「净值 MM-DD HH:MM（北京）」是否为今天
- 策略页：「2026 年策略收益」与账户页一致
- 有 `⚠ 数据偏旧` 提示则排查行情源

## 2. 数据更新链路

| 触发 | 频率 | 说明 |
|---|---|---|
| Supabase `trade-dispatch-market` | 盘中每 5 分钟 | 13–21 UTC 工作日 |
| Supabase `trade-dispatch` | 每 2 小时 | 非盘中兜底 |
| 手动 | 随时 | Actions → quotes → Run workflow |

一次运行的步骤：`quotes → monthly_pick(增量) → equity → preview → variant_b(B) → equity(B) → strategy_history(锁定) → audit_b → site_build → selftest → 部署`

## 3. 常见故障

| 现象 | 排查 | 处理 |
|---|---|---|
| 页面数字不变 | Actions 是否绿；`docs/data/quotes.json.ts` 是否更新 | 手动触发 workflow |
| `selftest` 失败 | 看输出哪一项 | 修复后重跑；不要跳过 |
| 持仓与锁定不一致 | `output/official_locks.json` | **不要**改锁定；先查 `monthly_pick` 是否用错成分表 |
| 某只票价格异常（断崖） | 是否拆股 | 在 `output/splits.json` 加 `[ticker, [[ex_ts, ratio]]]`，重跑 |
| 行业显示 Unknown | `static/sector_overrides.json` 缺该票 | 补 GICS 行业 |
| 页面白屏 | 浏览器控制台 | 多为 JS 语法错误：`node --check` 校验 |

## 3.5 年度结转（全自动，无需人工）

每年 12 月最后一个交易日收盘后自动发生，无需任何操作：

| 环节 | 自动行为 |
|---|---|
| 选股 | 排名基准切到当年 12 月，算出次年 3 只 |
| 锁定 | official_locks.json 自动写入（含 hash、日期） |
| 净值 | equity.base_date 切到 12-31，曲线从 1.0 重算 |
| 归档 | 次年 1 月 1 日起，上一年进入年度表 |
| 预览 | 自动变成「已定稿」，与正式持仓一致 |
| 标题 | 全部随数据更新（有门禁禁止写死年份） |

自检会验证跨年链路：python simulate_rollover.py（selftest 已内置）。

## 4. 年度换仓（最重要）

时间：**12 月最后一个交易日**。

1. 确认 `monthly_pick.py` 已用「排名日时点成分股」（`members_at`）。
2. 运行 `python monthly_pick.py --rerank` 生成新年度持仓。
3. **核对**：3 只、行业 ≤2 只且 ≤60%、权重和 =1、均为 12 月末成分股。
4. 用 `python verify_signal.py` 独立复算，结论必须为「信号与规则完全一致」。
5. 更新 `output/official_locks.json`（新年度 + `lock_run` + `input_hashes`）。
6. `equity.py → strategy_history.py → site_build.py → selftest.py`。
7. 核对首页「建仓日」已变为新年度建仓日。

## 5. 回测重算（谨慎）

```bash
python strategy_history.py --refresh     # 全量重算，约 2 分钟
```

**会改变已公布数字**。重算后必须：
- 对比 `output/lock_changelog.json` 记录变更
- 更新 `docs/CHANGELOG.md`
- 检查 16 年行业约束零违例

## 6. 密钥与凭据

| 凭据 | 存放 | 轮换 |
|---|---|---|
| GitHub PAT（Supabase 调度用） | Supabase Vault `gh_dispatch_token` | 到期前更新；本地 `output/_pat_token.txt`（gitignored） |
| Supabase service key | GitHub Secrets | 泄露即换 |
| 匿名 key | `docs/data/cloud.json`（公开可读） | 无需轮换 |

## 7. 测试套件

```bash
python ui_test.py http://127.0.0.1:8768/            # 34 项 UI
python e2e_check.py                                 # 20 项线上
python consistency_check.py                         # 14 项跨页口径
python live_audit.py                                # 20 项真实交互
python link_check.py                                # 死链
python verify_signal.py                             # 信号独立复核
```

本地起服务：`cd docs && python -m http.server 8768`

## 8. 发布

```bash
git add -A && git commit -m "..." && git pull --rebase origin main && git push
```

CI 推送后约 1–3 分钟生效；用 `?cb=<时间戳>` 绕开缓存验证。

## 9. 回滚

```bash
git revert <sha> && git push          # 数据与页面一起回退
```

历史锁定（`history_baseline.json`）**不可回退**，只能新增 `lock_version`。

### 9.1 历史基线（防静默漂移）

`output/baseline_b.json` 记录 B 的 16 年逐年收益与累计值，**只在人工确认后才更新**：

```bash
python variant_b.py                       # 历史与基线不一致 → exit 2，流水线打红
BASELINE_UPDATE=1 python variant_b.py     # 确认是数据修正后，显式提升基线
git add output/baseline_b.json && git commit -m "chore: 提升口径B历史基线"
```

`refresh-fixtures.yml` 每月刷新快照后会自动跑 `freeze_check.py`：漂移则开 Issue 并终止，不会静默改写历史。
