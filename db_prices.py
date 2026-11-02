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
import tempfile

import psycopg2
import psycopg2.extras

BASE = os.path.dirname(os.path.abspath(__file__))
REF = "fnvahsyrzmmxwovbsqpc"
HOST = "aws-0-ap-northeast-1.pooler.supabase.com"

# 源优先级（与 source_registry 保持一致；DB 不可达时的兜底）
PRIORITY = {"alphavantage": 1, "yahoo_relay": 2, "eodhd": 3,
            "hf_ayushkhaire": 4, "sina_repaired": 5, "sina_repaired+eodhd": 3}

_conn = None
_DB_DEAD = None      # True = 本进程已确认连不上
_MARKER = os.path.join(BASE, "output", ".db_unavailable")
CACHE_FILE = os.path.join(BASE, "output", "_prices_cache.json")
CACHE_TTL = 6 * 3600      # 派生缓存，6 小时内直接用
_cache = {}


def connect():
    global _conn, _DB_DEAD
    if _DB_DEAD:
        raise RuntimeError("数据库不可用（本进程已判定）")
    if _conn is not None and not _conn.closed:
        return _conn
    pw = ""
    env = os.environ.get("SUPABASE_DB_PASSWORD")
    p = os.path.join(BASE, "output", "_supabase_dbpass.txt")
    if env:
        pw = env.strip()
    elif os.path.exists(p):
        pw = io.open(p, encoding="utf-8").read().strip()
    try:
        _conn = psycopg2.connect(host=HOST, port=6543, user="postgres." + REF, password=pw,
                                 dbname="postgres", connect_timeout=6, sslmode="require")
    except Exception:
        _DB_DEAD = True
        try:
            open(_MARKER, "w").write("1")
        except Exception:
            pass
        raise
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
        cur.execute("""insert into sys_event(kind, source, subject, status, detail,
                                            dataset_id, finished_at)
                       values ('validation','db_prices',%s,%s,%s,
                               (select id from datasets where name=%s), now())""",
                    (check_name, status, detail, dataset))


def stats():
    with connect() as c, c.cursor() as cur:
        cur.execute("""select count(distinct symbol), count(*),
                              count(*) filter (where basis='total_return')
                       from prices_monthly""")
        return cur.fetchone()

def _db_stamp():
    """数据版本戳：行数 + 最大抓取时间 + 标的数（一条轻量查询）"""
    if _DB_DEAD and os.path.exists(_MARKER):
        return None
    try:
        with connect() as c, c.cursor() as cur:
            cur.execute("""select count(*), coalesce(max(fetched_at)::text,''),
                                  count(distinct symbol) from prices_monthly""")
            a = cur.fetchone()
            # 必须把 series_selected 也算进指纹：重建选源不改变 prices_monthly，
            # 只比 prices_monthly 会让旧缓存被误判为新鲜（实测导致本地 31.07x vs CI 30.07x）
            cur.execute("""select count(*), count(distinct symbol),
                                  coalesce(sum(adj_close),0)::text
                           from series_selected""")
            b = cur.fetchone()
            return (a[0], a[1], a[2], b[0], b[1], b[2])
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
        live = _db_stamp()
        if live is None:
            # An offline cache cannot prove that a source was not quarantined
            # since it was built. Read the guarded REST view instead.
            return False
        return tuple(stamp) == tuple(live or ())
    except Exception:
        return False


def load_all(prefer_basis="total_return", chunk=60, use_cache=False):
    """始终在线读取选中序列；兼容旧参数，本地快照仅用于诊断。"""
    import db_select as _ds
    before = _db_stamp()
    try:
        out = _ds.load_all()
    except Exception as _e:
        print("⚠ 直连数据库失败（%s），改走 REST 只读" % str(_e)[:60], flush=True)
        import db_rest as _rest
        out = _rest.load_selected()
        if not out:
            raise RuntimeError("REST 返回 0 行（凭据/RLS 异常），禁止退回文件数据")
    after = _db_stamp()
    if before is not None and after is not None and tuple(before[-3:]) != tuple(after[-3:]):
        raise RuntimeError('选中价格在读取期间变化，禁止把旧数据缓存为新版本；请重新读取')
    try:
        import json as _json
        payload = {k: {m: [v[0], v[1]] for m, v in d.items()} for k, d in out.items()}
        _live = after if before is not None else None
        # DB 不可达时必须给一个真值标记，否则空列表会被当作「无指纹」而永不命中缓存
        payload["__stamp__"] = list(_live) if _live else ["rest", len(out),
                                                          sum(len(d) for d in out.values())]
        payload["__built_at__"] = __import__("time").time()
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                    dir=os.path.dirname(CACHE_FILE), prefix='_prices.', suffix='.tmp',
                    delete=False) as file:
                temporary = file.name
                _json.dump(payload, file, separators=(",", ":"))
            os.replace(temporary, CACHE_FILE)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
    except Exception as _e:
        print("写缓存失败：%s" % _e)
    return out


def _load_all_legacy(prefer_basis="total_return", chunk=60, use_cache=False):
    """Compatibility entry point with the same online-only requirement."""
    return load_all(prefer_basis=prefer_basis, chunk=chunk, use_cache=False)


def log_run(source, requested, ok, failed=0, duration_s=0.0, note=""):
    """统一采集台账：每次运行都写一行，含耗时与失败数"""
    try:
        with connect() as c, c.cursor() as cur:
            cur.execute("""insert into sys_event(kind, source, subject, status, detail,
                                                metrics, started_at, finished_at)
                           values ('fetch', %s, 'run',
                                   case when %s > 0 then 'warn' else 'ok' end,
                                   %s,
                                   jsonb_build_object('requested', %s, 'ok', %s, 'failed', %s,
                                                      'duration_ms', %s),
                                   now() - make_interval(secs => %s), now())""",
                        (source, failed, (note or "")[:300], requested, ok, failed,
                         int(float(duration_s)*1000), float(duration_s)))
    except Exception as e:
        print("台账写入失败：%s" % str(e)[:80])
