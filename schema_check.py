# -*- coding: utf-8 -*-
"""schema_check.py — 数据库结构冒烟测试（防止重构后静默失效）

为什么需要
  重构期间发生过两次"看起来正常、实际已坏"：
    · anon 权限全开却以为已收紧（revoke 超时被回滚）
    · 重建后视图丢权限，CI 的 REST 读 401
  这类问题不会报错，只会让页面数字悄悄变旧。所以要有结构断言。

断言项
  A. 对象存在性：12 张表 + 关键视图
  B. 兼容视图与底层表行数一致
  C. anon 可读所有 CI 依赖对象（用 REST 实测）
     C2. 同一件事的库内断言（has_table_privilege）——REST 被配额挡下时由它把关
  D. 无 legacy 残留
  E. 关键不变量：锁定 430、series 行数、选源覆盖

用法
  python schema_check.py           # 全量
  python schema_check.py --quick   # 只查结构与权限，不查数据量
"""
import argparse
import io
import json
import os
import sys
import urllib.error
import urllib.request

import psycopg2

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
REF = "fnvahsyrzmmxwovbsqpc"
HOST = "aws-0-ap-northeast-1.pooler.supabase.com"

EXPECTED_TABLES = [
    "prices_monthly", "dataset_rows", "datasets", "members", "symbols", "series_pick",
    "data_lock", "sys_event", "data_source", "data_source_secret", "ingest_staging",
    "holdings", "corporate_actions",
]
EXPECTED_VIEWS = [
    "series_selected", "v_series", "sectors", "source_registry", "data_sources",
    "locks", "locked_symbols", "fetch_runs", "validations", "work_plans",
    "v_membership_open", "v_collect_state", "v_collect_summary",
]
REST_READABLE = ["series_selected", "v_series", "sectors", "source_registry",
                 "data_sources", "locked_symbols", "locks", "fetch_runs",
                 "validations", "data_source", "data_lock", "sys_event", "members"]

FAILS = []
WARNS = []


def ok(cond, name, detail=""):
    if cond:
        print("  ✅ %-40s %s" % (name, detail))
    else:
        print("  ❌ %-40s %s" % (name, detail))
        FAILS.append(name)


def warn(cond, name, detail=""):
    if cond:
        print("  ✅ %-40s %s" % (name, detail))
    else:
        print("  ⚠  %-40s %s" % (name, detail))
        WARNS.append(name)


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


def check_structure(cur):
    print("\n[A] 对象存在性")
    cur.execute("""select relname, relkind from pg_class c join pg_namespace n
                   on n.oid=c.relnamespace where n.nspname='public'""")
    rels = {r[0]: r[1] for r in cur.fetchall()}
    for t in EXPECTED_TABLES:
        ok(rels.get(t) == "r", "表 %s" % t, rels.get(t, "缺失"))
    for v in EXPECTED_VIEWS:
        ok(rels.get(v) == "v", "视图 %s" % v, rels.get(v, "缺失"))

    print("\n[D] 无 legacy 残留")
    legacy = [k for k in rels if "legacy" in k or k.startswith("zz_")]
    ok(not legacy, "无 legacy/zz_ 对象", str(legacy))


def check_consistency(cur):
    print("\n[B] 兼容视图与底层一致")
    pairs = [
        ("series_selected", "select count(*) from series_selected",
         "select count(*) from v_series"),
        ("sectors", "select count(*) from sectors",
         "select count(*) from symbols where sector is not null"),
        ("locked_symbols", "select count(*) from locked_symbols",
         """select count(*) from data_lock l where l.scope='symbol_month' and l.status='locked'
            and not exists (select 1 from data_lock x where x.supersedes_id=l.id and x.status='void')"""),
        ("fetch_runs", "select count(*) from fetch_runs",
         "select count(*) from sys_event where kind='fetch'"),
        ("validations", "select count(*) from validations",
         "select count(*) from sys_event where kind='validation'"),
    ]
    for name, qa, qb in pairs:
        cur.execute(qa); a = cur.fetchone()[0]
        cur.execute(qb); b = cur.fetchone()[0]
        ok(a == b, "视图 %s 行数" % name, "%d vs %d" % (a, b))


def check_grants(cur):
    """anon 权限的库内断言——与 REST 实测同口径，但不受出口配额影响。

    为什么要单独有这一条：REST 实测是这条不变量唯一的守门人，而网关一旦被
    出口配额挡下（HTTP 402），它对「权限丢了」和「账户欠费」给出同一个拒答，
    于是每晚必红、还把「汇总」和末尾的故障收口一起冻结——恒红等于没有门禁。
    权限本身在 pg_class/pg_auth 里就能问出来，直连查一次零出口流量。
    """
    print("\n[C2] anon 权限（库内断言）")
    cur.execute("select has_schema_privilege('anon', 'public', 'USAGE')")
    ok(cur.fetchone()[0] is True, "anon 有 public USAGE")
    arr = ",".join("'%s'" % o for o in REST_READABLE)
    cur.execute("""select r, has_table_privilege('anon', 'public.' || r, 'SELECT')
                   from unnest(ARRAY[%s]::text[]) r""" % arr)
    rows = cur.fetchall()
    lost = [r for r, can in rows if not can]
    ok(not lost, "anon 可选 %d 个 CI 依赖对象" % len(REST_READABLE),
       "丢权限：%s" % lost if lost else "%d/%d 有 GRANT" % (len(rows) - len(lost), len(rows)))
    cur.execute("""select coalesce(string_agg(p, ','), '') from (
                     select p, has_table_privilege('anon', 'public.prices_monthly', p) granted
                     from unnest(ARRAY['INSERT','UPDATE','DELETE','TRUNCATE']) p) t
                   where granted""")
    got = cur.fetchone()[0]
    ok(got == "", "anon 不可写 prices_monthly",
       "已授予：%s" % got if got else "INSERT/UPDATE/DELETE/TRUNCATE 全无")


def check_rest():
    print("\n[C] anon 可读（REST 实测）")
    cfg = json.load(io.open(os.path.join(BASE, "static", "cloud.json"), encoding="utf-8"))
    url, key = cfg["url"].rstrip("/"), cfg["key"]
    bad, restricted = [], []
    for obj in REST_READABLE:
        try:
            r = urllib.request.urlopen(urllib.request.Request(
                url + "/rest/v1/" + obj + "?select=*&limit=1",
                headers={"apikey": key, "Authorization": "Bearer " + key}), timeout=20)
            if r.status != 200:
                bad.append((obj, r.status))
        except urllib.error.HTTPError as e:
            (restricted if e.code == 402 else bad).append((obj, e.code))
        except Exception as e:
            bad.append((obj, str(e)[:30]))
    if restricted and not bad:
        # 网关一个对象都没答话时，"✅ 全部可读" 是假安慰（恒绿和恒红一样失真），
        # 而判失败又是拿账户欠费当结构缺陷。所以只点名，把权限结论交给 [C2]。
        warn(False, "REST 实测未答话（网关被出口配额拒答 402）",
             "%d/%d 个对象无法实测，anon 权限以 [C2] 库内断言为准；配额重置后自动恢复实测"
             % (len(restricted), len(REST_READABLE)))
    else:
        ok(not bad, "全部可读(%d 个)" % len(REST_READABLE), str(bad))

    # 写权限必须关闭
    if restricted:
        warn(False, "anon 写探针无法核验", "网关 402 时拒答来自配额而非授权，改看 [C2]")
        return
    try:
        req = urllib.request.Request(
            url + "/rest/v1/prices_monthly?symbol=eq.__probe__", method="PATCH",
            data=json.dumps({"adj_close": 1}).encode(),
            headers={"apikey": key, "Authorization": "Bearer " + key,
                     "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=20)
        ok(False, "anon 不能写 prices_monthly", "写入成功了！")
    except urllib.error.HTTPError as e:
        ok(e.code in (401, 403), "anon 不能写 prices_monthly", "HTTP %d" % e.code)
    except Exception as e:
        warn(False, "anon 写测试异常", str(e)[:60])



def check_invariants(cur):
    print("\n[E] 关键不变量")
    cur.execute("select count(*) from series_selected")
    n = cur.fetchone()[0]
    ok(n > 100000, "series 行数 > 10 万", "%d" % n)
    cur.execute("select count(*) from data_lock where scope='symbol_month' and status='locked'")
    lk = cur.fetchone()[0]
    ok(lk >= 400, "已锁标的 >= 400", "%d" % lk)
    cur.execute("select count(*) from series_pick")
    pk = cur.fetchone()[0]
    ok(pk >= 800, "选源决策 >= 800", "%d" % pk)
    cur.execute("""select count(*) from series_pick k
                   where not exists (select 1 from prices_monthly p
                                     where p.symbol=k.symbol and p.source=k.source)""")
    orphan = cur.fetchone()[0]
    ok(orphan == 0, "选源决策都能对上价格行", "%d 条孤儿" % orphan)
    cur.execute("""select count(*) from series_selected
                   where month between '2012-01' and '2025-12' and basis='total_return'""")
    tr = cur.fetchone()[0]
    cur.execute("""select count(*) from series_selected where month between '2012-01' and '2025-12'""")
    tot = cur.fetchone()[0]
    warn(tr / max(1, tot) >= 0.99, "窗口内全收益占比",
         "%.1f%%（目标 100%%）" % (100.0 * tr / max(1, tot)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    c = db()
    with c.cursor() as cur:
        check_structure(cur)
        check_consistency(cur)
        check_grants(cur)
        if not a.quick:
            check_invariants(cur)
    c.close()
    check_rest()

    print("\n结果：失败 %d 项 / 警告 %d 项" % (len(FAILS), len(WARNS)))
    if FAILS:
        print("失败项:", FAILS)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
