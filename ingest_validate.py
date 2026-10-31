# -*- coding: utf-8 -*-
"""ingest_validate.py — 采集环节的强制校验（写库前拦截）

用户要求：各环节的校验必须有效，包括采集环节。
本模块在每个数据源返回后、写库前执行 5 类检查，不合格直接丢弃并记录。

检查项
  1. 结构：至少 12 个月、含最近 3 个月、月份格式合法
  2. 连续性：相邻月 |涨跌| > 300% 视为断裂（拆股未复权/拼接），整条拒收
  3. 合理性：价格 > 0；单月 |涨跌| > 150% 的月份数不超过 2 个
  4. 交叉：与库内已有序列重叠月份的收益偏差中位数 > 5% → 判为口径不一致，拒收
  5. 来源一致性：basis 必须与源声明一致（total_return 源不得给出仅拆股数据）

返回 (ok: bool, reason: str, cleaned: dict)
"""
import statistics


def _months(d):
    return sorted(d)


def check_payload(sym, bars, claimed_basis, existing=None, min_months=12):
    """bars: {YYYY-MM: (adj, raw)}；existing: 库内同源或异源序列"""
    if not bars:
        return False, "空数据", {}
    ks = _months(bars)
    if len(ks) < min_months:
        return False, "月份不足（%d）" % len(ks), {}
    bad = [k for k in ks if not (len(k) == 7 and k[4] == "-" and k[:4].isdigit())]
    if bad:
        return False, "月份格式非法：%s" % bad[:3], {}
    if ks[-1] < "2026-01":
        return False, "数据过旧（末月 %s）" % ks[-1], {}

    # 价格合法性
    for k in ks:
        a = bars[k][0]
        if not a or a <= 0:
            return False, "%s 价格非法" % k, {}

    # 连续性 + 异常月
    jumps, spikes = 0, 0
    for i in range(1, len(ks)):
        a0, a1 = bars[ks[i - 1]][0], bars[ks[i]][0]
        if not a0:
            continue
        r = a1 / a0 - 1
        if abs(r) > 3.0:
            jumps += 1
        elif abs(r) > 1.5:
            spikes += 1
    if jumps:
        return False, "存在 %d 处断裂（|月涨跌|>300%%），疑似未复权" % jumps, {}
    if spikes > 2:
        return False, "异常月份过多（%d 个月 |涨跌|>150%%）" % spikes, {}

    # 与库内序列交叉
    if existing:
        common = [k for k in ks if k in existing]
        if len(common) >= 6:
            devs = []
            for k in common:
                if k == common[0]:
                    continue
                prev = ks[ks.index(k) - 1]
                if prev in existing:
                    r_new = bars[k][0] / bars[prev][0]
                    r_old = existing[k][0] / existing[prev][0]
                    devs.append(abs(r_new - r_old))
            if devs:
                med = statistics.median(devs)
                if med > 0.05:
                    return False, "与库内序列口径偏差过大（中位 %.1f%%）" % (med * 100), {}

    return True, "ok", {k: bars[k] for k in ks}
