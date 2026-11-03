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
