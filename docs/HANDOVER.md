# 项目状态与移交（2026-11-01）

> 这份文件回答三个问题：**做到哪一步了、还差什么、下一个人接手该从哪开始。**

## 一、已经做完的（可验证）

### 1.1 数据库重构：17 张表 → 12 张表 + 11 个视图

| 阶段 | 动作 | 验收证据 |
|---|---|---|
| P0 | 权限止血：17 表 anon 只读、`prices_monthly` 开 RLS、撤销默认写权限 | 实测改/删返回 401 |
| P1 | 守卫函数收归 `sql/`，脚本不再抢管 | 跑完 `lock_stage --check` 后守卫仍是强版本 |
| P4 | `series_selected` 11.7 万行实体表 → 视图（决策表 `series_pick` 847 行） | 逐行 diff = 0 |
| M1 | `source_registry` + `data_sources` → `data_source` + `data_source_secret`（凭据分离） | anon 读凭据表 = False |
| M2 | `sectors` 并入 `symbols`；`members` 补外键与 CHECK | 孤儿成分 = 0 |
| M5 | `locks` + `locked_symbols` → `data_lock`（append-only + supersede） | 5 种攻击全部拦截 |
| M6 | `fetch_runs` + `validations` + `work_plans` → `sys_event(kind)` | 三类行数一致 |
| M7 | `ingest_staging` 七条校验门槛 | 6 个正负样本全部按预期 |
| 清理 | 9 张旧表先备份 1,364 行再 drop | 残留 legacy = 0 |

### 1.2 质量保证：六层闸门，每层都被验证过"能失败"

| 层 | 机制 | 它曾经拦住过什么 |
|---|---|---|
| L1 入口 | 暂存区 7 条门槛 | AAPL 写 9999 → 拒收 |
| L2 单票 | 每月全收益 + 无跳变才锁 | 5 种篡改攻击全部拦截 |
| L3 数值 | 原始价+分红独立复算 | 抓出 AIV 分拆口径问题 |
| L4 完整 | 标的-年缺口口径 | 纠正"81.1% 全收益"的假象 |
| L5 发布 | selftest 85 + audit 158 + schema 25 | **拦住了一次 +60% 的数字跳变** |
| L6 监控 | 锁校验 + 实时层 + 看门狗 | 抓到 AAPL/MSFT 指纹漂移 |

### 1.3 数据现状

| 指标 | 值 |
|---|---|
| `prices_monthly` | 351,889 → 现在约 40 万行 |
| 口径 | 全收益 108,742 行（75%） |
| 已锁标的 | 963 只（指纹全部校验通过） |
| 覆盖率（标的-年） | **83.3%**（6,694 / 8,032） |
| 缺口 | 1,338 格；**99% 是退市票** |
| 分红事件 | 8,573 条 / 151 只（持续采集中） |
| 全收益独立复算 | 85 通过 / 0 超阈值 / 50 跳过（拆股类） |

### 1.4 实时层

- 盘中每 5 分钟派发：**196 次运行 / 196 次成功**
- `signal.json` / `equity.json` 新鲜度自动校验
- 四个 pg_cron 任务全部健康

## 二、还差什么（以及为什么）

### 2.1 唯一剩余工作：退市票补数

```
1,338 格缺口 → 1,288 格在退市票上 → 只有 Alpha Vantage 有数据
25 次/天 → 约 7 天
```

已做的所有努力：
- 30+ 个数据源实测（Yahoo/stockanalysis/Nasdaq/Stooq/Barchart/WSJ/FT/CNBC/
  东财/雪球/网易/FMP/TwelveData/Polygon/EODHD/Tiingo/Marketstack）
- HuggingFace 数据集（HexQuant/paperswithbacktest/AYUSHKHAIRE/no-ry 等）
- GitHub 数据集仓库
- 互联网档案馆（Wayback）
- 境外后缀（.MX/.DE/.L/.TO）、翻译中转、指纹伪装、Cookie 复用

**结论**：免费世界里没有第二家提供美股退市票的全收益历史。

### 2.2 100% 不可达的部分

2010 年前破产、从未被数字化的小盘成分，任何免费源都没有记录。
现实目标应是 **≥95% 且每个缺口可解释**，而非 100%。

## 三、接手第一件事

```bash
# 1. 看现在卡在哪
python coverage_pit.py          # 缺口按年份
python -c "import psycopg2,io; ..."  # 或直接 select * from v_collect_summary

# 2. 触发补数（AV 恢复后）
gh workflow run daily-backfill.yml

# 3. 全量验收
python selftest.py && python audit_b.py && python schema_check.py
python verify_locks.py && python verify_tr.py && python realtime_check.py
```

**关键约束（不要违反）**：
1. 数据库是唯一数据源，不许读本地 CSV（`allocate_work` 曾犯过，已修）
2. 不许静默回退到文件
3. 锁表只能 append + supersede，不能 UPDATE
4. DDL 只写在 `sql/` 里，脚本不许 `create or replace function`
5. 每个权限/DDL 变更后面必须跟断言

## 四、已知的坑（踩过并修好的）

| 坑 | 症状 | 教训 |
|---|---|---|
| 选源只看口径不看覆盖 | 选了 13 个月的 EODHD，丢了 189 个月的新浪 | 覆盖优先，口径其次 |
| `series_pick` INSERT 列数不符 | CI 每 6 小时全失败，拉了的数据白拉 | 构建失败必须让管线停下 |
| 看板用 `count(*)` 当月份数 | 171 > 168 的荒谬判定 | 多源表必须 `count(distinct month)` |
| 基线漂移检查太松 | +60% 的数字静默上线 | 漂移必须人工确认才能提基线 |
| `relay_source` 没导入 `DB` | 所有采集运行都没写进台账 | 被 try/except 吞掉的错误最危险 |

## 五、数字口径提醒

线上展示的是 **2012-2025（14 年）+3678%**，历史卡片展示 2013-2025（13 年）。
两个数字不同是正常的（起点不同），不要混用。

2010-2011 因池子过小（79/93 只）不计入官方记录。
