# 数据字典

> 所有口径的唯一出处。改字段先改这里。

## 1. `data/signal.json` —— 当前信号

| 字段 | 类型 | 含义 | 口径/约束 |
|---|---|---|---|
| `month` | string | 信号月份 | `YYYY-MM`，最近完整自然月 |
| `date` | string | 生成日期 | 北京时间 |
| `official_month` | string | 年度持仓的排名基准月 | 恒为「上年 12 月」 |
| `official_year` | number | 该持仓生效的年份 | = `official_month`.year + 1 |
| `valid_from` / `valid_to` | string | 生效区间 | 01-01 ~ 12-31 |
| `ranking_month` | string | 当月排名所用月份 | 用于「若现在重排」 |
| `rebalance_hint` | string | 下次换仓提示 | 「YYYY-12 最后一个交易日」 |
| `trend` | string | 大盘开关 | 满仓 / 半仓 |
| `exposure` | number | 建议仓位系数 | 1.0 或 0.5；**不参与净值计算** |
| `spy` | number | SPY 最新收盘 | 与 `quotes.session` 同日 |
| `ma` | number | SPY 10 月均线 | 只用完整月份 |
| `action` | string | 本月动作文案 | 一句话 |
| `official[]` | array | 年度持仓（3 只） | 见下表 |
| `picks[]` | array | 当月排名前 3 | 与 `official` 不同义 |
| `bench[]` | array | 当月排名前 10 | 供参考，不据此换仓 |
| `incremental` | bool | 是否走同月增量 | true 表示排名未重算 |

**`official[]` / `picks[]` 元素**

| 字段 | 类型 | 含义 |
|---|---|---|
| `rank` | number | 排名（1 起） |
| `ticker` | string | 代码 |
| `name` | string | 公司名 |
| `sector` | string | GICS 行业（不得为空/Unknown） |
| `ret12` | number | 过去 12 个月复权涨幅（小数） |
| `weight` | number | 策略内权重（3 只合计 1） |

## 2. `data/equity.json` —— 当期净值与持仓

| 字段 | 类型 | 含义 | 口径 |
|---|---|---|---|
| `base_date` | string | 建仓日 | `YYYYMMDD`，2025-12-31 |
| `updated` | string | 计算时间 | **北京时间**，`updated_tz = Asia/Shanghai` |
| `exposure` | number | 当前敞口 | 仅展示，不缩放净值 |
| `weights[]` | array | 目标权重 | `{ticker, weight}`，与 signal.official 一致 |
| `points[]` | array | 日频净值 | `{d: YYYYMMDD, s: 策略净值, b: 标普净值}`，起点 1.0 |
| `position[]` | array | 持仓明细 | 见下表 |
| `stats.strategy` | number | 当期策略收益 | 曲线末值 − 1，**必须等于页面收益率** |
| `stats.benchmark` | number | 同期标普收益 | |
| `stats.mdd` | number | 当期最大回撤 | 日频峰值回撤，负数 |

**`position[]` 元素**

| 字段 | 含义 |
|---|---|
| `ticker` / `weight` | 代码 / 权重 |
| `entry_date` / `entry` | 建仓日 / 建仓价（2025-12-31 收盘，未复权原价） |
| `last_date` / `last` | 最新交易日 / 最新收盘价 |

## 3. `data/curve.json` —— 全周期回测

| 字段 | 类型 | 含义 |
|---|---|---|
| `points[]` | array | 月频 `{d, s, b, dd}` |
| `years[]` | array | 逐年 `{year, ret, bench, dd}`，16 年 |
| `stats.total` | number | 累计收益（16 年复利） |
| `stats.cagr` | number | 年化 |
| `stats.mdd` | number | **年内谷底**口径最大回撤 |

## 4. `data/strategy_history.json` —— 逐年记录（锁定）

| 字段 | 含义 |
|---|---|
| `years[].year` | 年份 |
| `years[].holdings[]` | 当年持仓 `{ticker, name, sector, weight, ret12, ret_year}` |
| `years[].ret` | 当年策略收益 |
| `years[].dd` | 年内最深回撤 |
| `years[].bench` | 当年标普收益 |
| `years[].n_members` | 当年成分股数量 |
| `summary.total` | 累计收益 |
| `summary.cagr` | 年化 |
| `summary.mdd` | **年末口径**最大回撤（≠ `worst_intra`） |
| `summary.worst_intra` | 最深年内回撤（页面「全周期最大回撤」用它） |

> ⚠️ `summary.mdd`（年末口径）与 `worst_intra`（谷底口径）是两个不同指标，
> 页面只用后者，前者保留供审计。

## 5. `data/quotes.json` —— 行情快照

| 字段 | 含义 |
|---|---|
| `ts` / `ts_local` | UTC / 北京时间 |
| `session` | 所属交易日（由数据反推） |
| `fx` | 美元兑人民币 |
| `count` / `universe` / `coverage` | 抓取数 / 总数 / 覆盖率 |
| `ok` | 覆盖率是否达标 |
| `stale[]` | 未更新到最新交易日的代码 |
| `quotes{}` | `ticker → {p, pc, chg, name, ...}` |

## 5.5 权重分配规则

| 情形 | 权重 | 原因 |
|---|---|---|
| 三只分属三个行业 | 各 1/3 | 等权，最简 |
| 其中两只同行业 | 该行业 30% + 30% = **60%**，第三只 **40%** | 满足「单行业 ≤60%」的硬约束 |
| 三只都不足（<3 只） | 按剩余只数等分 | 极少见，年报中不会出现 |

因此历史记录里会出现两种权重形态：`33/33/33` 与 `30/30/40`。
出现 `30/30/40` 的年份，一定是三只中有两只属于同一 GICS 行业。

## 5.6 口径 B 数据集

`data/b/` 下含 `signal/equity/curve/strategy_history/preview` 五个文件（字段是主目录子集：signal 无 `date/spy/ma/bench`，curve 的点只有 `{d,s,b}`，years 用 `pool` 而非 `n_members`），
唯一差别是排名池：只保留「上一年 1/1–12/31 全程在标普 500 内」的股票。
两套数据互不影响，前端按 Tab 选择加载。

## 6. 口径铁律

1. **净值 = 满仓 gross**，不乘敞口。
2. **当期收益**必须等于 `equity.stats.strategy`，且三页一致。
3. **今日涨跌** = Σ 股数 ×(最新价 − 昨收) ÷ (当前市值 − 今日盈亏)。
4. **全周期**用 2010–2025 回测，**当期**用 2025-12-31 起的日频净值，两者不可比、不可混。
5. 所有面向用户的时间均为**北京时间**并标注「（北京）」。
6. 收益用百分比，不用「倍」。

## 7. 口径 B 的冻结输入（必须与代码一起版本化）

| 文件 | 内容 | 说明 |
|---|---|---|
| `static/members_frozen.csv` | 标普 500 时点成分（ticker/start_date/end_date） | B 的池子定义来源 |
| `static/sector_frozen.json` | 行业映射 | 行业上限判定 |
| `static/bars_monthly.json` | 月度复权价/原价（含 SPY） | 排名与收益计算的唯一价格来源 |

三份快照任一变化都可能改变历史；`output/baseline_b.json` 用于检测这种漂移。
