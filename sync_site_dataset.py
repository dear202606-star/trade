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
    """主库不可达时，从 Neon 原始表还原同一组展示 key。

    Neon 冷备只导入实体表（不含 PostgREST 视图），因此这里读的是
    site_dataset / live_quote_snapshot / live_strategy_current /
    live_strategy_anchor，与 export_site_snapshot.collect() 的视图语义等价。

    2026-11-10 起可由 --read-source auto 自动降级调用，但必须过
    resolve_datasets() 的**防回退守卫**：Neon 是日更冷备，滞后于 Turso，
    绝不允许用更旧的行情反向覆盖访客正在读的备库。显式 --from-neon
    仍按运维意图执行（人工已确认要回放）。
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


def turso_quotes_ts(url, token):
    """读取 Turso 当前 quotes 的业务时间戳，用于降级防回退守卫。"""
    rows = turso_execute(url, token, [
        ("select payload from site_dataset where key='quotes'", [])])
    if not rows or not rows[0] or not rows[0][0]:
        return None
    payload = rows[0][0][0]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return str((payload or {}).get("ts") or "") or None


def _quote_ts(datasets):
    return str((datasets.get("quotes") or {}).get("ts") or "") or None


def resolve_datasets(args, t_url, t_token):
    """读源自动切换：主库 → Neon 持久副本（带防回退守卫）。

    返回 (datasets, meta, source, degraded, reason)。
    绝不做「主库写」的自动切换：两库同时接受同一批次写入会产生无法
    合并的分叉，提升/回切必须人工单向回放（见 db_failover.py）。
    """
    if args.from_archive:
        d, m = collect_from_archive(args.from_archive)
        return d, m, "archive", True, "显式指定整库归档"
    if args.from_neon:
        d, m = collect_from_neon(neon_dsn())
        return d, m, "neon", True, "显式指定 Neon"
    if args.read_source == "neon":
        d, m = collect_from_neon(neon_dsn())
        return d, m, "neon", True, "显式指定读源 neon"
    if args.read_source == "supabase":
        d, m = ess.collect()
        return d, m, "supabase", False, ""

    # auto（默认）：主库优先；不可读才降级，且必须证明不会让数据倒退。
    try:
        d, m = ess.collect()
        return d, m, "supabase", False, ""
    except Exception as exc:
        reason = "主库不可读，自动降级 Neon：%s" % str(exc)[:160]
    d, m = collect_from_neon(neon_dsn())
    current = turso_quotes_ts(t_url, t_token)   # 读不到就抛错，fail-closed
    candidate = _quote_ts(d)
    if current and candidate and candidate <= current:
        raise RuntimeError(
            "拒绝用滞后的 Neon 副本覆盖 Turso（Turso=%s / Neon=%s）；"
            "保留现有数据并告警，不做回退写入" % (current, candidate))
    if current and not candidate:
        raise RuntimeError("Neon 副本缺少可核验的行情时间戳，拒绝自动降级")
    return d, m, "neon", True, reason


def build_rows(datasets, meta):
    """返回 [(key, payload_text, updated_at, sha256, payload_obj), ...]。"""
    rows = []
    for key in ess.ALL_KEYS:
        obj = datasets[key]
        text = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        rows.append((key, text, str(meta.get(key, {}).get("updated_at") or _utcnow()), sha, obj))
    return rows


def settled_outputs_match(datasets):
    q = datasets["quotes"]
    session, version = q.get("session"), q.get("version")
    if not isinstance(session, str) or not session or not version or q.get("intraday") is not False:
        return False
    for key in ("equity", "b/equity", "preview", "b/preview"):
        result = datasets[key]
        result_session = (result.get("last_session") or "") if key.endswith("equity") else result.get("session")
        if (str(result_session).replace("-", "") != session.replace("-", "") or
                result.get("quote_version") != version):
            return False
    return True


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
        values = ",".join(["(?,?,?,?)"] * len(todo))
        sql = ("insert into site_dataset(key,payload,updated_at,payload_sha256) values " +
               values + " on conflict(key) do update set "
               "payload=excluded.payload, updated_at=excluded.updated_at, "
               "payload_sha256=excluded.payload_sha256")
        turso_execute(url, token, [(sql, [field for row in todo for field in row[:4]])])
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
    ap.add_argument("--read-source", default="auto",
                    choices=("auto", "supabase", "neon"),
                    help="展示数据读源：auto=主库优先、不可读自动降级 Neon（防回退）")
    ap.add_argument("--dry-run", action="store_true", help="只校验并打印计划")
    ap.add_argument("--force", action="store_true", help="忽略指纹，强制全量重写")
    args = ap.parse_args()

    # 凭据只在真正需要（写备库 / 降级防回退）时才是硬性依赖；主库健康的
    # --dry-run 不应因为本机没放 Turso token 而失败。
    try:
        t_url, t_token = turso_credentials()
    except Exception as exc:
        t_url, t_token = None, None
        print("WARN: Turso 凭据不可用，降级守卫将 fail-closed：%s"
              % str(exc)[:120], file=sys.stderr)
    datasets, meta, read_source, degraded, fallback_reason = resolve_datasets(
        args, t_url, t_token)
    problems = ess.validate(datasets)
    if problems:
        print(json.dumps({"ok": False, "stage": "validate", "problems": problems},
                         ensure_ascii=False))
        return 1
    rows = build_rows(datasets, meta)
    if not settled_outputs_match(datasets):
        runtime = set(ess.RUNTIME_KEYS)
        rows = [row for row in rows if row[0] not in runtime]
        print("收盘行情、净值和排名尚未对齐，备库保留上一份完整快照。")
    report = {"ok": True, "generated_at": _utcnow(), "keys": len(rows),
              "digest": hashlib.sha256("".join(r[3] for r in rows).encode()).hexdigest(),
              "read_source": read_source, "degraded": degraded,
              "fallback_reason": fallback_reason, "targets": {}}
    if args.dry_run:
        report["dry_run"] = True
        report["plan"] = [{"key": r[0], "sha256": r[3][:12], "bytes": len(r[1].encode("utf-8"))}
                          for r in rows]
        print(json.dumps(report, ensure_ascii=False))
        return 0

    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    warnings = [fallback_reason] if fallback_reason else []
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
    # 降级读源时不写主库 sys_event 回执：主库此刻可能不可达，且不能伪造
    # 「主库健康」的证据；看门狗会从 warnings / read_source 看到真实状态。
    offline = degraded
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
