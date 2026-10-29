# -*- coding: utf-8 -*-
"""修复 monthly_pick 的「未来函数」：排名必须用排名日的时点成分股。

改动点：
  1. 新增 members_at(ym) —— 从时点成分表取该月末的成分集合
  2. 抓取清单 = 当前成分 ∪ 排名涉及月份的成分（保证历史时点成员也有数据）
  3. ranking_at 内部按 as-of 月份过滤，只保留当时属于标普 500 的股票
"""
import io
import re

P = "monthly_pick.py"
s = io.open(P, encoding="utf-8").read()

# 1) 引入 datafeed 并加 members_at
if "def members_at(" not in s:
    anchor = "def nice_ratio("
    helper = '''_MEMBERS = None


def members_at(ym):
    """返回 yyyy-mm 月末属于标普 500 的股票集合（时点成分，防未来函数）"""
    global _MEMBERS
    if _MEMBERS is None:
        import datafeed
        m = datafeed.load_members()
        m["start_date"] = m["start_date"].astype(str)
        m["end_date"] = m["end_date"].fillna("2099-12-31").astype(str)
        _MEMBERS = m
    import calendar
    y, mm = int(ym[:4]), int(ym[5:7])
    day = "%04d-%02d-%02d" % (y, mm, calendar.monthrange(y, mm)[1])
    m = _MEMBERS
    return set(m[(m["start_date"] <= day) & (m["end_date"] >= day)]["ticker"].unique().tolist())


def nice_ratio('''
    s = s.replace(anchor, helper, 1)

# 2) ranking_at 内部按 as-of 月份过滤
old_loop = '''    rows = []
    for tk, df in data.items():
        if tk == "SPY":
            continue
        if base not in df.index or m not in df.index:
            continue'''
new_loop = '''    rows = []
    live = members_at(m)          # 当时属于标普 500 的股票
    for tk, df in data.items():
        if tk == "SPY":
            continue
        if tk not in live:        # 排名日还没纳入的股票不能入选
            continue
        if base not in df.index or m not in df.index:
            continue'''
assert old_loop in s, "ranking_at 循环未找到"
s = s.replace(old_loop, new_loop, 1)

# 3) 抓取清单补上「历史时点成员」
old_tk = '''    tickers = sp.ticker.tolist()
    if args.limit:
        tickers = tickers[: args.limit]'''
new_tk = '''    tickers = sp.ticker.tolist()
    # 补上排名时需要的历史时点成员（当前名单可能新增/剔除过）
    try:
        _prev_y = dt.datetime.now(dt.timezone.utc).year - 1
        for _ym in ("%d-12" % _prev_y, "%d-01" % _prev_y):
            tickers += [t for t in members_at(_ym) if t not in tickers]
    except Exception as _e:
        print("      WARN 时点成分表不可用：%s" % _e, flush=True)
    tickers = list(dict.fromkeys(tickers))
    if args.limit:
        tickers = tickers[: args.limit]'''
assert old_tk in s, "tickers 初始化未找到"
s = s.replace(old_tk, new_tk, 1)

io.open(P, "w", encoding="utf-8").write(s)
print("monthly_pick 已修复未来函数")
