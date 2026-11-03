# -*- coding: utf-8 -*-
"""sensitivity.py — 缺口对结论的影响有多大（缺口语义：剩下的 16% 会改变结果吗）

做法
  用三套池子各跑一遍同样的策略（年末选前 3、行业≤2、权重≤60%）：
    A 当前口径   全部可用标的（含未复权）
    B 纯全收益   只用 total_return 标的（池子更小但口径统一）
    C 纯价格     只用 price_only 标的（极端对照）
  比较三者的年化与最大回撤。差距小 → 缺口不影响结论；差距大 → 必须补完才能定稿。
"""
import io
import json
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
Y0, Y1 = 2012, 2025
MAXW = 0.60


def db():
    pw = os.environ.get("SUPABASE_DB_PASSWORD", "")
    if not pw:
        p = os.path.join(BASE, "output", "_supabase_dbpass.txt")
        if os.path.exists(p):
            pw = io.open(p, encoding="utf-8").read().strip()
    c = psycopg2.connect(host=HOST, port=6543, user="postgres." + REF, password=pw,
                         dbname="postgres", connect_timeout=15, sslmode="require")
    c.autocommit = True
    return c


def load(basis_filter):
    """分批取，避免一次性拉 14 万行把连接压断"""
    series, sector, active, span = {}, {}, set(), {}
    for y in range(2011, 2026):
        c = db()
        with c.cursor() as cur:
            cur.execute("""select symbol, month, adj_close, basis from series_selected
                           where month between %s and %s""", ('%d-01' % y, '%d-12' % y))
            for s, m, v, b in cur.fetchall():
                if basis_filter and b != basis_filter:
                    continue
                series.setdefault(s, {})[m] = float(v)
        c.close()
    c = db()
    with c.cursor() as cur:
        cur.execute('select symbol, sector from symbols where sector is not null')
        sector = dict(cur.fetchall())
        cur.execute("""select symbol, min(start_date)::text, max(end_date)::text
                         from members where end_date >= '2010-01-01' group by symbol""")
        span = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    c.close()
    return series, sector, active, span


def members_at(span, year):
    day = "%d-12-31" % year
    return {s for s, (a, b) in span.items() if a <= day <= b}


def backtest(series, sector, span):
    eq = 1.0
    peak = 1.0
    dd = 0.0
    years = []
    for y in range(Y0, Y1 + 1):
        pool = []
        for s in members_at(span, y):
            d = series.get(s)
            if not d:
                continue
            # 排名用 Dec(y-2)→Dec(y-1)，持有用 Dec(y-1)→Dec(y)
            mr0, mr1 = "%d-12" % (y - 2), "%d-12" % (y - 1)
            m0, m1 = "%d-12" % (y - 1), "%d-12" % y
            if mr0 not in d or mr1 not in d or m0 not in d or m1 not in d:
                continue
            if d[mr0] <= 0 or d[m0] <= 0:
                continue
            # 防线1：排名年与持有年内任意相邻月涨跌 >150% 视为数据断裂
            ms = sorted(k for k in d if k.startswith(str(y - 1)) or k.startswith(str(y)))
            prev = mr0
            broken = False
            for mm in ms:
                a, b2 = d.get(prev), d.get(mm)
                if a and b2 and abs(b2 / a - 1) > 1.5:
                    broken = True
                    break
                prev = mm
            if broken:
                continue
            rank_r = d[mr1] / d[mr0] - 1.0
            # 防线2：排名用的 12 个月 |收益| > 500% 视为异常
            if abs(rank_r) > 5.0:
                continue
            pool.append((s, rank_r, d[m1] / d[m0] - 1.0))
        if not pool:
            continue
        pool.sort(key=lambda x: -x[1])
        pick, sec = [], {}
        for s, rank_r, hold_r in pool:
            k = sector.get(s, "?")
            if sec.get(k, 0) >= 2:
                continue
            pick.append((s, hold_r))
            sec[k] = sec.get(k, 0) + 1
            if len(pick) == 3:
                break
        if not pick:
            continue
        w = min(1.0 / len(pick), MAXW)
        ret = sum(r * w for _, r in pick)
        eq *= (1 + ret)
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1)
        years.append((y, ret, len(pool)))
    cagr = eq ** (1.0 / max(1, len(years))) - 1 if years else 0
    return eq, cagr, dd, years


def main():
    data_all = load(None)
    data_tr = load("total_return")
    data_po = load("price_only")

    print("%-14s %10s %10s %10s %8s" % ("口径", "累计倍数", "年化", "最大回撤", "池子均值"))
    out = {}
    for label, (series, sector, active, span) in (
            ("A 当前（混合）", data_all),
            ("B 纯全收益", data_tr),
            ("C 纯价格", data_po)):
        eq, cagr, dd, years = backtest(series, sector, span)
        avg = sum(p for _, _, p in years) / max(1, len(years))
        out[label] = dict(eq=eq, cagr=cagr, dd=dd, avg=avg, years=years)
        print("%-14s %10.2f %9.2f%% %9.1f%% %8.0f" % (label, eq, cagr * 100, dd * 100, avg))

    a, b = out["A 当前（混合）"], out["B 纯全收益"]
    print("\nA vs B：年化差 %.2f 个百分点，累计倍数差 %.1f%%"
          % ((a["cagr"] - b["cagr"]) * 100, (a["eq"] / b["eq"] - 1) * 100))
    print("逐年差异（A-B）：")
    for (ya, ra, _), (yb, rb, _) in zip(a["years"], b["years"]):
        flag = "  ← 差异大" if abs(ra - rb) > 0.05 else ""
        print("  %d  A %+7.2f%%  B %+7.2f%%  差 %+6.2f%%%s" % (ya, ra * 100, rb * 100, (ra - rb) * 100, flag))


if __name__ == "__main__":
    main()
