# 2026 年代码身份补查

本轮从流动性采集的“当前证券目录无对应代码”回执出发，核对发行人原始披露及在线库。目录缺失不能直接视为没有历史数据，更不能把合并后的股票价格直接拷贝到旧代码。

## 已处理：BK → BNY

发行人公告明确普通股于 2026-05-21 更换代码；OCC 58945 公告确认同日代码切换，之后的 SEC 8-K 仍是 CIK 1390777 的普通股。它符合用户已批准的同一证券连续成分资格规则。

- [发行人公告](https://www.bny.com/corporate/global/en/about-us/newsroom/press-release/bny-announces-planned-change-of-stock-ticker-symbol-to-bny-130465.html)
- [OCC 公告 58945](https://infomemo.theocc.com/infomemos?number=58945)
- [代码切换后的 SEC 8-K](https://www.sec.gov/Archives/edgar/data/1390777/000119312526281160/d137058d8k.htm)

在线 members 已按 2026-05-21 拆分 BK/BNY，BNY 已有 total_return 选源；但已核验的连续资格引用表未包含该更名。新增事实已写入在线引用表，不能仅靠本地登记文件生效。对实际成员区间比较，唯一变化是 2026 年全年资格增加 BNY；因此最早影响 2027 年选股资格，2010–2026 选股年度的资格集合不变。原价、价格源选择、锁及正式策略产物未改，旧 B 基线字节不变。回执：`output/bny_membership_verification.json`。

BK 旧选源仍为 price_only，价格绝对单位与 BNY 不可直接混用。本次只修正资格，不授权旧代码价格复制或来源提升。

## 待补历史来源/实际承接，禁止作为简单更名合并

- AVB 与 EQR：发行人最终 8-K 确认 2026-08-17 完成合并，AVB 每股换 2.793 股后继普通股，并涉及零碎股份现金；新代码 VMRK 自 08-18 开始交易。在线 members 已有相应截止及 VMRK 开始日期。旧 AVB/EQR 的 2025 年流动性仍需要历史日线来源；AVB 跨事件持有收益必须经过实际承接路径。不能把 VMRK 价格直接放入 AVB。
  [AVB 最终 8-K](https://investors.avalonbay.com/sec-filings/all-sec-filings/content/0001104659-26-097833/tm2623381d1_8k.htm)，[后继发行人完成公告](https://investors.vivmarkresidential.com/sec-filings/all-sec-filings/content/0001140361-26-033377/ef20080318_ex99-1.htm)。
- CTRA：发行人确认 2026-05-07 完成与 Devon 的全股票合并，后继上市代码为 DVN。在线 members 已有截止日期；旧代码历史日线缺口不能用当前 DVN 简单代替，承接比例等须从完整交易条款另行核验。
  [Devon 完成公告](https://www.devonenergy.com/news/2026/Devon-Energy-and-Coterra-Energy-Complete-Merger)。
- DAY：发行人确认 2026-02-04 私有化完成，普通股每股获得 70 美元现金，停止交易。它需要现金承接，不能按股票更名处理。2025 年流动性缺口仍可寻找退市前日线；当前目录缺失不是删去候选的依据。
  [Dayforce 完成公告](https://www.dayforce.com/who-we-are/newsroom/thoma-bravo-completes-acquisition-of-dayforce)。

这些补查不构成相应日线、公司行动收益或完整策略的验收。其余目录缺失证券继续逐项核对，不猜测继承关系。

## 已定位：交易所迁移截断历史

Walmart 的发行人 8-K 明确 2025-12-08 收盘后结束 NYSE 交易，12-09 开始 Nasdaq 交易，代码仍为 WMT。这不是新股票上市，不能将迁移前 234 个缺失交易日视为无交易或剔除它们。

[发行人 8-K](https://stock.walmart.com/sec-filings/all-sec-filings/content/0000104169-25-000177/wmt-20251119.htm)，[Nasdaq 数据技术通知](https://m.nasdaqtrader.com/TraderNews.aspx?id=DTN2025-35)。

实际有界探测：当前 Eastmoney 105.WMT 返回 2025-12-09 至 12-31 的 16 日；旧 106.WMT 返回 rc=100、无数据。因此切换交易所参数不能补齐该源的旧历史。回执为 output/wmt_exchange_history_probe.json；仍需其他完整历史成交额来源，不拼接猜测数据，不删除缺失交易日，不触碰 910 个已核验窗口。
# BK→BNY 精确暂存复核（2026-11-03）

后续状态：已使用 research/apply_bk_rename.py 完成定向提升与选源切换，下面“仍未完成”段落保留为暂存阶段记录。操作前重新执行 34 个 A/B 年度、每年两种未知价格门槛边界的对比，0 项变化。真实数据库事务先执行并整体回滚，另开连接确认原选源、202 条待处理行、目标价格缺失及历史窗口缺失全部恢复后，才执行提交。

提交仅提升本批 202 个暂存 ID，BK 改为 yahoo_relay / total_return；2026-12 截止窗口防止未来旧代码观测进入历史别名。741 条原观测逐值保留。提交后数据库精确数值哈希匹配原审查，另用只读 REST 核对 202 行浮点读取值完全一致，旧 B 基线 SHA256 仍为 20c0680276cf79cbb06d8aaab35c3e40cde010447f6e15e75abd95ff0ef33c90。见 output/bk_rename_rollback_probe.json、bk_rename_applied.json、bk_applied_verification.json。该源修复不是全历史收益和新策略发布验收，未新增历史锁。

在线 REPEATABLE READ 只读复核得到 202 个 canonical 月份，93 对独立收益比较仍通过；同股更名事实仍存在，原 BK 选源仍为 sina_repaired / price_only / 未冻结，目标 Yahoo 行和同源暂存行此前均为零。完整精确数值与哈希见 output/bk_exact_repair_review.json。

research/stage_bk_rename.py 在复核哈希、在线身份、原选源与保护状态后，以数据库精确数值复制 202 行到 ingest_staging；不调用全局 promote_staging，不修改选源或价格。提交后另开连接验证 202 行全部待处理、0 行已提升，与 BNY 原始 canonical 数值逐行比较无差异，BK 正式选源未变，见 output/bk_staging_verification.json。重复写入被显式拒绝，不能用重跑覆盖已有暂存证据。

这一步只完成在线暂存。正式提升、精确行身份验收、BK 旧源其他行保留证明、独立读取路径与选源切换仍未完成；不得把它视为新策略或历史验收完成，也不得全局提升其他未经本次审查的数据。

