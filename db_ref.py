# -*- coding: utf-8 -*-
"""db_ref.py — 成分股与行业分类入库（替代 static/*.csv / *.json）

用户要求：一切以在线库为准，禁止线下文件做数据依据。
  · members  成分区间（原 static/members_frozen.csv）
  · sectors  行业分类（原 static/sector_frozen.json）

读取策略：DB 直连优先，失败自动走 REST（同一份库数据，CI 无口令时可用）。
写入只在 bootstrap（表为空时）发生，文件不再是运行时依赖。
"""
import csv
import io
import json
import hashlib
import datetime as dt
import os
import sys

import psycopg2
import psycopg2.extras

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
REF = "fnvahsyrzmmxwovbsqpc"
HOST = "aws-0-ap-northeast-1.pooler.supabase.com"

DDL = [
    "create table if not exists members (symbol text not null, start_date date not null, "
    "end_date date not null, source text default 'fja05680', primary key (symbol, start_date))",
    "create table if not exists sectors (symbol text primary key, sector text, "
    "source text default 'datahub', updated_at timestamptz default now())",
    "alter table members enable row level security",
    "alter table sectors enable row level security",
]


def db():
    pw = os.environ.get("SUPABASE_DB_PASSWORD", "")
    if not pw:
        p = os.path.join(BASE, "output", "_supabase_dbpass.txt")
        if os.path.exists(p):
            pw = io.open(p, encoding="utf-8").read().strip()
    c = psycopg2.connect(host=HOST, port=6543, user="postgres." + REF, password=pw,
                         dbname="postgres", connect_timeout=6, sslmode="require")
    c.autocommit = True
    return c


def _rest(table, cols, order=None):
    import db_rest
    params = {"select": cols}
    if order:
        params["order"] = order
    return db_rest.get(table, params)


def bootstrap():
    c = db()
    with c.cursor() as cur:
        for st in DDL:
            try:
                cur.execute(st)
            except Exception as e:
                print("  ! %s: %s" % (st[:46], str(e)[:60]))
        for pol in ("members", "sectors"):
            try:
                cur.execute("create policy public_read on %s for select to anon, "
                            "authenticated using (true)" % pol)
            except Exception:
                pass
        cur.execute("select count(*) from members")
        n_mem = cur.fetchone()[0]
        cur.execute("select count(*) from sectors")
        n_sec = cur.fetchone()[0]

        if n_mem == 0:
            p = os.path.join(BASE, "static", "members_frozen.csv")
            rows = list(csv.DictReader(io.open(p, encoding="utf-8")))
            data = [(r["ticker"], r["start_date"] or "1970-01-01",
                     r["end_date"] or "2099-12-31") for r in rows]
            psycopg2.extras.execute_values(
                cur, "insert into members(symbol,start_date,end_date) values %s "
                     "on conflict do nothing", data, page_size=2000)
            print("members 已 bootstrap：%d 行" % len(data))

        if n_sec == 0:
            p = os.path.join(BASE, "static", "sector_frozen.json")
            d = json.load(io.open(p, encoding="utf-8"))
            psycopg2.extras.execute_values(
                cur, "insert into sectors(symbol,sector) values %s "
                     "on conflict (symbol) do update set sector=excluded.sector",
                list(d.items()), page_size=2000)
            print("sectors 已 bootstrap：%d 行" % len(d))

        cur.execute("select count(*), min(start_date), max(end_date) from members")
        print("members:", cur.fetchone())
        cur.execute("select count(*) from sectors")
        print("sectors:", cur.fetchone()[0])
    c.close()


def load_members():
    """{ticker: [(start, end), ...]}；DB → REST，两者都空则报错"""
    try:
        c = db()
        with c.cursor() as cur:
            cur.execute("select symbol, start_date, end_date from members "
                        "order by symbol, start_date")
            rows = cur.fetchall()
        c.close()
        out = {}
        for s, a, b in rows:
            out.setdefault(s, []).append((a.isoformat(), b.isoformat()))
        if out:
            return out
    except Exception as e:
        print("⚠ members 直连失败（%s），改走 REST" % str(e)[:50], flush=True)
    rows = _rest("members", "symbol,start_date,end_date", "symbol,start_date")
    out = {}
    for r in rows:
        out.setdefault(r["symbol"], []).append((r["start_date"], r["end_date"]))
    if not out:
        raise RuntimeError("members 从库读取失败（直连与 REST 均空）")
    return out


def load_sectors():
    try:
        c = db()
        with c.cursor() as cur:
            cur.execute("select symbol, sector from sectors")
            rows = cur.fetchall()
        c.close()
        if rows:
            return {s: v for s, v in rows}
    except Exception as e:
        print("⚠ sectors 直连失败（%s），改走 REST" % str(e)[:50], flush=True)
    rows = _rest("sectors", "symbol,sector")
    out = {r["symbol"]: r["sector"] for r in rows}
    if not out:
        raise RuntimeError("sectors 从库读取失败（直连与 REST 均空）")
    return out


def _resolution_digest(proof):
    """Canonical digest; must match research/security_identity_resolutions.py."""
    return hashlib.sha256(json.dumps(proof, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def _reviewed_resolutions(cursor, symbols):
    """Read append-only identity resolutions. Anything questionable is ignored.

    Fail-closed on purpose: if the table is absent (i.e. before the user
    approves the migration), unreadable, tampered with, or merely incomplete,
    the caller behaves exactly as it always did and keeps blocking.
    """
    try:
        cursor.execute(
            "select symbol,resolution,proof,evidence_sha256 from "
            "public.security_identity_resolution where symbol=any(%s)",
            (symbols,))
        rows = cursor.fetchall()
    except Exception:
        return {}
    reviewed = {}
    for row in rows:
        try:
            symbol, resolution, proof, sha = row
        except (TypeError, ValueError):
            continue
        if (resolution != "exclude_reviewed" or not isinstance(proof, dict)
                or proof.get("symbol") != symbol
                or proof.get("resolution") != "exclude_reviewed"
                or proof.get("full_history_accepted") is not False
                or not isinstance(sha, str)
                or sha != _resolution_digest(proof)):
            continue
        reviewed[symbol] = sha
    return reviewed


def require_unquarantined_candidates(symbols):
    """An unknown original security cannot silently leave a ranking pool.

    A reviewed resolution turns an unknown exclusion into an explicit,
    evidence-backed one. It never releases the quarantine and never changes
    prices: the resolution table is append-only and its proof digest is
    re-verified here on every read. Unresolved symbols still abort generation.
    """
    symbols = sorted(set(symbols))
    if not symbols:
        return []
    connection = None
    try:
        connection = db()
        with connection.cursor() as cursor:
            cursor.execute('select symbol,reason from series_quarantine where symbol=any(%s) order by symbol',
                           (symbols,))
            unresolved = cursor.fetchall()
            reviewed = (_reviewed_resolutions(
                cursor, [symbol for symbol, reason in unresolved])
                if unresolved else {})
    except Exception as exc:
        raise RuntimeError('在线证券身份隔离检查失败，禁止生成新策略结果') from exc
    finally:
        if connection is not None:
            connection.close()
    if unresolved:
        blocked = [symbol for symbol, reason in unresolved if symbol not in reviewed]
        if blocked:
            raise RuntimeError('排名候选证券身份仍待核验，禁止静默剔除后发布：'+
                               ', '.join(blocked))
        # Explicit, greppable audit trail in CI logs. The durable record is
        # the append-only resolution row itself, not a runtime write (the
        # primary database is egress-limited and must not be written here).
        print("IDENTITY_RESOLUTION_APPLIED " + json.dumps({
            "reviewed": sorted(reviewed),
            "count": len(reviewed),
            "note": "隔离维持不变，仅显式排除；价格序列未解锁",
        }, ensure_ascii=False), flush=True)
        return sorted(reviewed)
    return []


def load_market_reference(as_of):
    """One online point-in-time pool with reviewed sector overlays, no file fallback.

    The legacy sector table stays unchanged. A new member's current sector
    must not be backfilled into its earlier historical membership intervals.
    """
    day=dt.date.fromisoformat(as_of)
    connection=db()
    try:
        connection.autocommit=False
        connection.set_session(isolation_level='REPEATABLE READ',readonly=True)
        with connection.cursor() as cursor:
            cursor.execute("set local statement_timeout='20s'")
            cursor.execute('select symbol,start_date,end_date,source from members order by symbol,start_date')
            all_members=cursor.fetchall()
            members=[r for r in all_members if r[1]<=day<=r[2]]
            if not members:
                raise RuntimeError('在线预览成分场次无效')
            live={r[0] for r in members}
            cursor.execute('select symbol,sector from sectors where symbol=any(%s) order by symbol',(sorted(live),))
            sectors=dict(cursor.fetchall())
            cursor.execute("select id,metrics from sys_event where kind='validation' and source='reviewed_index_change' and status='pass' order by id")
            rows=cursor.fetchall()
        reviews=[]
        membership_sha=hashlib.sha256(json.dumps(all_members,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
        for identity,metrics in rows:
            proof=metrics.get('proof')
            sha=hashlib.sha256(json.dumps(proof,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
            if metrics.get('evidence_sha256')!=sha or not isinstance(proof,dict) or proof.get('index')!='S&P 500':
                raise RuntimeError('在线指数变更证明无效')
            change=proof['change']
            if change['action']=='add' and change['symbol'] in live and change['sector_from']<=as_of:
                if not change['sector']:raise RuntimeError('在线行业变更缺少分类')
                reviews.append((change['sector_from'],identity,change,sha))
        if rows and rows[-1][1]['proof'].get('membership_after_sha256')!=membership_sha:
            raise RuntimeError('在线成分与最近核验版本不一致，禁止使用未审查的更新')
        for _,_,change,sha in sorted(reviews,key=lambda r:(r[0],r[1])):
            sectors[change['symbol']]=change['sector']
        version=hashlib.sha256(json.dumps({'as_of':as_of,'members':members,'sectors':sectors,
            'review_sha256':[r[3] for r in reviews]},sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
        return live,sectors,version
    finally:
        connection.close()


if __name__ == "__main__":
    bootstrap()
