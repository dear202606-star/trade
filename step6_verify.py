# -*- coding: utf-8 -*-
"""Step 6: verify published champions against our price data."""
import os, json
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "output")

# year, ticker, company_cn, published_return, sector, driver
CHAMPS = [
    (2006, "ATI",  "阿勒格尼技术(钛材)",      1.500, "特种金属", "航空/能源用钛材需求爆发、钛价上涨"),
    (2007, "NOV",  "National Oilwell Varco", 1.280, "油气设备", "油价冲100美元，钻机与深海资本开支激增"),
    (2008, "FDO",  "Family Dollar 折扣零售",  0.270, "折扣零售", "危机中消费降级，必需消费避险"),
    (2009, "XL",   "XL Capital 再保险",       3.954, "保险",     "次贷损失出清+再保险定价回升，困境反转"),
    (2010, "CMI",  "康明斯 Cummins",          1.354, "工业机械", "中印巴重卡与发电设备需求爆发"),
    (2011, "COG",  "Cabot Oil & Gas",         1.288, "页岩气",   "Marcellus产量+39%，逆气价走强"),
    (2012, "PHM",  "PulteGroup 住宅建筑",     1.690, "住宅建筑", "美国房市触底，建筑商集体重估"),
    (2013, "NFLX", "Netflix",                 2.980, "流媒体",   "《纸牌屋》原创内容+订阅大增"),
    (2014, "LUV",  "西南航空",                1.200, "航空",     "油价暴跌降成本+运力纪律"),
    (2015, "NFLX", "Netflix",                 1.340, "流媒体",   "全球订阅加速，国际业务接近盈亏平衡"),
    (2016, "NVDA", "英伟达",                  2.238, "半导体",   "Pascal架构GPU受益深度学习"),
    (2017, "NRG",  "NRG Energy",              1.350, "电力",     "激进投资者推动重组降杠杆+回购"),
    (2018, "AMD",  "超威半导体",              0.730, "半导体",   "Ryzen/EPYC抢份额"),
    (2019, "AMD",  "超威半导体",              1.480, "半导体",   "7nm Zen2 + EPYC Rome 上市"),
    (2020, "TSLA", "特斯拉",                  7.431, "电动车",   "连续季度盈利+交付超预期+纳入标普"),
    (2021, "DVN",  "Devon Energy",            1.790, "油气",     "油价回升+WPX合并降本+可变股息"),
    (2022, "OXY",  "西方石油",                1.190, "油气",     "俄乌战争推高油价+伯克希尔增持"),
    (2023, "NVDA", "英伟达",                  2.390, "半导体",   "ChatGPT引发AI算力军备竞赛"),
    (2024, "PLTR", "Palantir",                3.410, "软件/AI",  "AIP商业化加速，9月纳入标普"),
    (2025, "SNDK", "闪迪 Sandisk",            5.590, "存储",     "从WDC分拆，AI数据中心NAND缺货涨价"),
]

SPX = {2006: 0.1362, 2007: 0.0353, 2008: -0.3849, 2009: 0.2345, 2010: 0.1278,
       2011: 0.0000, 2012: 0.1341, 2013: 0.2960, 2014: 0.1139, 2015: -0.0073,
       2016: 0.0954, 2017: 0.1942, 2018: -0.0624, 2019: 0.2888, 2020: 0.1626,
       2021: 0.2689, 2022: -0.1944, 2023: 0.2423, 2024: 0.2331, 2025: 0.1639}


def my_ret(tk, y):
    try:
        d = json.load(open(os.path.join(DATA, "sina2", f"{tk}.json"), encoding="utf-8"))
    except Exception:
        return None
    m = {b[0]: b[1] for b in d["bars"]}
    a, b = m.get(f"{y-1}-12"), m.get(f"{y}-12")
    return (b / a - 1) if a and b else None


rows = []
for y, tk, name, pub, sec, why in CHAMPS:
    r = my_ret(tk, y)
    match = "" if r is None else ("OK" if abs(r - pub) < 0.08 else "差异")
    rows.append({"year": y, "ticker": tk, "name": name, "ret_published": pub,
                 "ret_mydata": r, "diff": (r - pub) if r is not None else None,
                 "check": match, "sector": sec, "driver": why, "spx": SPX[y],
                 "excess": pub - SPX[y]})
df = pd.DataFrame(rows)
df.to_csv(os.path.join(OUT, "champions_verified.csv"), index=False, encoding="utf-8-sig")
print(df[["year", "ticker", "ret_published", "ret_mydata", "diff", "check", "spx"]].to_string(index=False))
print("\npublished usable years:", df.ret_published.notna().sum())
print("mydata coverage:", df.ret_mydata.notna().sum())
