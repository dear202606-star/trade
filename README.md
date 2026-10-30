# LEADER QUANT · 美股动量策略与跟踪站点

[![行情快照](https://github.com/dear202606-star/trade/actions/workflows/quotes.yml/badge.svg)](https://github.com/dear202606-star/trade/actions/workflows/quotes.yml)
[![每日全量](https://github.com/dear202606-star/trade/actions/workflows/monthly-pick.yml/badge.svg)](https://github.com/dear202606-star/trade/actions/workflows/monthly-pick.yml)

**站点**：https://dear202606-star.github.io/trade/

一个个人投资决策系统：用「每年换仓一次」的横截面动量策略管住手，用静态站点回答每天唯一重要的问题——
**今天该做什么**。

---

## 1. 策略是什么

| 项 | 规则 |
|---|---|
| 选股池 | 标普 500 成分股（**排名日的时点成分**，防未来函数） |
| 排序 | 过去 12 个自然月复权涨幅 |
| 持仓 | 前 3 名；同行业最多 2 只、该行业合计 ≤60%<br>（三行业各 1/3；两只同行业则 30/30 + 40） |
| 换仓 | 每年 12 月最后一个交易日锁定，次年全年持有 |
| 仓位 | 满仓（跌破 10 月均线减半属于可选风控，**未纳入**回测与净值） |
| 费用 | 不计手续费与滑点（理论净值） |

**已公布业绩（2010–2025，16 年）**：累计 **5059%**，年化 **27.95%**，最深回撤 **-42.6%**，15/16 年正收益。

> 2026 年度持仓（2025-12 锁定）：**WDC 30% / MU 30% / HOOD 40%**
> 当期（2026 年至今）：**+104.72%**（同期标普 +13.26%）

## 2. 站点有什么

四个页面，三层时间视野：

**单页五段式**（一屏纵向滚动，无 Tab、无跳转）：

| 段 | 内容 |
|---|---|
| ① 今天该做什么 | 结论、今日涨跌、大盘开关 |
| ② 当期策略累计表现 | 2026 年收益、本金切换、市值/盈亏/成本/仓位 |
| ③′ 明年可能持仓 | 年初至今涨幅前 3（实时预览，年末才最终确定） |
| ④ 总体策略至今表现 | 2010–2025 累计、年化、最大回撤、胜率、双曲线 |
| ⑤ 历史与回测 | 逐年持仓、收益、回撤 |

命名规范：**全周期**（2010–2025 回测）、**{年份}**（当期）、**今日**（当日涨跌）。同一指标全站只出现一次。

## 3. 架构速览

```
Supabase pg_cron（盘中每 5 分钟）
        ↓ workflow_dispatch
GitHub Actions：行情 → 信号(同月增量) → 净值 → 逐年记录(历史锁定) → 构建 → 自检 → Pages
        ↓
docs/data/*.json  →  静态站点（浏览器零后端）
```

- **历史回测一次算好并锁定**，之后每次运行 1 秒返回，不再重算。
- **当期**每日更新；**今日涨跌**来自实时快照。
- 详细设计见 [ARCHITECTURE](docs/ARCHITECTURE.md)，口径定义见 [DATA_DICTIONARY](docs/DATA_DICTIONARY.md)。

## 4. 本地运行

```bash
python -m pip install -r requirements.txt
python quotes.py            # 行情快照
python monthly_pick.py      # 信号（同月自动走增量）
python equity.py            # 当期净值
python strategy_history.py  # 历史（已锁定则 1 秒返回）
python site_build.py        # 构建 docs/
python selftest.py          # 部署前自检（48 项硬校验）
python simulate_rollover.py # 跨年仿真：任意日期推导全链路
```

预览站点：`cd docs && python -m http.server 8768`

## 5. 质量保障

| 层 | 机制 |
|---|---|
| 数据源 | 新浪（主）+ Yahoo（交叉核对，不可达时降级并记录） |
| 公司与行业 | `output/splits.json`（合并写入，不被覆盖）、`static/sector_overrides.json`（退市票行业） |
| 自检 | 48 项硬校验：无 NaN、3 只持仓、权重和 1、行业 ≤60%、曲线=逐年复利、持仓锁定一致、持仓为排名日成分股、账户=净值 |
| 前端 | 页面内不变量校验（市值=Σ股数×价、盈亏=市值−成本），失败即页面提示 |
| 测试 | `ui_test.py`(34) / `e2e_check.py`(20) / `consistency_check.py`(14) / `live_audit.py`(20) |

## 6. 文档

| 文档 | 内容 |
|---|---|
| [PRD](docs/PRD.md) | 产品定位、用户场景、功能清单、验收标准 |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | 数据流、组件、调度、失败模式 |
| [DATA_DICTIONARY](docs/DATA_DICTIONARY.md) | 每个字段的含义、口径、来源 |
| [GITHUB](docs/GITHUB.md) | 工作流、并发、门禁、Issue 策略、Supabase 定时 |
| [RUNBOOK](docs/RUNBOOK.md) | 日常检查、故障处理、换仓与 Token 轮换 |
| [CHANGELOG](docs/CHANGELOG.md) | 版本与口径变更（含业绩修正记录） |
| [KNOWN_ISSUES](docs/KNOWN_ISSUES.md) | 已知问题与待修项 |
| [DATABASE](docs/DATABASE.md) | 数据库表结构、权限、迁移与验收 |
| [DB 迁移方案](docs/ARCH_PLAN.md) | 数据库作为唯一事实来源（`db-arch` 分支） |

## 7. 目录结构

```
├─ 数据管线      quotes.py  monthly_pick.py  equity.py  strategy_history.py
├─ 数据层        market_data.py（双源+复权）  datafeed.py（缓存/成分/行业）
├─ 站点构建      site_build.py  dashboard.py（保留，页面已并入单页）
├─ 校验          selftest.py（硬门禁）  quality_gate.py  crosscheck.py
├─ 测试          ui_test.py  e2e_check.py  consistency_check.py  live_audit.py  link_check.py
├─ 静态资源      site_src/（html/css/js）  static/（成分表、行业覆盖）
├─ 产出          output/（锁定数据、报告）  docs/（站点 + 文档）
└─ 自动化        .github/workflows/（quotes / monthly-pick / e2e / data-check）
```

## 8. 免责声明

本项目为个人研究与记录，**不构成投资建议**。历史回测不代表未来收益；所有净值为理论值，
不含手续费、滑点与税。
