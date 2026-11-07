# -*- coding: utf-8 -*-
"""sync_site_dataset.py — 展示数据集「一主两备」增量同步（HANDOFF 第 7 节 A/B 层）。

设计（2026-11-08）
  主库 Supabase PG 仍然是唯一写入口。本脚本只做单向复制，绝不反向写主库：

    Supabase PG ──collect()/validate()──> 14 个展示 key
        ├──> Turso site_dataset        （A 层：libsql HTTP，浏览器可直读，只读 token）
        └──> Neon site_dataset_mirror  （B 层：持久副本 / 灾难恢复，全 24 表另由
                                         db_backup.py + restore_to_neon.py 负责）

  增量判据是 payload 的规范 JSON sha256：每次只读取备库的 (key, sha256) 指纹列，
  只 upsert 变化行，同步流量与数据集大小无关（典型 0~3 行、几十 KB）。
  Turso 写 token 只存在于 GitHub Secrets / 本地 output/*.local.json，
  cloud.json 里只放只读 token；site_build.py 会强制校验 a=="ro"。

用法
  python sync_site_dataset.py --dry-run                 # 只校验并打印同步计划
  python sync_site_dataset.py                          # 同步 Turso + Neon
  python sync_site_dataset.py --targets turso --force  # 强制全量重写 Turso
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import export_site_snapshot as ess  # noqa: E402  (collect / validate / ALL_KEYS)

LOCAL_TURSO = BASE / "output" / "turso_site.local.json"
LOCAL_NEON = BASE / "output" / "neon_cold_backup.local.json"

DDL_TURSO = """
create table if not exists site_dataset (
  key text primary key,
  payload text not null,
  updated_at text not null,
  payload_sha256 text not null default ''
)
"""
DDL_NEON = """
create table if not exists public.site_dataset_mirror (
  key text primary key,
  payload jsonb not null,
  payload_sha256 text not null,
  updated_at timestamptz not null default now()
)
"""


def _utcnow():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _local(path):
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def turso_credentials():
    cfg = _local(LOCAL_TURSO)
    url = (os.environ.get("TURSO_HTTP_URL") or "").strip() or str(cfg.get("http_url") or "")
    token = (os.environ.get("TURSO_AUTH_TOKEN") or "").strip() or str(cfg.get("write_token") or "")
    return url.rstrip("/"), token


def neon_dsn():
    cfg = _local(LOCAL_NEON)
    dsn = (os.environ.get("NEON_DSN") or "").strip() or str(cfg.get("connection_string") or "")
    # libpq 的 psycopg2 不识别 channel_binding 连接参数，沿用 restore_to_neon.py 的处理
    return dsn.replace("&channel_binding=require", "").replace("channel_binding=require&", "")


def _text_args(values):
    return [{"type": "text", "value": str(v)} for v in values]


def turso_execute(url, token, statements):
    """statements: [(sql, [values...]), ...] → 每条语句的 rows（单元格取 value）。

    hrana v2 响应结构为 results[i].response.result，不是 results[i].result。
    """
    if not url or not token:
        raise RuntimeError("缺少 TURSO_HTTP_URL / TURSO_AUTH_TOKEN")
    requests = [{"type": "execute", "stmt": {"sql": sql, "args": _text_args(args)}}
                for sql, args in statements]
    requests.append({"type": "close"})
    body = {"baton": None, "requests": requests}
    req = urllib.request.Request(
        url + "/v2/pipeline", data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError("Turso HTTP %s: %s" % (exc.code, exc.read().decode("utf-8")[:200]))
    results = raw.get("results") or []
    if len(results) < len(statements):
        raise RuntimeError("Turso 响应结果数不足：%d < %d" % (len(results), len(statements)))
    out = []
    for item in results[:len(statements)]:
        if item.get("type") != "ok":
            raise RuntimeError("Turso 执行失败：%s" %
                               ((item.get("error") or {}).get("message") or "unknown"))
        res = ((item.get("response") or {}).get("result") or {})
        out.append([[cell.get("value") for cell in row] for row in res.get("rows", [])])
    return out


def collect_from_archive(archive):
    """冷启动路径：主库不可用时，从整库逻辑备份还原同一组 14 个展示 key。

    归档里的实体表与 export_site_snapshot.collect() 读取的视图一一对应：
      site_dataset / live_quote_snapshot / live_strategy_current /
      live_strategy_anchor。仅用于首次灌备库或灾难恢复，常态仍以主库为准。
    """
    import gzip
    import tarfile

    def rows(tf, name):
        member = tf.extractfile("tables/" + name + ".jsonl.gz")
        if member is None:
            raise RuntimeError("归档缺少表 %s" % name)
        text = gzip.decompress(member.read()).decode("utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    datasets, meta = {}, {}
    with tarfile.open(archive, "r:gz") as tf:
        for row in rows(tf, "site_dataset"):
            key = row["key"]
            if key in ess.STATIC_KEYS:
                datasets[key] = row["payload"]
                meta[key] = {"updated_at": str(row.get("updated_at") or ""),
                             "origin": "archive/site_dataset"}
        q = rows(tf, "live_quote_snapshot")
        if len(q) != 1:
            raise RuntimeError("归档行情快照必须且只能有 1 行，实际 %d" % len(q))
        datasets["quotes"] = q[0]["payload"]
        meta["quotes"] = {"updated_at": str(q[0].get("source_as_of") or
                                            q[0].get("collected_at") or ""),
                          "origin": "archive/live_quote_snapshot"}
        for row in rows(tf, "live_strategy_current"):
            key = ("b/" if row.get("variant") == "B" else "") + str(row.get("kind"))
            if key in ("equity", "b/equity", "preview", "b/preview"):
                datasets[key] = row["payload"]
                meta[key] = {"updated_at": str(row.get("source_as_of") or
                                               row.get("created_at") or ""),
                             "origin": "archive/live_strategy_current"}
        best = {}
        for row in rows(tf, "live_strategy_anchor"):
            variant = row.get("variant")
            order = (row.get("hold_year") or 0, row.get("revision") or 0)
            if variant not in best or order > best[variant][0]:
                best[variant] = (order, row)
        for variant, (_order, row) in best.items():
            key = ("b/" if variant == "B" else "") + "signal"
            datasets[key] = (row.get("payload") or {}).get("signal")
            meta[key] = {"updated_at": str(row.get("created_at") or ""),
                         "origin": "archive/live_strategy_anchor"}
    return datasets, meta


def collect_from_neon(dsn):
    """主库不可达且 Neon 已被提升为写主库时，从 Neon 原始表还原同一组展示 key。

    Neon 冷备只导入实体表（不含 PostgREST 视图），因此这里读的是
    site_dataset / live_quote_snapshot / live_strategy_current /
    live_strategy_anchor，与 export_site_snapshot.collect() 的视图语义等价。
    仅供运维显式启用（--from-neon），默认绝不自动回退，避免用旧冷备
    数据反向覆盖较新的 Turso 备库。
    """
    if not dsn:
        raise RuntimeError("缺少 NEON_DSN")
    import psycopg2
    conn = psycopg2.connect(dsn, connect_timeout=30)
    datasets, meta = {}, {}
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("set statement_timeout = '60s'")
            cur.execute("select key, payload, updated_at from public.site_dataset")
            for key, payload, ts in cur.fetchall():
                if key in ess.STATIC_KEYS:
                    datasets[key] = payload
                    meta[key] = {"updated_at": str(ts or ""), "origin": "neon/site_dataset"}
            cur.execute("select payload, source_as_of, collected_at "
                        "from public.live_quote_snapshot limit 1")
            q = cur.fetchall()
            if len(q) != 1:
                raise RuntimeError("Neon 行情快照必须且只能有 1 行，实际 %d" % len(q))
            datasets["quotes"] = q[0][0]
            meta["quotes"] = {"updated_at": str(q[0][1] or q[0][2] or ""),
                              "origin": "neon/live_quote_snapshot"}
            cur.execute("select variant, kind, payload, source_as_of, created_at "
                        "from public.live_strategy_current")
            for variant, kind, payload, source_as_of, created_at in cur.fetchall():
                key = ("b/" if variant == "B" else "") + str(kind)
                if key in ("equity", "b/equity", "preview", "b/preview"):
                    datasets[key] = payload
                    meta[key] = {"updated_at": str(source_as_of or created_at or ""),
                                 "origin": "neon/live_strategy_current"}
            cur.execute("select distinct on (variant) variant, hold_year, revision, "
                        "payload, created_at from public.live_strategy_anchor "
                        "order by variant, hold_year desc, revision desc")
            for variant, _hy, _rev, payload, created_at in cur.fetchall():
                key = ("b/" if variant == "B" else "") + "signal"
                datasets[key] = (payload or {}).get("signal")
                meta[key] = {"updated_at": str(created_at or ""),
                             "origin": "neon/live_strategy_anchor"}
    finally:
        conn.close()
    return datasets, meta


def build_rows(datasets, meta):
    """返回 [(key, payload_text, updated_at, sha256, payload_obj), ...]。"""
    rows = []
    for key in ess.ALL_KEYS:
        obj = datasets[key]
        text = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        rows.append((key, text, str(meta.get(key, {}).get("updated_at") or _utcnow()), sha, obj))
    return rows


def sync_turso(url, token, rows, force=False):
    turso_execute(url, token, [(DDL_TURSO, [])])
    try:
        current = dict(turso_execute(url, token, [
            ("select key, payload_sha256 from site_dataset", [])])[0])
    except RuntimeError:
        # 旧表只有 3 列：幂等补列，之后指纹列即可用于增量判定
        turso_execute(url, token, [
            ("alter table site_dataset add column payload_sha256 text not null default ''", [])])
        current = dict(turso_execute(url, token, [
            ("select key, payload_sha256 from site_dataset", [])])[0])
    todo = rows if force else [r for r in rows if current.get(r[0]) != r[3]]
    if todo:
        sql = ("insert into site_dataset(key,payload,updated_at,payload_sha256) "
               "values(?,?,?,?) on conflict(key) do update set "
               "payload=excluded.payload, updated_at=excluded.updated_at, "
               "payload_sha256=excluded.payload_sha256")
        turso_execute(url, token, [(sql, [r[0], r[1], r[2], r[3]]) for r in todo])
    return {"total": len(rows), "changed": len(todo), "keys": [r[0] for r in todo]}


def sync_neon(dsn, rows, force=False):
    if not dsn:
        raise RuntimeError("缺少 NEON_DSN")
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(dsn, connect_timeout=30)
    try:
        with conn.cursor() as cur:
            cur.execute(DDL_NEON)
            cur.execute("select key, payload_sha256 from public.site_dataset_mirror")
            current = dict(cur.fetchall())
        todo = rows if force else [r for r in rows if current.get(r[0]) != r[3]]
        for key, _text, _ts, sha, obj in todo:
            with conn.cursor() as cur:
                cur.execute(
                    "insert into public.site_dataset_mirror(key,payload,payload_sha256,updated_at) "
                    "values(%s,%s,%s,now()) on conflict(key) do update set "
                    "payload=excluded.payload, payload_sha256=excluded.payload_sha256, "
                    "updated_at=now() where public.site_dataset_mirror.payload_sha256 "
                    "<> excluded.payload_sha256",
                    (key, psycopg2.extras.Json(obj), sha))
        conn.commit()
        return {"total": len(rows), "changed": len(todo), "keys": [r[0] for r in todo]}
    finally:
        conn.close()


def write_event(status, metrics):
    """同步回执写主库 sys_event；失败只告警，不影响备库已完成的写入。"""
    try:
        import db_sources
        import psycopg2.extras
        conn = db_sources.db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "insert into public.sys_event(kind,source,subject,status,detail,metrics,finished_at) "
                    "values('validation','sync_site_dataset','replication',%s,%s,%s,now())",
                    (status, "Turso(A) + Neon(B) incremental replication",
                     psycopg2.extras.Json(metrics)))
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as exc:
        print("WARN: sys_event 回执写入失败：%s" % str(exc)[:160], file=sys.stderr)
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--targets", default="turso,neon", help="逗号分隔：turso,neon")
    ap.add_argument("--from-archive", default="", help="冷启动：从整库备份归档构造展示数据")
    ap.add_argument("--from-neon", action="store_true",
                    help="主库长期不可用且已提升 Neon 为写主库时，从 Neon 原始表取数")
    ap.add_argument("--dry-run", action="store_true", help="只校验并打印计划")
    ap.add_argument("--force", action="store_true", help="忽略指纹，强制全量重写")
    args = ap.parse_args()

    if args.from_archive:
        datasets, meta = collect_from_archive(args.from_archive)
    elif args.from_neon:
        datasets, meta = collect_from_neon(neon_dsn())
    else:
        datasets, meta = ess.collect()
    problems = ess.validate(datasets)
    if problems:
        print(json.dumps({"ok": False, "stage": "validate", "problems": problems},
                         ensure_ascii=False))
        return 1
    rows = build_rows(datasets, meta)
    report = {"ok": True, "generated_at": _utcnow(), "keys": len(rows),
              "digest": hashlib.sha256("".join(r[3] for r in rows).encode()).hexdigest(),
              "targets": {}}
    if args.dry_run:
        report["dry_run"] = True
        report["plan"] = [{"key": r[0], "sha256": r[3][:12], "bytes": len(r[1].encode("utf-8"))}
                          for r in rows]
        print(json.dumps(report, ensure_ascii=False))
        return 0

    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    warnings = []
    t_url, t_token = turso_credentials()
    if "turso" in targets:
        try:
            report["targets"]["turso"] = sync_turso(t_url, t_token, rows, args.force)
        except Exception as exc:
            report["targets"]["turso"] = {"error": str(exc)[:200]}
            warnings.append("turso: %s" % str(exc)[:120])
    if "neon" in targets:
        try:
            report["targets"]["neon"] = sync_neon(neon_dsn(), rows, args.force)
        except Exception as exc:
            report["targets"]["neon"] = {"error": str(exc)[:200]}
            warnings.append("neon: %s" % str(exc)[:120])

    report["warnings"] = warnings
    status = "ok" if not warnings else "warn"
    offline = bool(args.from_archive or args.from_neon)
    report["event_written"] = False if offline else write_event(status, {
        "keys": len(rows), "digest": report["digest"], "targets": report["targets"],
        "warnings": warnings, "dry_run": False})
    print(json.dumps(report, ensure_ascii=False))
    # A 层（浏览器可读备库）失败属于阻断级；B 层只降级为告警
    if "turso" in targets and "error" in report["targets"].get("turso", {}):
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
