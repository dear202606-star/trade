# -*- coding: utf-8 -*-
"""relay_source.py — 多源接力补全（按口径分级 + 逐月取最优源）

决策依据（2026-10-31 已确认）
  · 价格口径：全收益（拆股+分红复权）
  · 补全方式：多源接力，逐月取「质量最高」的源
  · 缺失 12 月：允许从其它源接力拼接；拼接不到才剔除

源分级（数字越小越优先）
  1 alphavantage   全收益（adjusted_close），含退市票，权威
  2 eodhd          全收益，但免费层仅近 13 个月
  3 hf_ayushkhaire 拆股复权、无分红（价格收益），覆盖面广（仅现役票）
  4 sina_repaired  本地修复（拆股中和），无分红

写入 prices_monthly(symbol, month, adj_close, raw_close, source, quality, basis)
同一 (symbol, month) 允许多源并存，取其最优进 dataset。

用法
  python relay_source.py --symbols 30 --av-quota 24     # 接力补 30 只（AV 用 24 次额度）
  python relay_source.py --gaps                          # 只补缺口月份
"""
import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.request

import psycopg2
import psycopg2.extras

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
REF = "fnvahsyrzmmxwovbsqpc"
HOST = "aws-0-ap-northeast-1.pooler.supabase.com"
UA = {"User-Agent": "Mozilla/5.0"}

# 源等级与口径
# 优先级与口径从 static/source_matrix.json 读取（策略即配置）
_MATRIX = os.path.join(BASE, "static", "source_matrix.json")
_DEFAULT = {
    "alphavantage":   {"rank": 1, "basis": "total_return"},
    "eodhd":          {"rank": 2, "basis": "total_return"},
    "hf_ayushkhaire": {"rank": 3, "basis": "price_only"},
    "sina_repaired":  {"rank": 4, "basis": "price_only"},
}
SOURCES = dict(_DEFAULT)
try:
    if os.path.exists(_MATRIX):
        _m = json.load(open(_MATRIX, encoding="utf-8"))
        _order = {"history_primary": 1, "recent_crosscheck": 2,
                  "third_party_check": 2, "coverage_bulk": 3,
                  "delisted_bridge": 4}
        for _s in _m.get("sources", []):
            _id = _s.get("id")
            if _id in SOURCES and _s.get("role") in _order:
                SOURCES[_id]["rank"] = _order[_s["role"]]
                _adj = (_s.get("adjustment") or "").lower()
                SOURCES[_id]["basis"] = ("total_return" if "total" in _adj or "全收益" in _adj
                                          else "price_only")
except Exception as _e:
    print("source_matrix 读取失败，用默认优先级：%s" % _e)
AV = ("https://www.alphavantage.co/query?function=TIME_SERIES_MONTHLY_ADJUSTED"
      "&symbol=%s&apikey=%s")
EOD = ("https://eodhd.com/api/eod/%s.US?api_token=%s&fmt=json&period=m&order=a")
HF = ("https://huggingface.co/datasets/AYUSHKHAIRE/all-stock-market-data-daily-updates"
      "/resolve/main/%s/%s.csv")


def load_priority_from_db():
    """从 Supabase 的 source_registry 读取调度顺序（唯一真相），失败则用内置默认"""
    try:
        import psycopg2 as _pg
        pw = io.open(os.path.join(BASE, "output", "_supabase_dbpass.txt"), encoding="utf-8").read().strip()
        c = _pg.connect(host=HOST, port=6543, user="postgres." + REF, password=pw,
                        dbname="postgres", connect_timeout=10, sslmode="require")
        cur = c.cursor()
        cur.execute("""select source_id, priority, basis from source_registry
                       where priority is not null and status in ('ok','untested')
                       order by priority""")
        rows = cur.fetchall()
        c.close()
        out = {}
        for sid, pri, basis in rows:
            key = {"sina": "sina_repaired"}.get(sid, sid)
            if key in SOURCES:
                SOURCES[key]["rank"] = pri
                SOURCES[key]["basis"] = basis or SOURCES[key]["basis"]
                out[key] = pri
        return out
    except Exception as e:
        print("读 source_registry 失败，用内置优先级：%s" % str(e)[:60])
        return {}


def keys():
    k = {}
    if os.path.exists(os.path.join(BASE, "output", "_api_keys.local.txt")):
        for line in io.open(os.path.join(BASE, "output", "_api_keys.local.txt"), encoding="utf-8"):
            if "=" in line:
                a, b = line.split("=", 1)
                k[a.strip()] = b.strip()
    return k


def db():
    pw = io.open(os.path.join(BASE, "output", "_supabase_dbpass.txt"), encoding="utf-8").read().strip()
    return psycopg2.connect(host=HOST, port=5432 if False else 6543,
                            user="postgres." + REF, password=pw,
                            dbname="postgres", connect_timeout=20, sslmode="require")


def get_json(url, timeout=45):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=timeout).read().decode())


# ---------- 各源适配器：返回 {YYYY-MM: (adj, raw)} ----------
def fetch_av(sym, key):
    d = get_json(AV % (sym, key))
    s = d.get("Monthly Adjusted Time Series")
    if not s:
        return {}
    return {d0[:7]: (float(v["5. adjusted close"]),
                     float(v["4. close"]) if v.get("4. close") else None)
            for d0, v in s.items()}


def fetch_eodhd(sym, key):
    try:
        d = get_json(EOD % (sym.replace(".", "-"), key))
    except Exception:
        return {}
    out = {}
    for x in d:
        if x.get("adjusted_close"):
            out[x["date"][:7]] = (float(x["adjusted_close"]), float(x.get("close") or 0) or None)
    return out


def fetch_hf(sym, key=None):
    """HF 每日 CSV → 月末收盘；时间戳是 Unix 秒。只有拆股复权，无分红"""
    import datetime as _dt
    url = HF % (sym[0].upper(), sym.upper())
    try:
        raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()
    except Exception:
        return {}
    txt = raw.decode("utf-8", "replace").strip().split("\n")
    if len(txt) < 2:
        return {}
    hdr = [h.strip().lower() for h in txt[0].split(",")]
    try:
        ti = hdr.index("timestamp") if "timestamp" in hdr else hdr.index("date")
        ci = hdr.index("close")
    except ValueError:
        return {}
    monthly = {}
    for line in txt[1:]:
        p = line.split(",")
        if len(p) <= max(ti, ci):
            continue
        tok = p[ti].strip()
        try:
            if tok.isdigit() or (tok[:1].isdigit() and tok.replace(".", "").isdigit()):
                d0 = _dt.datetime.utcfromtimestamp(int(float(tok))).strftime("%Y-%m-%d")
            else:
                d0 = tok[:10]
            ym = d0[:7]
            if not (len(ym) == 7 and ym[4] == "-" and ym[:4].isdigit()):
                continue
            monthly[ym] = float(p[ci])           # 逐日覆盖 → 最终留下月末值
        except Exception:
            continue
    return {m: (v, v) for m, v in monthly.items()}


def sina_local(sym):
    p = os.path.join(BASE, "static", "prices_unified", sym + ".json")
    if not os.path.exists(p):
        return {}
    d = json.load(open(p, encoding="utf-8"))
    return {r[0]: (r[1], r[1]) for r in d["bars"]}


def relay(sym, k, av_left):
    """按优先级逐源取数，返回该标的的候选行 [(sym,month,adj,raw,source,quality,basis)]"""
    rows, used_av = [], False
    got = {}
    for src, fn in (("alphavantage", lambda: fetch_av(sym, k.get("alphavantage_key", ""))),
                    ("eodhd", lambda: fetch_eodhd(sym, k.get("eodhd_token", ""))),
                    ("hf_ayushkhaire", lambda: fetch_hf(sym)),
                    ("sina_repaired", lambda: sina_local(sym))):
        if src == "alphavantage" and av_left <= 0:
            continue
        if src == "alphavantage" and not k.get("alphavantage_key"):
            continue
        try:
            data = fn()
        except Exception as e:
            print("    ! %s/%s 失败 %s" % (sym, src, str(e)[:50]))
            data = {}
        if src == "alphavantage" and data:
            used_av = True
        for m, (adj, raw) in (data or {}).items():
            if m in got or not adj:
                continue
            got[m] = (adj, raw, src)
    for m, (adj, raw, src) in got.items():
        rows.append((sym, m, adj, raw, src, "ok", SOURCES[src]["basis"]))
    return rows, used_av, len(got)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=int, default=20, help="本次处理多少只")
    ap.add_argument("--av-quota", type=int, default=24, help="本次允许消耗的 AV 调用数")
    ap.add_argument("--gaps", action="store_true", help="只处理有缺口的标的")
    ap.add_argument("--no-av", action="store_true", help="不消耗 Alpha Vantage 额度")
    ap.add_argument("--plan-source", default="", help="按 work_plan.json 里某个源的任务清单执行")
    a = ap.parse_args()
    k = keys()
    _prio = load_priority_from_db()
    if _prio:
        print("调度顺序（来自 source_registry）:", dict(sorted(_prio.items(), key=lambda x: x[1])), flush=True)

    # 目标标的：优先「完全缺失」和「缺口最大」的
    with db() as c, c.cursor() as cur:
        cur.execute("select distinct symbol from prices_monthly")
        have = {r[0] for r in cur.fetchall()}
        mem = list(csv.DictReader(open(os.path.join(BASE, "static", "members_frozen.csv"), encoding="utf-8")))
        need = sorted({r["ticker"] for r in mem if r["end_date"] >= "2009-01-01"})
        cur.execute("""select symbol, count(*) filter (where month like '%-12') as dec_n
                       from prices_monthly group by 1""")
        dec = dict(cur.fetchall())
    missing = [t for t in need if t not in have]
    thin = sorted([t for t in need if t in have and dec.get(t, 0) < 15],
                  key=lambda t: dec.get(t, 0))
    if a.plan_source:
        import json as _json
        _pl = _json.load(open(os.path.join(BASE, "output", "work_plan.json"), encoding="utf-8"))
        for _a in _pl["assignments"]:
            if _a["source"] == a.plan_source:
                targets = _a["symbols"][:a.symbols]
                print("按计划执行 %s：%d 只" % (a.plan_source, len(targets)))
                break
        else:
            targets = []
    else:
        targets = (missing + thin) if a.gaps else (missing + thin)[:a.symbols]
        targets = targets[:a.symbols]
    print("待处理 %d 只（完全缺失 %d / 12 月不足 %d）" % (len(targets), len(missing), len(thin)))

    av_left = 0 if a.no_av else a.av_quota
    done, stats = 0, {}
    all_rows = []
    for t in targets:
        rows, used, n = relay(t, k, av_left)
        if used:
            av_left -= 1
        all_rows += rows
        for r in rows:
            stats[r[4]] = stats.get(r[4], 0) + 1
        print("  %-6s %s → %d 个月" % (t, "AV" if used else "接力", n), flush=True)
        done += 1
        time.sleep(0.8)

    with db() as c, c.cursor() as cur:
        psycopg2.extras.execute_values(
            cur, """insert into prices_monthly(symbol,month,adj_close,raw_close,source,quality,basis)
                    values %s
                    on conflict (symbol,month,source) do update
                    set adj_close=excluded.adj_close, raw_close=excluded.raw_close,
                        basis=excluded.basis, fetched_at=now()""",
            all_rows, page_size=5000)
        cur.execute("""insert into fetch_runs(source,finished_at,requested,ok,failed,note)
                       values ('relay',now(),%s,%s,%s,%s)""",
                    (done, sum(1 for r in all_rows), 0, json.dumps(stats)))
    print("写入 %d 行，来源分布 %s，AV 剩余额度 %d" % (len(all_rows), stats, av_left))
    return 0


if __name__ == "__main__":
    sys.exit(main())
