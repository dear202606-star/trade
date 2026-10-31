# -*- coding: utf-8 -*-
"""plan_gaps.py — 按 source_matrix.json 的 gap_playbook，生成可执行的补数计划

输出：output/gap_plan.json（每个缺口 → 首选/备用源 → 需要多少次调用）
并打印汇总，可直接指导 relay_source.py 的调度。
"""
import csv
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
MATRIX = os.path.join(BASE, "static", "source_matrix.json")
OUT = os.path.join(BASE, "output", "gap_plan.json")
YEARS = list(range(2010, 2026))


def db():
    pw = io.open(os.path.join(BASE, "output", "_supabase_dbpass.txt"), encoding="utf-8").read().strip()
    return psycopg2.connect(host=HOST, port=6543, user="postgres." + REF, password=pw,
                            dbname="postgres", connect_timeout=20, sslmode="require")


def main():
    m = json.load(open(MATRIX, encoding="utf-8"))
    mem = list(csv.DictReader(open(os.path.join(BASE, "static", "members_frozen.csv"), encoding="utf-8")))
    need = sorted({r["ticker"] for r in mem if r["end_date"] >= "2009-01-01"})

    with db() as c, c.cursor() as cur:
        cur.execute("select distinct symbol from prices_monthly")
        have = {r[0] for r in cur.fetchall()}
        cur.execute("""select symbol, substr(month,1,4)||'-12'
                       from prices_monthly where month like '%-12'
                       and month >= '2010-12' and month <= '2025-12'""")
        dec = {}
        for s, mo in cur.fetchall():
            dec.setdefault(s, set()).add(mo)
        cur.execute("select source, basis, count(*) from prices_monthly group by 1,2")
        by_source = [{"source": a, "basis": b, "rows": n} for a, b, n in cur.fetchall()]

    missing_symbols = [t for t in need if t not in have]
    thin = {t: [y for y in YEARS if "%d-12" % y not in dec.get(t, set())] for t in need}
    thin = {t: ys for t, ys in thin.items() if ys}

    total_dec_needed = len(need) * len(YEARS)
    total_dec_have = sum(len(dec.get(t, set())) for t in need)

    plan = {
        "generated": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
        "matrix_version": m["version"],
        "universe": len(need),
        "in_db": len(have),
        "missing_symbols": missing_symbols,
        "missing_symbol_count": len(missing_symbols),
        "dec_expected": total_dec_needed,
        "dec_have": total_dec_have,
        "dec_missing": total_dec_needed - total_dec_have,
        "dec_coverage": round(total_dec_have / max(1, total_dec_needed), 4),
        "symbols_with_gaps": len(thin),
        "gap_by_year": {str(y): total_dec_needed // len(YEARS) - sum(
            1 for t in need if "%d-12" % y in dec.get(t, set())) for y in YEARS},
        "basis_mix": by_source,
        "next_actions": [
            {"action": "alphavantage_backfill",
             "targets": (missing_symbols + sorted(thin, key=lambda t: -len(thin[t]))) [:25],
             "est_calls": min(25, len(missing_symbols) + len(thin)),
             "note": "按缺口排序消耗每日 25 次额度"},
            {"action": "hf_coverage",
             "targets": [t for t in thin if t not in missing_symbols][:120],
             "est_calls": 0,
             "note": "HF 无额度限制，先补现役票的 12 月缺口"},
            {"action": "eodhd_recent_check",
             "targets": "持仓 + 预览前 5 名", "est_calls": 10,
             "note": "近 13 个月交叉校验，偏差 >2% 自动覆盖"},
        ],
        "playbook": m["gap_playbook"],
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(plan, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("标的 %d 只 ｜ 库内 %d ｜ 完全缺失 %d" % (len(need), len(have), len(missing_symbols)))
    print("12 月覆盖 %.1f%%（%d / %d），缺 %d 个 symbol-month，涉及 %d 只标的有缺口" % (
        plan["dec_coverage"] * 100, total_dec_have, total_dec_needed,
        plan["dec_missing"], len(thin)))
    print("各年 12 月缺口:", {k: v for k, v in plan["gap_by_year"].items() if v})
    print("\n口径分布:")
    for r in by_source:
        print("  %-22s %-12s %7d 行" % (r["source"] or "-", r["basis"] or "-", r["rows"]))
    print("\n计划已写:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
