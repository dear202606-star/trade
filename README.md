# 美股排名动量策略研究

[![月度选股](https://github.com/dear202606-star/trade/actions/workflows/monthly-pick.yml/badge.svg)](https://github.com/dear202606-star/trade/actions/workflows/monthly-pick.yml)

对「买入上一年涨幅前 N 名」的横截面动量策略做系统回测，比较 11 种持有模型，并给出可执行的建仓方案。

> **每月自动选股已上线**：查看最新名单 → [`output/monthly/latest.md`](output/monthly/latest.md)

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

## 已知局限

1. **生存者偏差**：本地行情仅含当前仍可查询的标的，退市与被并购公司缺失，估计高估收益 3–6 个百分点/年
2. **未计股息**：复权价口径不含股息再投资
3. **分拆未复权**：spin-off 会导致个别年份收益低估
4. **容量未建模**：未模拟冲击成本，大资金实际收益会低于回测

本仓库仅供研究，不构成投资建议。
