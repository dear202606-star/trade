# -*- coding: utf-8 -*-
"""迁移前基线快照：把「页面数字 + 文件 sha + 库指纹」固化成一个可比对的 JSON。"""
import hashlib, io, json, os, subprocess, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = r"C:\Users\Administrator\Desktop\量化"
OUT = os.path.join(BASE, "output", "migration_baseline.json")

def sha(p):
    h = hashlib.sha256()
    try:
        with open(p, "rb") as f:
            for c in iter(lambda: f.read(1 << 20), b""):
                h.update(c)
        return h.hexdigest()
    except Exception as e:
        return "ERR:%s" % str(e)[:40]

def js(p):
    try:
        return json.load(io.open(p, encoding="utf-8"))
    except Exception:
        return None

def rel(*a):
    return os.path.join(BASE, *a)

B = {"captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "git_head": None,
     "files": {}, "page_numbers": {}, "db": {}}

try:
    B["git_head"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=BASE,
                                             text=True).strip()
    B["git_status_dirty"] = bool(subprocess.check_output(["git", "status", "--porcelain"],
                                                         cwd=BASE, text=True).strip())
except Exception as e:
    B["git_head"] = "ERR:%s" % e

FILES = [
    "docs/data/b/strategy_history.json", "docs/data/b/curve.json",
    "docs/data/b/equity.json", "docs/data/b/signal.json",
    "docs/data/b/preview.json", "docs/data/strategy_history.json",
    "docs/data/curve.json", "docs/data/equity.json", "docs/data/signal.json",
    "docs/data/quality.json", "docs/data/health.json", "docs/data/quotes.json",
    "docs/data/cloud.json",
    "static/members_frozen.csv", "static/sector_frozen.json", "static/cloud.json",
    "static/bars_monthly.json",
    "output/signal_history.json", "output/_prices_cache.json",
    "output/baseline_b.json", "output/pipeline_status.json",
]
for f in FILES:
    p = rel(*f.split("/"))
    B["files"][f] = {"sha256": sha(p), "bytes": os.path.getsize(p) if os.path.exists(p) else None}

h = js(rel("docs", "data", "b", "strategy_history.json")) or {}
c = js(rel("docs", "data", "b", "curve.json")) or {}
e = js(rel("docs", "data", "b", "equity.json")) or {}
s = js(rel("docs", "data", "b", "signal.json")) or {}
q = js(rel("docs", "data", "quality.json")) or {}
p2 = js(rel("docs", "data", "b", "preview.json")) or {}

B["page_numbers"] = {
    "B.input_sha": h.get("input_sha"),
    "B.signal.input_sha": s.get("input_sha"),
    "B.generated_at": h.get("generated_at"),
    "B.curve.stats": c.get("stats"),
    "B.summary.total": (h.get("summary") or {}).get("total"),
    "B.summary.cagr": (h.get("summary") or {}).get("cagr"),
    "B.summary.worst_intra": (h.get("summary") or {}).get("worst_intra"),
    "B.summary.benchmark": (h.get("summary") or {}).get("benchmark"),
    "B.years.n": len(h.get("years") or []),
    "B.years.ret": {str(r.get("year")): r.get("ret") for r in (h.get("years") or [])},
    "B.years.holdings": {str(r.get("year")): [x.get("ticker") for x in (r.get("holdings") or [])]
                         for r in (h.get("years") or [])},
    "B.curve.points": len(c.get("points") or []),
    "B.curve.last": (c.get("points") or [{}])[-1] if c.get("points") else None,
    "B.equity.position": [x.get("ticker") for x in (e.get("position") or [])],
    "B.signal.official": [x.get("ticker") for x in (s.get("official") or [])],
    "B.signal.month": s.get("month"),
    "preview.year": p2.get("year"),
    "quality.total_return_ratio": q.get("total_return_ratio"),
    "quality.degraded": q.get("degraded"),
    "quality.summary": q.get("summary"),
}

try:
    import psycopg2
    PW = io.open(rel("output", "_supabase_dbpass.txt"), encoding="utf-8").read().strip()
    cc = psycopg2.connect(host="aws-0-ap-northeast-1.pooler.supabase.com", port=6543,
                          user="postgres.fnvahsyrzmmxwovbsqpc", password=PW,
                          dbname="postgres", connect_timeout=10, sslmode="require")
    cc.autocommit = False
    cur = cc.cursor()
    cur.execute("set local statement_timeout='300s'")
    FP = {}
    for t, cols in [
      ("prices_monthly", "symbol||'|'||month||'|'||coalesce(adj_close::text,'')||'|'||coalesce(raw_close::text,'')||'|'||coalesce(source,'')||'|'||coalesce(basis,'')"),
      ("series_selected", "symbol||'|'||month||'|'||coalesce(adj_close::text,'')||'|'||coalesce(raw_close::text,'')||'|'||coalesce(source,'')||'|'||coalesce(basis,'')"),
      ("members", "symbol||'|'||start_date::text||'|'||end_date::text"),
      ("sectors", "symbol||'|'||coalesce(sector,'')"),
      ("locked_symbols", "symbol||'|'||coalesce(hash,'')"),
      ("dataset_rows", "dataset_id::text||'|'||symbol||'|'||month||'|'||coalesce(adj_close::text,'')")]:
        cur.execute("select count(*), md5(string_agg(h,'|' order by h)) from (select %s h from %s) z" % (cols, t))
        n, m = cur.fetchone()
        FP[t] = {"rows": int(n), "md5": m}
    # 选源逐行一致性（本地重算 vs 落表）
    cur.execute("""
    with cand as (select symbol, source,
       max(case when basis='total_return' then 0 else 1 end) basis_rank,
       count(*) filter (where month between '2010-01' and '2025-12') cov,
       case source when 'alphavantage' then 1 when 'yahoo_relay' then 2 when 'eodhd' then 3
            when 'hf_amirtrader' then 3 when 'hf_ayushkhaire' then 4
            when 'sina_repaired+eodhd' then 4 else 5 end src_rank
       from prices_monthly group by symbol, source),
    pick as (select symbol, source from (select symbol, source,
       row_number() over (partition by symbol order by basis_rank, src_rank, cov desc) rn from cand) y where rn=1)
    select count(*) filter (where p.source is distinct from s.source) from series_selected s
    left join pick p on p.symbol=s.symbol""")
    FP["check_selection_mismatch"] = int(cur.fetchone()[0])
    cur.execute("""select count(*) from series_selected s where not exists
                   (select 1 from prices_monthly p where p.symbol=s.symbol and p.month=s.month
                    and p.source=s.source and p.adj_close=s.adj_close)""")
    FP["check_series_orphan"] = int(cur.fetchone()[0])
    # dataset_rows 与 series_selected 漂移
    cur.execute("""select d.dataset_id, count(*) from dataset_rows d where not exists
                   (select 1 from series_selected s where s.symbol=d.symbol and s.month=d.month
                    and s.adj_close=d.adj_close) group by 1 order by 1""")
    FP["check_dataset_drift"] = {int(a): int(b) for a, b in cur.fetchall()}
    cur.execute("select count(distinct symbol) from series_selected where month='2025-12'")
    FP["dec2025_symbols"] = int(cur.fetchone()[0])
    cur.execute("select count(*), count(*) filter (where basis='total_return') from series_selected")
    n, tr = cur.fetchone()
    FP["series_basis"] = {"rows": int(n), "total_return": int(tr)}
    cur.execute("select count(*), count(*) filter (where basis='total_return') from prices_monthly")
    n, tr = cur.fetchone()
    FP["prices_basis"] = {"rows": int(n), "total_return": int(tr)}
    cc.rollback(); cc.close()
    B["db"] = FP
except Exception as ex:
    B["db"] = {"error": str(ex)[:300]}

json.dump(B, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(B, ensure_ascii=False, indent=1)[:6000])
print("\n已写", OUT)
