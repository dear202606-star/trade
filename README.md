# 美股排名动量策略 · 量化网站

[![月度选股](https://github.com/dear202606-star/trade/actions/workflows/monthly-pick.yml/badge.svg)](https://github.com/dear202606-star/trade/actions/workflows/monthly-pick.yml)
[![行情快照](https://github.com/dear202606-star/trade/actions/workflows/quotes.yml/badge.svg)](https://github.com/dear202606-star/trade/actions/workflows/quotes.yml)

对「买入上一年涨幅前 N 名」的横截面动量策略做系统回测，比较 11 种持有模型，并落地成一个可录入持仓、实时看盈亏、自动给下单指令的网站。

## 🌐 网站

**https://dear202606-star.github.io/trade/**

| 页面 | 作用 |
|---|---|
| [总览](https://dear202606-star.github.io/trade/) | 本期信号、你的组合概况、数据质量 |
| [我的持仓](https://dear202606-star.github.io/trade/portfolio.html) | 录入总资金与买入记录 → 实时市值/盈亏/占比/目标偏离 |
| [行动计划](https://dear202606-star.github.io/trade/plan.html) | 按你的实际持仓算出要下的每一笔单：买哪只、几股、多少钱 |
| [信号看板](https://dear202606-star.github.io/trade/signals.html) | 历史信号、开关状态、月度记录 |
| [数据质量](https://dear202606-star.github.io/trade/analysis.html) | 两源交叉核对结果、隔离清单、回测结论 |
| [策略报告](https://dear202606-star.github.io/trade/report.html) | 完整回测报告（含实时信号卡） |

## 🏗 架构

```
GitHub Actions（定时）
  ├── monthly_pick.py   月度选股 → signal_history.json
  ├── quotes.py         行情快照 → docs/data/quotes.json（508/510 只）
  ├── site_build.py     构建站点 → docs/
  └── deploy-pages      发布到 GitHub Pages
        ↓
   静态站（无后端、无数据库、零费用）
        ↓
   浏览器：localStorage 存持仓，fetch 同源 JSON 取行情
```

**为什么这么设计**：

- **不要后端**：持仓是私密数据，存浏览器本地最安全，也省掉服务器成本
- **不要数据库**：本站数据量小（一份 JSON 42 KB），GitHub Pages 直接当 CDN
- **不要行情 API key**：免费行情源普遍不开 CORS，浏览器直连会被拦，所以放服务端批量抓、发布成同源 JSON
- **行情刷新**：美股盘中每 30 分钟一次（`quotes.yml`），延后约 15 分钟

## 🔔 自动通知

每月运行后会自动在 [Issues](https://github.com/dear202606-star/trade/issues) 的「📊 月度信号看板」下追加一条评论，内容包括当期持仓、建议权重、大盘开关、数据质量与上期变化。

GitHub 会对 Issue 参与人发送**邮件通知**（手机 App 可开推送）。想改通知方式：右上角 Watch → Custom → 勾选 Issues。

> **每月自动选股已上线**：查看最新名单 → [`output/monthly/latest.md`](output/monthly/latest.md)

## 📒 实盘跟踪

从首期信号开始记账（`output/track_record.json`）：每期按正式持仓的权重 × 仓位计算组合收益，与标普 500、回测预期（年化 29.4%）对照。页面「实盘跟踪」页签可看：

| 指标 | 含义 |
|---|---|
| 策略累计 | 按信号真实执行的组合收益 |
| 标普 500 同期 | 基准 |
| 回测预期 | 29.4% 年化按已过月份折算 |
| 与回测的偏差 | 判断策略是否按回测兑现 |

## 🧮 数据质量

**一句话：同一套规则，换数据源必须给同一答案。** 为此做了四件事。

### 1. 口径归一

| 差异 | 处理 |
|---|---|
| 复权口径 | 统一为**仅拆股**。Yahoo 的 `adjclose` 含分红，会抬高分红股收益，弃用 |
| Yahoo `close` 语义 | 实测发现它**已按拆股前复权**（KLAC 10:1 拆股后，拆股前月份显示 192 而非 1920），因此**不再二次复权** |
| 新浪价格 | 原始价 × Yahoo 权威拆股表 = 后复权，与 Yahoo 只差一个常数，收益完全一致 |
| 符号格式 | `BRK.B` ↔ `BRK-B` 自动转换 |

### 2. 拆股表入库共享

`output/splits.json`（502 只，含 AAPL 2:1/7:1/4:1、TSLA 5:1/3:1、KLAC 10:1）由 CI 生成并提交，
**本地与 CI 用同一张表**，离线跑也不会漂。

### 3. 两源交叉核对 + 隔离

每只票两源都取，比较最近 13 个月的**月度收益序列**：

- 12 个月累计收益偏差 ≤ 1.5%
- 任一月度收益偏差 ≤ 2%

超阈值 → **隔离该票，不参与排名**（绝不静默采用其中一个源）。当前结果：

```
两源一致 486/496 = 98.0% ｜ 隔离 10 只 ｜ 失败 2 只
```

剩下 10 只是公司行为（分拆上市、换股）导致两源编码不同，已隔离。

### 4. 持续保障

| 机制 | 说明 |
|---|---|
| `data-check.yml` | 每月 1 日自动跑两源核对，产出 `output/data_quality.md` |
| `quality_gate.py` | 一致率 < 95% 时 CI 直接失败（触发 GitHub 失败邮件） |
| 自动开 Issue | 不达标时自动创建/追加 `data-alert` Issue |
| 信号通知附带 | 每月 Issue 里固定带一行「数据质量：两源一致 x/y」 |
| `survivorship.py` | 逐年统计标普 500 成分股覆盖率（2008 年 93% → 2021 年后 100%，累计仅缺 38 只） |


| 年份 | 2008 | 2010 | 2012 | 2015 | 2021+ |
|---|---|---|---|---|---|
| 覆盖率 | 93.0% | 96.0% | 98.6% | 99.6% | 100% |

累计仅缺 **38 只**，主要是雷曼（LEHMQ）、贝尔斯登（BSC）、华盛顿互惠等 2008–2010 倒闭的金融股。对"选出赢家"的影响集中在 2009–2010 反弹年，远小于早前"打 7–8 折"的估计。

## 核心结论

| 模型 | 年化 | 18年累计 | 最大回撤 | 赚钱年数 |
|---|---|---|---|---|
| 只买第 1 名 | 37.2% | 280× | −60.7% | 14/18 |
| 前 3 名 | 33.2% | 167× | −44.6% | 17/18 |
| **前 3 名 + 趋势减仓** | **29.4%** | **100×** | **−32.5%** | **17/18** |
| 前 10 名 | 20.0% | 26× | −31.8% | 14/18 |
| 标普 500 | 12.7% | 8.4× | −24.8% | 14/18 |

**首选模型**：每年 12 月末按过去 12 个月涨幅排序，买入标普 500 成分股前 3 名（等权），持有 1 年；当标普 500 上月收盘跌破 10 月均线时，仓位减半。

## 数据与方法

- **样本**：6,853 只美股（NASDAQ / NYSE / AMEX 普通股，剔除 ETF），月度面板约 90 万行
- **行情**：新浪财经美股全历史日线，自建拆股复权（正向 / 反向拆股按除权日开盘价判定，累计因子校验）
- **成分股**：fja05680/sp500 的标普 500 时点成分（PIT），避免前视偏差
- **股票池**：NASDAQ Trader 官方符号目录
- **回测**：2009–2025 共 204 个月，月度净值，含 0.2% 单边交易成本

## 目录结构

```
.
├── step1b_universe.py        股票池与成分股清单构建
├── step2*.py                 行情抓取（东方财富 / 腾讯 / 新浪多源）
├── step3b_build.py           复权月度面板构建
├── step6_verify.py           冠军名单与本地数据交叉验证
├── step7_final.py            年度冠军与基础回测
├── step16_mature.py          严谨回测引擎（PIT / 参数网格 / 成本 / 风控开关）
├── step17_final_report.py    严谨版报告
├── step18_plain.py           全模型对比与当前选股
├── step19_report_plain.py    直白版报告生成
├── output/
│   ├── 排名策略对比.html       主报告（含图表）
│   ├── 排名策略对比.xlsx       数据表
│   ├── report.html            冠军股研究
│   └── *.csv / *.json         中间结果
└── data/                      行情缓存（未纳入版本控制）
```

## 复现

```bash
pip install pandas numpy matplotlib requests openpyxl xlsxwriter

python step1b_universe.py     # 股票池
python step2e_fetch_sina2.py  # 行情（约 3 分钟，7,400 只）
python step3b_build.py        # 复权面板
python step16_mature.py       # 回测引擎
python step18_plain.py        # 全模型对比
python step19_report_plain.py # 生成报告
```

## 月度自动选股

仓库配置了 GitHub Actions（`.github/workflows/monthly-pick.yml`），**每月 1 日北京时间 09:00 自动运行**：

1. 拉取最新标普 500 成分股（含 GICS 行业）
2. 对每只股票取新浪全历史日线，自建拆股复权
3. 计算过去 12 个月涨幅，过滤股价 < $5、日均成交额 < $2,000 万
4. 按规则选出前 3 名，同行业最多 2 只、行业权重上限 60%
5. 检查标普 500 是否跌破 10 月均线，决定满仓 / 半仓
6. 把结果写回 `output/monthly/YYYY-MM.md` 和 `output/current_picks.csv` 并自动提交

也可以手动触发：仓库页面 → Actions → 月度选股 → Run workflow。

**触发方式**

| 方式 | 条件 |
|---|---|
| 定时 | 每月 1 日 01:00 UTC（北京时间 09:00） |
| 手动 | Actions → 月度选股 → Run workflow |
| 推送 | 修改 `monthly_pick.py` / `requirements.txt` / 本工作流时自动跑一次 |

机器人用 `GITHUB_TOKEN` 提交，不会反过来触发工作流，因此不会死循环。

**注意**：GitHub 会在仓库 60 天无提交后自动暂停定时任务，任意一次 push 即可恢复。

**查看结果**：`output/monthly/YYYY-MM.md`（当月）+ `output/monthly/latest.md`（最新）+ `output/current_picks.csv`（可直接导入表格）。

本地运行：

```bash
python monthly_pick.py --workers 10
```

输出示例见 `output/monthly/latest.md`。

## 隐私保护

本仓库**不包含任何个人持仓、账户或截图**，并有三道防线：

1. **`.gitignore` 黑名单**：`持仓*`、`*截图*`、`*.jpg`、`*.jpeg`、`*.webp`、`截图*`、`screenshot*` 等一律忽略；仓库只允许 `output/img/` 下的研究图表。
2. **提交前拦截钩子**（`.githooks/pre-commit`）：检测到敏感文件名或非 `output/img/` 的图片时直接拒绝提交。新机器克隆后执行一次：
   ```bash
   git config core.hooksPath .githooks
   ```
3. **历史审计**：任何时刻可执行下面的命令确认历史里没有混入个人文件：
   ```bash
   git log --all --name-only --pretty=format: | sort -u | grep -Ei '持仓|诊断|截图|screenshot'
   ```

> 钩子必须带 `-c core.quotePath=false`，否则 Git 会把中文文件名转义成 `\346\214\201` 导致匹配失效。

## 已知局限

1. **生存者偏差**：本地行情仅含当前仍可查询的标的，退市与被并购公司缺失，估计高估收益 3–6 个百分点/年
2. **未计股息**：复权价口径不含股息再投资
3. **分拆未复权**：spin-off 会导致个别年份收益低估
4. **容量未建模**：未模拟冲击成本，大资金实际收益会低于回测

本仓库仅供研究，不构成投资建议。
