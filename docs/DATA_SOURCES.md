# 数据源全景与分配策略

> 版本 2026-11-02.2 ｜ 机器可读版：[`static/source_matrix.json`](../static/source_matrix.json) ｜ 实时逐格审计：`output/coverage_audit.json`
> 原则：**不丢弃任何数据源**。每个源都有明确的角色、优先级与兜底位；低等级源保留在 `prices_monthly`，仅供追溯与对比。

> 口径标签核验：28 个选源元数据原误标 total_return，实际对应源全是 price_only；已按实库行口径纠正，剩余不一致 0。`db_select.refresh` 同时比较来源和口径，锁定/冻结决策仍受保护。不得用 `series_pick` 标签替代 `series_selected` 逐月实际 basis 计算覆盖率；纠正标签本身没有新增全收益数据。
> 最新 PIT 全收益覆盖 6,791/8,032（84.55%），距 95% 差 840 格。两个 CC0 档案已入库且禁止自动选源；4 组同股更名补入 50 格，3 个已核验 AV 源再补 36 格。筛选价已决定按当时名义股价修正，实施未完成，见 `PRICE_FILTER_BASIS_DECISION.md`。

> 名义价源独立于收益价：WIKI 原始价、2022 同版本 Yahoo 事件逆推价及 AV 官方原价均经单位证据门控；新浪原始观察单位未证明，不能进入筛选视图。AV 月线已实测可用，当前滚动预约 25/25，不再请求。095 视图守卫禁止越过已核验证券生命周期；新旧 LB 不能拼接。最新落库和验收见 `CONVERGENCE_REVIEW_2026-11-02.md`。

> 新增 2023 固定 Yahoo 事件快照（v2）和 2026 科技股固定事件档案（清单在 `static/tech_actions2026_archives.json`）。分别入库 4,768/893 条原价观察，当前可用 4,768/528 条；均只填补筛选观察。至少 24 个既有独立原价锚点及收益交叉匹配，排除自身锚点；门槛附近按实际最大偏差保留不确定性。096–097 支持这两个独立源，不改变收益选源优先级。2026 档案首批 4 个超时在续跑后恢复，当前选中 18 代码全部通过；已固定各版本和实际档案 SHA256，缓存键包括版本，重跑无重复行。证据见对应 `output/*review_summary.json`。

## 一、历史调查快照（下面数字与结论不是当前覆盖状态）

| 指标 | 数值 |
|---|---|
| 成分表需要的标的 | **869 只**（2010 年以来曾在标普 500） |
| 库内已有 | 847 只 |
| 完全缺失 | **23 只**（多为 2000 年前破产票：LEHMQ/ANRZQ/CITGQ/EKDKQ…） |
| 12 月覆盖 | **78.9%**（10,972 / 13,904） |
| 缺失的 12 月 | **2,932 个 symbol-month**，涉及 416 只标的 |
| 各年缺口 | 2010:253 → 2015:147（最少）→ 2025:216（新纳入票无早期历史） |
| 口径分布 | `total_return` 1,080 行（0.4%）／`price_only` 237,371 行（99.6%） |

**两个核心结论**
1. **覆盖率**：每年 12 月缺 135–253 只，直接缩小排名池。必须用 AV 逐只回填。
2. **口径**：99.6% 的行是 `price_only`（无分红）。按你定的「全收益」口径，**当前数据集不具备锁定资格**。

## 二、数据源能力矩阵（19 个，含已排除）

### 2.1 历史供数类（能提供价格）

| 源 | 退市票 | 复权口径 | 深度 | 免费额度 | 实测状态 | 角色 |
|---|---|---|---|---|---|---|
| **Alpha Vantage** | ✅ 实测 ATVI/ETFC/CERN/XL/TWTR 都有 | **全收益** | 1999+ | 25 次/天 | NVDA2021 +125.5%／DUK2012 −2.02% ✓ | **历史主源** |
| **EODHD** | 付费层 ✅ | **全收益** | 免费 13 月／付费 30+ 年 | 20 次/天（免费） | 抓出 SNDK 142%、MPC 3.2% ✓ | **近期校验 + 付费加速** |
| **HF AYUSHKHAIRE** | ❌ 全缺 | price_only | 2010+ | 无限制 | NVDA2021 +125.1% ✓ | **覆盖兜底** |
| **HF AmirTrader** | ❌ | 全收益 | 2019+ | 无限制 | NVDA2021 +125.5% ✓ | **第三源裁判** |
| **本地新浪** | ✅ **唯一免费覆盖退市** | price_only（已修复） | 1999+ | 无限制 | 修复 583 只／2077 次 | **退市桥接** |
| Kaggle vladosht | ? | ? | 2010–2025 月频 | 免费（需 token） | 目标数据集下架 | 历史候选（关闭） |
| **Kaggle CC0 全市场档案** | ✅ 有退市票；不保证全覆盖 | **total_return**（数据卡声明拆股+分红复权） | 各票不一，最晚 2017-11 | 免费，CC0 | v3 文件清单命中 794 个 PIT 成分代码；单票实读 2005–2017 | **一次性历史补缺** |
| **Kaggle CC0 Yahoo-2020 档案** | ✅ 有 2020 年后退市者 | Adj Close 为全收益，Close 仅拆股复权 | 完整月末至 2020-03 | 免费，CC0 | 12,201 行/88 代码已正式入库；另有名称元数据 | **候选档案，禁止自动选源** |
| MarketParquet | ✅ 8,500 只退市 | price_only | 2000+ | 365 天／$79 买断 | 格式已验证 | **Plan C 买断** |
| Tiingo | ? | 全收益 | 1962+ | 500 symbols/月 | 未验证 | **第二全收益源** |
| Sharadar SEP | ✅ 21,000 | 三套价格 | 1998+ | 付费 | 未验证 | **机构级终局** |
| Norgate | ✅ 全覆盖 | 全收益 | 1990+ | $630/年 | 未验证 | **PIT 成分专家** |
| Nasdaq API | ❌ | price_only | 10 年 | 无 key | 实测可用 | 近期抽检 |
| stockanalysis | ❌ | 全收益 | 12 月 | 无 key | 实测 12 条 | 单票快查 |

### 2.2 参考/校验类（不供价格，但决定正确性）

| 源 | 提供什么 | 用途 | 状态 |
|---|---|---|---|
| **fja05680/sp500** | 成分进出日期（1263 行） | 口径 B 的池子定义 | 已用，稳定 |
| coil.trade PIT / chinobing | 成分历史第二源 | 与 fja05680 交叉验证 | 未验证 |
| datahub s-and-p-500 | 当前 GICS | 行业上限判定 | 已冻结 513/869 |
| **QuantConnect LEAN** | 27,500 只含退市的独立回测环境 | 第三方复现我们的数字（数据不可导出） | 未验证 |
| **OpenAssetPricing** | CRSP 口径 Mom12m 因子 | 学术锚点：方向与量级是否合理 | 未验证 |

### 2.3 已排除但保留在册（7 类）

Yahoo（403）、Stooq（JS 挑战）、WSJ／FMP demo（401）、BigQuery（无美股 EOD）、DoltHub（404）、Polygon 免费（2 年）、Alpaca（2016 起）、Twelve Data／Finnhub（无退市或付费）、Kaggle 常规 S&P 集（只有现役）。旧 `kaggle_vladosht` 路径仍下架；新发现的 CC0 全市场档案单独登记。

> **保留原因**：上游策略会变（Yahoo 曾可用），留档避免重复调研；且每个源的接入代码路径一致，恢复即可用。

## 三、缺口 → 源 的分配策略（可执行）

| 缺口 | 数量 | 首选 | 备用 | 最终处理 |
|---|---|---|---|---|
| 完全缺失标的 | 23 只 | AV | EODHD 付费 / MarketParquet | 标记 `permanent_missing`，该标的当年不入池 |
| 12 月缺失 | 2,932 点 | AV | HF（现役）→ 新浪（退市）→ EODHD（近 13 月） | 仍缺 → 该年不入池，计入覆盖率 |
| 口径不足（99.6%） | 237k 行 | AV | EODHD 付费 / Tiingo / Sharadar | 未升级 → 不允许 `locked` |
| 可疑序列 | 43 处 | AV | AmirTrader / EODHD | 无法解释 → 该年不参与排名 |
| 行业分类 | 356 只 Unknown | datahub | 手工 overrides | 未分类桶最多 1 只 |
| 成分冲突 | — | fja05680 | coil / chinobing | 人工裁定并入库 |

## 四、调度策略（按额度与价值排序）

```
每天 03:10 UTC   relay_source（HF，无额度）      → 先补现役票的 12 月缺口
每天 11:10 UTC   alphavantage 回填 22–25 只       → 按「缺口数」降序，优先退市票
每次流水线       eodhd 校验近 13 个月（持仓+候选） → 偏差 >2% 自动覆盖
每周一次         fja05680 成分刷新 + 交叉验证
一次性           Kaggle CC0 档案                  → 仅补可验证的 2010–2016 历史缺口；不覆盖 2017-12 以后
```

## 五、锁定条件（数据必须「绝对可靠」）

| 门槛 | 要求 | 当前 |
|---|---|---|
| 口径 | 100% `total_return` | 0.4% ❌ |
| 12 月覆盖 | ≥ 95% | 78.9% ❌ |
| 六项验收 | 全绿 | 已过 6/6（但基于混合口径） |
| 交叉校验 | 持仓与候选两源偏差 ≤2% | 5/5 通过 ✅ |
| 版本 | 有 dataset 记录 + input_hash + 人工批准 | 有 hash，未批准 |

**结论：现在不能锁定。** 预计 Alpha Vantage 免费额度需要约 **35 天**跑完 869 只；若订阅 EODHD 一个月（$19.99），可压缩到 **1 天**。

## 六、更新与不可变机制

| 场景 | 规则 |
|---|---|
| 新月份 | append-only，自动入库，不改历史 |
| 历史修正 | 必须新建 dataset 版本（v2、v3…），人工批准后才提升基线 |
| 锁定后 | Postgres 触发器禁止 `dataset_rows` 的 INSERT/UPDATE/DELETE |
| 源冲突 | 高等级源胜出，双方原始值都保留在 `prices_monthly` |
| 数据源变更 | 记录到 `fetch_runs` 与 `validations`，可追溯到哪次运行 |

## 七、为什么这样分配（决策理由）

1. **AV 做持续主力**：现有可按免费额度持续请求、覆盖退市票的全收益源；Kaggle CC0 档案只覆盖到 2017-11，不能替代后续年份。
2. **EODHD 做校验**：免费层只有 13 个月，恰好覆盖「当年持仓与次年预览」——这正是最需要准确、也最容易出错的部分。
3. **HF 做覆盖**：无额度限制，能立刻把覆盖率从 79% 拉到 90%+，代价是口径降级（需标记）。
4. **新浪不淘汰**：它是唯一免费覆盖已退市票的源，400+ 只票的历史全靠它；即使复权有缺陷，修复后仍是最低成本的过渡底座。
5. **Kaggle CC0 档案**：能一次性补一部分 2010–2016 的全收益年末缺口；2017-12 以后仍需现有源。MarketParquet 完整包属于付费选项，按当前决策不启用。
6. **QuantConnect / OpenAssetPricing 不做供数**：它们的价值是**裁判**——独立复现我们的回测，验证数字没算错。

## 八、调度真相：数据库里的 source_registry（2026-10-31 更新）

数据源不再写在代码里，而是存在 Supabase 的 source_registry 表，采集脚本按 priority 调度。

| 序 | source_id | 口径 | 状态 | 额度 | 角色 |
|---|---|---|---|---|---|
| 1 | alphavantage | total_return | ok | 25/天 | 历史主力（退市票优先） |
| 2 | eodhd | total_return | ok | 20/天 | 近 13 个月校验 |
| 3 | **sina（新浪，老底座）** | price_only | ok | ∞ | 退市票桥接 |
| 4 | hf_ayushkhaire | price_only | ok | ∞ | 现役票覆盖 |
| 5 | hf_amirtrader | total_return | ok | ∞ | 2019+ 校验 |
| 6 | marketparquet_free | price_only | **ok（新）** | ∞（近 365 天） | 近期批量 |
| 7 | nasdaq_api | price_only | ok | ∞ | 抽检 + 股息史 |
| 8 | stockanalysis | total_return | ok | ∞ | 单票快查 |
| — | kaggle_cc0 | total_return | **ok（历史档案）** | 一次性 | CC0；逐票历史最晚至 2017-11；不参与日常调度 |
| — | kaggle_yahoo2020 | total_return | **ok（历史档案）** | 一次性 | CC0；完整月末至 2020-03；数据库约束禁止自动选源 |
| 1 | fja05680 / datahub_gics | — | ok | ∞ | 成分 / 行业 |
| — | stooq / yahoo / wsj / macrotrends | — | **blocked** | — | 被 JS 挑战/403/网络层封锁；curl_cffi 也无法绕过 |
| — | kaggle_vladosht / bigquery / dolthub | — | **dead** | — | 目标数据集下架 / 无美股 EOD；`kaggle_cc0` 是单独可用的历史档案 |
| — | marketparquet_paid / tiingo / sharadar / norgate | — | **excluded_paid** | — | **按决策不考虑付费** |

**付费源全部标记 excluded_paid（priority=NULL，不参与调度），仍保留在册以便日后解锁。**

### 分工与额度使用（不重复劳动）

`
AV 25/天      → 只做退市票 + 缺口最大的票（别人做不了）
HF ∞          → 现役票的 12 月缺口（便宜的先补）
新浪 ∞        → 退市票桥接（AV 未轮到前不留空窗）
EODHD 20/天   → 只做近 13 个月校验，不碰历史
校验抽样      → 每个源校验不同样本（AV 15 / HF 15 / AmirTrader 15 / EODHD 10），互不重复
`

**重叠检查**：v ∩ hf = 0、v ∩ sina = 0、hf ∩ sina = 0（每次生成计划都断言）

### 当前进度

| 指标 | 起始 | 现在 |
|---|---|---|
| 12 月覆盖 | 78.9% | **80.9%** |
| 有缺口的标的 | 416 | 361 |
| 库内标的 | 840 | 847 |
| 完全缺失 | 30 | 23 |

AV 今日额度已用尽（25 次），明天起每天自动 24 只，约 35 天补齐历史层。

## 九、全球免费源扫描（2026-11-01，19 个端点并发实测）

对每个源用 AAPL（现役）+ ATVI（退市）双标的探测，只读、15 秒超时。

| 源 | 国家 | AAPL | ATVI | 结论 |
|---|---|---|---|---|
| **stockanalysis** | US | ✅ 全收益 10 年 | ✅ 26 只退市可查 | **已接入** |
| nasdaq | US | ✅ 10 年 | ❌ | 仅抽检 |
| stooq | PL | ❌ JS 挑战 | ❌ | 封锁 |
| morningstar / marketwatch / wsj | US | ❌ 401 | ❌ | 封锁 |
| finanzen.net / onvista / ariva | DE | ❌ 403/400/404 | ❌ | 封锁 |
| marketscreener / boursorama | FR | ❌ 403 / 无数据 | ❌ | 封锁 |
| investing.com | EU | ❌ 403 | ❌ | 封锁 |
| **yahoo-jp**（日文 Yahoo） | JP | ✅ HTML 有数据 | ⚠️ ATVI 有、ETFC 无 | 仅 HTML 表格，无批量接口 |
| minkabu / kabutan | JP | ❌ 405 | ❌ | 封锁 |
| naver / daum | KR | ❌ 空结构 / 403 | ❌ | 封锁 |
| eastmoney | CN | ⚠️ 曾 200 后连接重置 | ❌ | 不稳 |
| tencent / netease / xueqiu | CN | ❌ DNS/502/400 | ❌ | 封锁 |

**结论**
1. 能稳定供美股历史价的只有 4 家：**stockanalysis（全收益，含部分退市）**、Nasdaq（10 年，无退市）、Yahoo 中转（现役）、Alpha Vantage（全收益，含退市，25/天）。
2. **日韩欧主流财经站点全部有反爬或需登录**，没有一家能免费批量取美股历史。
3. 唯一能补「2010–2016 退市票」的仍是 **Alpha Vantage**。

注册表现状：ok 11 / blocked 20 / untested 5 / excluded_paid 4 / dead 3 / partial 1

## 十、反爬绕过实测（2026-11-01，目标：把被拦的源直接拉下来）

工具：stealth_fetch.py（curl_cffi TLS 指纹 + Playwright 反检测 + Cookie 复用 + 浏览器内 fetch）

| 源 | 手段 | 结果 |
|---|---|---|
| **ariva.de** | Playwright + 反检测脚本（抹 webdriver/plugins/chrome） | ✅ **200，历史表可取**：AAPL 美元/美交所；**ATVI 退市后仍有德国所欧元价** |
| **naver.com** | 同上 | ✅ 200，美股票页面可开；无历史 JSON 接口 |
| finanzen.net | 同上 | ❌ Akamai 403（Cookie 只有 Bot-Information） |
| onvista | 同上 | ❌ Vercel Security Checkpoint，停在挑战页 |
| eastmoney | curl_cffi | ❌ 主机连接超时（网络层不通，不是反爬） |
| minkabu / kabutan | 浏览器 | ❌ 405 |
| investing / marketscreener | curl_cffi | ❌ 403 |

**关键结论**
1. 反爬**可以绕过一部分**（ariva、naver 已通），但拿到的是**收盘价（未复权）**，不是全收益。
2. 退市票在 ariva 上只剩**德国交易所的欧元价**，与美股美元全收益不可比。
3. 因此对「2010–2016 退市票全收益」这个唯一缺口，**绕过后仍然没有替代 Alpha Vantage 的免费源**。
4. 绕过工具已沉淀为 stealth_fetch.py，未来任何源被拦可直接复用。
