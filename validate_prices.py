# -*- coding: utf-8 -*-
"""validate_prices.py — 数据质量验收（与审计给的 5 条标准一一对应）

① NVDA 2021 ≈ +125%，2024 ≈ +171%
② DUK 2012-07 单月 |涨跌| ≤ 5%
③ AMCR 2025-12 → 最新 应在 +3%~+40%（不是 +468%）
④ 覆盖率：选源覆盖「2009 年后仍在场」的成分股 ≥ 95%
⑤ 连续性：单月 |涨跌| > 150% 的占比 < 0.5%

2026-11-21：验收对象从 static/prices_unified/*.json 换成库内 series_selected。
原链路是 fetch_prices.py → static/prices → build_unified.py → static/prices_unified，
而它从未跑通过：backfill-prices 连续 21 次全部失败在 Alpha Vantage RATE_LIMIT，
`git log --all --grep=回填复权行情` 为空（一次提交都没产生过），本机两个目录同样
是 0 个文件。于是同一道检查在 daily-backfill 里天天红、在 backfill-prices 里被
continue-on-error 吞掉——一条永远红着的门禁和永远绿着一样没有信息量。
series_selected 是 sql/020 之后策略真正读到的每票单源序列，判据、阈值与文案不变，
只是终于量到了存在的东西。
"""
import io
import os
import sys

import psycopg2

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
REF = "fnvahsyrzmmxwovbsqpc"
HOST = "aws-0-ap-northeast-1.pooler.supabase.com"

SPOT_SQL = """
select symbol, month, adj_close
  from series_selected
 where (symbol = 'NVDA' and month in ('2020-12', '2021-12', '2023-12', '2024-12'))
    or (symbol = 'DUK' and month in ('2012-06', '2012-07'))
    or (symbol = 'AMCR' and (month = '2025-12' or
         month = (select max(month) from series_selected where symbol = 'AMCR')))
"""

# 与 allocate_work.py:54 同一口径：end_date 用 2099-12-31 表示仍在场（入库时
# 由 static/members_frozen.csv 原样带入，没有 NULL），所以不需要 coalesce。
COVERAGE_SQL = """
select count(*) filter (where s.symbol is not null) as have,
       count(*) as need
  from (select distinct symbol from members where end_date >= '2009-01-01') m
  left join (select distinct symbol from series_selected) s using (symbol)
"""

AUTHORITATIVE_SQL = "select count(*) from series_pick where source = 'alphavantage'"

CONTINUITY_SQL = """
with step as (
  select adj_close,
         lag(adj_close) over (partition by symbol order by month) as prev
    from series_selected
)
select count(*) filter (where adj_close > 0 and prev > 0) as total,
       count(*) filter (where adj_close > 0 and prev > 0
                             and abs(adj_close / prev - 1) > 1.5) as jumps
  from step
"""


def db():
    """与 db_validate.py 同一惯例：环境变量优先，退回 CI 写入的口令文件。"""
    pw = os.environ.get("SUPABASE_DB_PASSWORD", "")
    if not pw:
        p = os.path.join(BASE, "output", "_supabase_dbpass.txt")
        if os.path.exists(p):
            pw = io.open(p, encoding="utf-8").read().strip()
    return psycopg2.connect(host=HOST, port=6543, user="postgres." + REF, password=pw,
                            dbname="postgres", connect_timeout=20, sslmode="require")


def accept(cur):
    """跑完 5 条标准，返回失败条数。判定全部在这里，便于用夹具复现。"""
    ok = bad = 0

    def ck(cond, name, detail=""):
        nonlocal ok, bad
        if cond:
            ok += 1
        else:
            bad += 1
        print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else "  " + detail))

    cur.execute(SPOT_SQL)
    spot = {(s, m): a for s, m, a in cur.fetchall()}

    def gain(sym, a, b):
        x, y = spot.get((sym, a)), spot.get((sym, b))
        # 必须转 float：库里 adj_close 是 numeric，psycopg2 回的是 Decimal，
        # 与阈值（float）相减直接 TypeError。sqlite 夹具永远复现不出来，
        # 2026-11-22 是拿生产库跑才炸出来的（见 DecimalSpotRegression）。
        return float(y) / float(x) - 1 if x and y else None

    for label, months, target, tol in (
            ("NVDA 2021", ("2020-12", "2021-12"), 1.25, 0.12),
            ("NVDA 2024", ("2023-12", "2024-12"), 1.71, 0.15)):
        r = gain("NVDA", *months)
        ck(r is not None and abs(r - target) < tol,
           "%s ≈ %+.0f%%（实际 %s）" % (label, target * 100,
                                        "%+.1f%%" % (r * 100) if r is not None else "缺数据"),
           "偏差过大：%s" % r)

    r = gain("DUK", "2012-06", "2012-07")
    if r is None:
        ck(False, "DUK 2012-07 可取", "缺数据")
    else:
        ck(abs(r) <= 0.05, "DUK 2012-07 单月 %+.2f%%（应 ≤5%%）" % (r * 100),
           "仍有拆股断点：%+.1f%%" % (r * 100))

    months = sorted(m for (s, m) in spot if s == "AMCR")
    if not months:
        ck(False, "AMCR 2025-12 可取", "缺数据")
    else:
        r = gain("AMCR", "2025-12", months[-1])
        ck(r is not None and 0.03 <= r <= 0.40,
           "AMCR 2025-12→%s %s（合理区间 3%%~40%%）" % (
               months[-1], "%+.1f%%" % (r * 100) if r is not None else "缺数据"),
           "疑似反向拆股未处理：%s" % r)

    cur.execute(COVERAGE_SQL)
    have, need = cur.fetchone()
    cur.execute(AUTHORITATIVE_SQL)
    print("    （选源为权威源的 %d 只，其余来自接力/本地修复）" % cur.fetchone()[0])
    ck(need > 0 and have >= 0.95 * need,
       "覆盖率 %.1f%%（%d/%d）" % (100.0 * have / max(1, need), have, need),
       "低于 95%%，需继续回填（%d/%d）" % (have, need))

    cur.execute(CONTINUITY_SQL)
    total, jumps = cur.fetchone()
    # total=0 时占比恒为 0，打印出来的「0.000%」说的是「一行都没看」，
    # 不是「数据连续」。样本为空必须显式失败，判定与样本量一起打印出来。
    rate = jumps / total if total else 0.0
    ck(total > 0 and rate < 0.005,
       "连续性：%d 段月环比，|涨跌|>150%% 占比 %.3f%%（应 <0.5%%）" % (total, rate * 100),
       "样本为空（0 段）无从判定，或跳变过多：%d/%d" % (jumps, total))

    print("\n验收：通过 %d · 失败 %d" % (ok, bad))
    return bad


def main():
    conn = db()
    try:
        with conn.cursor() as cur:
            bad = accept(cur)
    finally:
        conn.close()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
