# -*- coding: utf-8 -*-
"""db_prices.py — 价格数据访问层（唯一数据真相 = Supabase/Postgres）

所有下游（variant_b / equity / 校验 / 导出）都通过这一层读数据，不再读本地 csv/json。

核心规则
  · 一个 (symbol, month) 可能多源并存 → 取「等级最高」的一条（source_priority）
  · basis：total_return 优先于 price_only；同 basis 内按 source_registry.priority
  · 读取带进程内缓存，减少往返
"""
import io
import os

import psycopg2
import psycopg2.extras

BASE = os.path.dirname(os.path.abspath(__file__))
REF = "fnvahsyrzmmxwovbsqpc"
HOST = "aws-0-ap-northeast-1.pooler.supabase.com"

# 源优先级（与 source_registry 保持一致；DB 不可达时的兜底）
PRIORITY = {"alphavantage": 1, "yahoo_relay": 2, "eodhd": 3,
            "hf_ayushkhaire": 4, "sina_repaired": 5, "sina_repaired+eodhd": 3}

_conn = None
CACHE_FILE = os.path.join(BASE, "output", "_prices_cache.json")
CACHE_TTL = 6 * 3600      # 派生缓存，6 小时内直接用
_cache = {}


def connect():
    global _conn
    if _conn is not None and not _conn.closed:
        return _conn
    pw = ""
    env = os.environ.get("SUPABASE_DB_PASSWORD")
    p = os.path.join(BASE, "output", "_supabase_dbpass.txt")
    if env:
        pw = env.strip()
    elif os.path.exists(p):
        pw = io.open(p, encoding="utf-8").read().strip()
    _conn = psycopg2.connect(host=HOST, port=6543, user="postgres." + REF, password=pw,
                             dbname="postgres", connect_timeout=20, sslmode="require")
    return _conn


def series(symbol, prefer_basis="total_return"):
    """返回 {YYYY-MM: (adj, raw)}；同月多源时取最优"""
    key = (symbol, prefer_basis)
    if key in _cache:
        return _cache[key]
    with connect() as c, c.cursor() as cur:
        cur.execute("""select month, adj_close, raw_close, source, basis
                       from prices_monthly where symbol=%s""", (symbol,))
        rows = cur.fetchall()
    best = {}
    for month, adj, raw, source, basis in rows:
        rank = PRIORITY.get(source, 99)
        score = (0 if basis == prefer_basis else 1, rank)
        cur_score = best.get(month, (9, 99))[0:2]
        if month not in best or score < (best[month][2], best[month][3]):
            best[month] = (adj, raw, score[0], score[1], source)
    out = {m: (float(v[0]), float(v[1]) if v[1] else float(v[0]))
           for m, v in best.items() if v[0]}
    _cache[key] = out
    return out


def symbols():
    with connect() as c, c.cursor() as cur:
        cur.execute("select distinct symbol from prices_monthly order by 1")
        return [r[0] for r in cur.fetchall()]


def coverage(symbol):
    with connect() as c, c.cursor() as cur:
        cur.execute("""select count(*) filter (where month like '%-12'),
                              count(*), min(month), max(month)
                       from prices_monthly where symbol=%s""", (symbol,))
        return cur.fetchone()


def write_rows(rows):
    """rows: [(symbol, month, adj, raw, source, quality, basis)]"""
    if not rows:
        return 0
    with connect() as c, c.cursor() as cur:
        psycopg2.extras.execute_values(
            cur, """insert into prices_monthly(symbol,month,adj_close,raw_close,source,quality,basis)
                    values %s
                    on conflict (symbol,month,source) do update
                    set adj_close=excluded.adj_close, raw_close=excluded.raw_close,
                        quality=excluded.quality, basis=excluded.basis, fetched_at=now()""",
            rows, page_size=5000)
    _cache.clear()
    return len(rows)


def dataset_id(name):
    with connect() as c, c.cursor() as cur:
        cur.execute("select id, input_hash, status from datasets where name=%s", (name,))
        r = cur.fetchone()
        return r


def log_validation(dataset, check_name, status, detail=""):
    with connect() as c, c.cursor() as cur:
        cur.execute("""insert into validations(dataset_id, check_name, status, detail)
                       values ((select id from datasets where name=%s), %s, %s, %s)""",
                    (dataset, check_name, status, detail))


def stats():
    with connect() as c, c.cursor() as cur:
        cur.execute("""select count(distinct symbol), count(*),
                              count(*) filter (where basis='total_return')
                       from prices_monthly""")
        return cur.fetchone()

def _db_stamp():
    """数据版本戳：行数 + 最大抓取时间 + 标的数（一条轻量查询）"""
    try:
        with connect() as c, c.cursor() as cur:
            cur.execute("""select count(*), coalesce(max(fetched_at)::text,''),
                                  count(distinct symbol) from prices_monthly""")
            return cur.fetchone()
    except Exception:
        return None


def _cache_fresh():
    """只认数据版本戳，不认 mtime（mtime 会被 git checkout 刷新）"""
    try:
        if not os.path.exists(CACHE_FILE):
            return False
        import json as _json
        head = _json.load(open(CACHE_FILE, encoding="utf-8"))
        stamp = head.get("__stamp__")
        if not stamp:
            return False
        return tuple(stamp) == tuple(_db_stamp() or ())
    except Exception:
        return False


def load_all(prefer_basis="total_return", chunk=60, use_cache=True):
    """对外唯一入口：委托 db_select 的「每票单源」结果（已修正逐月换源造假）"""
    import db_select as _ds
    if use_cache and prefer_basis == "total_return" and _cache_fresh():
        import json as _json
        raw = _json.load(open(CACHE_FILE, encoding="utf-8"))
        raw.pop("__stamp__", None)
        return {k: {m: (v[0], v[1]) for m, v in d.items()} for k, d in raw.items()}
    out = _ds.load_all()
    try:
        import json as _json
        payload = {k: {m: [v[0], v[1]] for m, v in d.items()} for k, d in out.items()}
        payload["__stamp__"] = list(_db_stamp() or [])
        _json.dump(payload, open(CACHE_FILE, "w"), separators=(",", ":"))
    except Exception as _e:
        print("写缓存失败：%s" % _e)
    return out


def _load_all_legacy(prefer_basis="total_return", chunk=60, use_cache=True):
    if use_cache and prefer_basis == "total_return" and _cache_fresh():
        import json as _json
        raw = _json.load(open(CACHE_FILE, encoding="utf-8"))
        raw.pop("__stamp__", None)
        return {k: {m: (v[0], v[1]) for m, v in d.items()} for k, d in raw.items()}
    """分片载入全库（避免单条查询超时），返回 {symbol: {month: (adj, raw)}}"""
    syms = symbols()
    best = {}
    conn = connect()
    for i in range(0, len(syms), chunk):
        part = syms[i:i + chunk]
        with conn.cursor() as cur:
            cur.execute("""select symbol, month, adj_close, raw_close, source, basis
                           from prices_monthly where symbol = any(%s)""", (part,))
            rows = cur.fetchall()
        for sym, month, adj, raw, source, basis in rows:
            rank = PRIORITY.get(source, 99)
            score = (0 if basis == prefer_basis else 1, rank)
            d = best.setdefault(sym, {})
            cur_s = d.get(month)
            if cur_s is None or score < (cur_s[2], cur_s[3]):
                d[month] = (adj, raw, score[0], score[1])
        print("  载入 %d/%d" % (min(i + chunk, len(syms)), len(syms)), end="\r", flush=True)
    out = {}
    for sym, months in best.items():
        out[sym] = {m: (float(v[0]), float(v[1]) if v[1] else float(v[0]))
                    for m, v in months.items() if v[0]}
    print()
    try:
        import json as _json
        payload = {k: {m: [v[0], v[1]] for m, v in d.items()} for k, d in out.items()}
        payload["__stamp__"] = list(_db_stamp() or [])
        _json.dump(payload, open(CACHE_FILE, "w"), separators=(",", ":"))
    except Exception as _e:
        print("写缓存失败：%s" % _e)
    return out
