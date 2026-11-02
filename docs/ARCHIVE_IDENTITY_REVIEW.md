# 历史档案身份核验记录

2026-11-01。本文件记录原始发行人材料，不等于批准换源。数据库约束及本地选源排除仍有效。

2026-11-02 增量：`sql/095_security_history_windows.sql` 为 BJS、AYE、LB、HES、ALXN、XLNX 设置完整月终止边界，各原始发行人链接保存在该表的证据字段。部分退出月份不能假作普通完整月；原始数据不删除。HES、ALXN、XLNX 已完成 AV 收益独立核对并精确换源，应用证据为 `output/av_source_application.json`。

LB 特别处理：原 [L Brands 2021-08-03 更名 BBWI 并分拆 VSCO](https://www.sec.gov/Archives/edgar/data/701985/000114036121026658/nt10026698x7_8k.htm)，每 3 股获得 1 股 VSCO；当前 LB 则是 [2024-06-28 上市的 LandBridge](https://www.sec.gov/Archives/edgar/data/1995807/000119312525107346/d925777dars.pdf)。28 条新公司原价已撤销单位证明，底层观察保留；旧公司 163 条原价独立核对后存入，截止 2021-07。是否持有全部分拆承接股票仍待用户决定，禁止将母公司改名直接当作完整经济承接。详见 `output/lb_distribution_review.json`。

| 代码 | 已核实事实 | 对数据的影响 | 原始材料 |
|---|---|---|---|
| HCP | HCP Inc. 于 2019-10-30 更名 Healthpeak，2019-11-05 改用 PEAK；HashiCorp 的 HCP 于 2021-12-09 开始交易 | 当前 HCP 的 2021 起历史不能用于旧医疗地产公司的指数成员期；不得拼接 | [Healthpeak 2019 10-K](https://www.sec.gov/Archives/edgar/data/765880/000162828020001456/healthpeak10-k12312019.htm)、[HashiCorp IPO](https://www.sec.gov/Archives/edgar/data/1720671/000119312521355378/d199913dex991.htm) |
| CHK | 2021-02-09 破产重组使旧股权益注销，新旧财务报表不可直接比较 | 旧股票档案与 2021 起新股票不是连续持仓；不能跨重组拼收益 | [2021 10-K](https://www.sec.gov/Archives/edgar/data/895126/000089512622000029/chk-20211231.htm) |
| PX | Praxair 的 PX 股票于 2018-10-30 收盘后停止 NYSE 交易 | 2021 起的 PX 序列不能自动认定为同一股票 | [Linde 合并 8-K](https://www.sec.gov/Archives/edgar/data/1707925/000119312518313073/d643979d8k.htm) |
| UTX | United Technologies 与 Raytheon 合并后于 2020-04-03 改名并使用 RTX | 2017 档案可作为原证券候选；跨分拆/合并的连续收益仍需专门核验 | [RTX 合并公告](https://www.sec.gov/Archives/edgar/data/1047122/000119312520097237/d827106dex991.htm) |
| EP | 指数里的 El Paso 于 2012-05-25 退市；Empire Petroleum 于 2022-03-08 开始使用 EP | 目前 EP 的长历史不能仅凭代码相同认定为原 El Paso，先隔离核验 | [El Paso 退市 8-K](https://www.sec.gov/Archives/edgar/data/1066107/000119312512253764/d360854d8k.htm)、[Empire 8-K](https://www.sec.gov/Archives/edgar/data/887396/000107261322000337/emp_8k-18602.htm) |

DNB、S 的新旧证券边界及其余档案代码尚待逐个核验；SCG 直接换成档案会丢掉 2017-11 之后的已有数据。不能仅凭代码相同、月数更多或价格曲线连续就批准换源。

最低放行证据：指数成员期对应的发行人/证券身份、数据覆盖有效期、复权声明与独立收益抽查、换源前后的缺口及丢失月份、策略与锁定基线差异。当前没有任何档案代码完成全套放行。
