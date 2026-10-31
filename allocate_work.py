# -*- coding: utf-8 -*-
"""allocate_work.py — 额度感知的补数与校验分工（不重复劳动）

分工原则
  1. 稀缺额度（Alpha Vantage 25/天）只做「别人做不了」的活：退市票 + 缺口最大的票
  2. 无限额度（HF）负责现役票的覆盖，先便宜地补齐，避免浪费 AV
  3. EODHD 免费层只有近 13 个月 → 只做「当年数据校验」，不做历史
  4. 本地新浪只做「已退市且 AV 还没轮到」的桥接
  5. 校验抽样按源切分：不同源校验不同样本，互不重复

产物
  output/work_plan.json   每个源的任务清单（互斥）
  DB 表 work_plans        计划留痕
"""
import csv
import io
import json
import os
import random
import sys
import time

import psycopg2

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
REF = "fnvahsyrzmmxwovbsqpc"
HOST = "aws-0-ap-northeast-1.pooler.supabase.com"
OUT = os.path.join(BASE, "output", "work_plan.json")
YEARS = list(range(2010, 2026))

# 每日额度（免费层）
QUOTA = {"alphavantage": 25, "eodhd": 20}
# 校验抽样切分（互斥）
VALIDATION_SPLIT = {"alphavantage": 15, "hf_ayushkhaire": 15,
                    "hf_amirtrader": 15, "eodhd": 10}


def db():
    pw = io.open(os.path.join(BASE, "output", "_supabase_dbpass.txt"), encoding="utf-8").read().strip()
    return psycopg2.connect(host=HOST, port=6543, user="postgres." + REF, password=pw,
                            dbname="postgres", connect_timeout=20, sslmode="require")


def main():
    mem = list(csv.DictReader(open(os.path.join(BASE, "static", "members_frozen.csv"), encoding="utf-8")))
    need = sorted({r["ticker"] for r in mem if r["end_date"] >= "2009-01-01"})
    # 退市判定：end_date 不是占位符
    delisted = {r["ticker"] for r in mem
                if r["end_date"] < "2099-01-01" and r["end_date"] >= "2009-01-01"}

    with db() as c, c.cursor() as cur:
        cur.execute("select distinct symbol from prices_monthly")
        have = {r[0] for r in cur.fetchall()}
        cur.execute("""select symbol, substr(month,1,4)||'-12' from prices_monthly
                       where month like '%-12' and month between '2010-12' and '2025-12'""")
        dec = {}
        for s, m in cur.fetchall():
            dec.setdefault(s, set()).add(m)
        cur.execute("""select symbol from prices_monthly where basis='total_return'""")
        tr = {r[0] for r in cur.fetchall()}

    def gaps(t):
        return [y for y in YEARS if "%d-12" % y not in dec.get(t, set())]

    scored = []
    for t in need:
        g = gaps(t)
        if not g:
            continue
        score = len(g) * (1.0 if t in delisted else 0.55) \
            + (3.0 if t not in have else 0) + (1.0 if t not in tr else 0)
        scored.append((t, len(g), t in delisted, score))
    scored.sort(key=lambda x: -x[3])

    # ---- 分工 ----
    av_targets = [t for t, g, d, s in scored if d][:QUOTA["alphavantage"]]
    av_set = set(av_targets)
    # HF 只做现役票（退市票它没有），且跳过 AV 今天要处理的
    hf_targets = [t for t, g, d, s in scored if not d and t not in av_set][:120]
    hf_set = set(hf_targets)
    # 新浪桥接：退市票里 AV 今天没排上的
    sina_targets = [t for t, g, d, s in scored if d and t not in av_set][:60]
    # EODHD：仅近 13 个月校验（不是历史补数）
    sig = os.path.join(BASE, "docs", "data", "b", "signal.json")
    pv = os.path.join(BASE, "docs", "data", "b", "preview.json")
    recent = []
    if os.path.exists(sig):
        recent += [p["ticker"] for p in json.load(open(sig, encoding="utf-8")).get("official") or []]
    if os.path.exists(pv):
        recent += [p["ticker"] for p in (json.load(open(pv, encoding="utf-8")).get("top") or [])[:5]]
    recent = list(dict.fromkeys(recent))[:QUOTA["eodhd"]]

    # ---- 校验抽样：互斥切分 ----
    pool = [t for t in need if t in have]
    random.seed(20261031)
    shuffled = pool[:]
    random.shuffle(shuffled)
    val, i = {}, 0
    for src, n in VALIDATION_SPLIT.items():
        take = [t for t in shuffled[i:i + n] if t not in set(av_targets) | hf_set]
        val[src] = take
        i += n

    plan = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "universe": len(need), "delisted": len(delisted),
        "gaps_total": sum(1 for t in need if gaps(t)),
        "assignments": [
            {"source": "alphavantage", "task": "历史补数（退市票优先）",
             "symbols": av_targets, "quota": QUOTA["alphavantage"],
             "why": "唯一免费且含退市+全收益的源，额度必须只花在别人做不了的票上"},
            {"source": "hf_ayushkhaire", "task": "覆盖兜底（现役票 12 月缺口）",
             "symbols": hf_targets, "quota": None,
             "why": "无额度限制，先便宜补齐现役票，减少 AV 消耗"},
            {"source": "sina_local", "task": "桥接（退市票，AV 未轮到前）",
             "symbols": sina_targets, "quota": None,
             "why": "本地已有，零成本，避免退市票空窗"},
            {"source": "eodhd", "task": "当年数据交叉校验（近 13 个月）",
             "symbols": recent, "quota": QUOTA["eodhd"],
             "why": "免费层只有 13 个月，正好覆盖持仓与预览，不与历史补数重叠"},
        ],
        "validation": {
            "policy": "不同源校验不同样本，互不重复",
            "split": {k: len(v) for k, v in val.items()},
            "samples": val,
        },
        "no_overlap_check": {
            "av ∩ hf": len(av_set & hf_set),
            "av ∩ sina": len(av_set & set(sina_targets)),
            "hf ∩ sina": len(hf_set & set(sina_targets)),
        },
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(plan, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    with db() as c, c.cursor() as cur:
        cur.execute("""create table if not exists work_plans(
                         id serial primary key, generated timestamptz default now(),
                         plan jsonb)""")
        cur.execute("insert into work_plans(plan) values (%s)",
                    (json.dumps(plan, ensure_ascii=False),))

    print("标的 %d ｜ 有缺口 %d ｜ 已退市 %d" % (len(need), plan["gaps_total"], len(delisted)))
    for a in plan["assignments"]:
        print("  %-16s %-32s %3d 只%s" % (a["source"], a["task"], len(a["symbols"]),
                                          "（额度 %d）" % a["quota"] if a["quota"] else ""))
    print("  校验抽样:", {k: len(v) for k, v in val.items()})
    print("  重叠检查:", plan["no_overlap_check"], "（应全为 0）")
    print("\n计划已写:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
