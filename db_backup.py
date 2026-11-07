"""db_backup.py — 整库逻辑备份（直连，不依赖 pg_dump 二进制）。

2026-11-07：仓库此前没有任何可执行的整库备份，只有文档里的 pg_dump
建议命令与 8 个已脱敏的一次性表级 JSON 快照；HANDOVER 明确记录
「data_lock 962 条、series_pick 847 条、3 个 pg_cron 任务不在任何文件里，
换 Supabase 项目就全丢」。本脚本把整库状态变成可定期、可校验、
可回本地的产物。

安全边界
  · 凭据表 public.data_source_secret 永不进入备份（含数据、定义、内容）；
  · manifest 不含连接串、口令或任何凭据字段；
  · 只读连接，全程不写库。

产物（单个 .tar.gz）
  manifest.json          备份时间、表清单、行数、sha256、排除清单、校验和
  tables/<table>.jsonl   每张表一行一个 JSON 对象，gzip 压缩
  schema.sql             视图 / 函数定义（可重建数据库对象）
  ddl/*.sql              sql/ 目录全部建表 DDL（表结构 / 约束 / 触发器 / RLS）
  runtime.json           序列当前值、pg_cron 任务、RLS 开关、触发器清单
  README.txt             恢复方法与边界说明

用法
  python db_backup.py --output output/backups/trade-YYYYMMDD.tar.gz
  python db_backup.py --verify <file.tar.gz>       # 只读校验，不写库
"""
from __future__ import annotations

import argparse
import datetime as dt
import decimal
import gzip
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path

# 永不进入备份：凭据明文。其余业务表全量导出。
EXCLUDE_TABLES = {"data_source_secret"}

README = """trade 数据库整库逻辑备份
==========================
生成：{generated_at}
来源：PostgreSQL 直连（只读），不含任何凭据表。
排除：{excluded}

恢复边界
  · 本备份是「全量业务数据 + 全部建表 DDL + 视图/函数 + 运行时状态」的
    逻辑快照，不是物理转储；Supabase 平台级对象（Auth 用户、Storage 文件、
    API keys、角色口令、项目配置）不属于 public schema，SQL 备份无法覆盖。
  · 恢复顺序：① 空库按编号执行 ddl/*.sql 建立表结构与守卫；
    ② 按 manifest 表清单逐表导入 tables/*.jsonl；③ 执行 schema.sql 重建
    视图与函数；④ 按 runtime.json 恢复序列值(setval)与 pg_cron 任务，
    并核对 RLS 开关、触发器清单；⑤ 凭据由项目所有者重新注入。
  · 库内 append-only 守卫（data_lock / sys_event / live_*）会拒绝
    直接 UPDATE/DELETE；恢复演练应在独立空库进行，不要对生产库回放。
  · 凭据需由项目所有者重新注入，备份中刻意不含。

校验
  python db_backup.py --verify <file.tar.gz>

回本地（CI 每日 UTC 04:00 自动产出，artifact 保留 90 天）
  gh run download <run_id> --name trade-db-backup --dir ./backups
  # 或直接本地生成（需 SUPABASE_DB_PASSWORD）
  python db_backup.py --output backups/trade-$(date +%Y%m%d).tar.gz
"""


def _encode(value):
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__bytes_hex__": bytes(value).hex()}
    if isinstance(value, (list, dict)):
        return json.loads(json.dumps(value, default=_encode))
    return str(value)


def connect():
    import db_sources
    try:
        return db_sources.db()
    except (OSError, IOError) as exc:
        raise RuntimeError("缺少 SUPABASE_DB_PASSWORD：备份只能在 CI 或配置凭据后执行") from exc


def _tables(cur):
    cur.execute("""
        select table_name from information_schema.tables
         where table_schema='public' and table_type='BASE TABLE'
         order by table_name
    """)
    return [r[0] for r in cur.fetchall() if r[0] not in EXCLUDE_TABLES]


def _schema_sql(cur):
    """导出视图 / 函数 / 触发器定义；显式排除任何涉及凭据表的对象。"""
    cur.execute("""
        select c.relname, pg_get_viewdef(c.oid, true)
          from pg_class c join pg_namespace n on n.oid=c.relnamespace
         where n.nspname='public' and c.relkind='v'
         order by c.relname
    """)
    parts = ["-- 视图/函数定义（自动导出，不含凭据表）\n"]
    for name, definition in cur.fetchall():
        if "data_source_secret" in (definition or ""):
            continue
        parts.append("create or replace view public.%s as\n%s;\n" % (name, definition))
    cur.execute("""
        select p.proname, pg_get_functiondef(p.oid)
          from pg_proc p join pg_namespace n on n.oid=p.pronamespace
         where n.nspname='public'
         order by p.proname
    """)
    for name, definition in cur.fetchall():
        if "data_source_secret" in (definition or ""):
            continue
        parts.append("%s;\n" % definition.rstrip().rstrip(";"))
    return "\n".join(parts)


def _ddl_files() -> dict:
    """把仓库 sql/ 目录的权威建表 DDL 一并打包，使备份自包含。

    表结构、约束、触发器、RLS policy 都由这些编号脚本定义；此前备份只带
    数据，恢复时必须另找仓库对应版本，换项目/归档久了就对不上。
    """
    root = Path(__file__).resolve().parent / "sql"
    return {p.name: p.read_text(encoding="utf-8")
            for p in sorted(root.glob("*.sql"))}


def _runtime_state(cur) -> dict:
    """导出不属于 public 业务表、但换项目必然丢失的运行时状态。

    重点是 pg_cron 任务（在 cron schema，_tables 只扫 public 会漏掉）与
    序列当前值；RLS 开关、触发器/视图清单作为恢复后的核对依据。
    """
    state = {"sequences": [], "cron_jobs": [], "rls_enabled_tables": [],
             "triggers": [], "views": [], "functions": []}
    cur.execute("""select sequencename, last_value from pg_sequences
                    where schemaname='public' order by sequencename""")
    state["sequences"] = [{"name": r[0], "last_value": _encode(r[1])}
                          for r in cur.fetchall()]
    try:
        cur.execute("select * from cron.job order by jobid")
        cols = [d[0] for d in cur.description]
        state["cron_jobs"] = [{c: _encode(v) for c, v in zip(cols, row)}
                              for row in cur.fetchall()]
    except Exception as exc:            # 无 pg_cron 扩展时留痕，不阻断备份
        state["cron_jobs_error"] = str(exc)[:160]
    cur.execute("""select c.relname from pg_class c
                     join pg_namespace n on n.oid=c.relnamespace
                    where n.nspname='public' and c.relkind='r'
                      and c.relrowsecurity and not c.relname = any(%s)
                    order by 1""", (sorted(EXCLUDE_TABLES),))
    state["rls_enabled_tables"] = [r[0] for r in cur.fetchall()]
    cur.execute("""select c.relname, t.tgname, pg_get_triggerdef(t.oid)
                     from pg_trigger t join pg_class c on c.oid=t.tgrelid
                     join pg_namespace n on n.oid=c.relnamespace
                    where n.nspname='public' and not t.tgisinternal
                    order by 1,2""")
    state["triggers"] = [{"table": r[0], "name": r[1], "definition": r[2]}
                         for r in cur.fetchall() if r[0] not in EXCLUDE_TABLES]
    cur.execute("""select c.relname from pg_class c
                     join pg_namespace n on n.oid=c.relnamespace
                    where n.nspname='public' and c.relkind='v' order by 1""")
    state["views"] = [r[0] for r in cur.fetchall()]
    cur.execute("""select p.proname from pg_proc p
                     join pg_namespace n on n.oid=p.pronamespace
                    where n.nspname='public' order by 1""")
    state["functions"] = [r[0] for r in cur.fetchall()]
    return state


def _table_ddl(cur, tables) -> str:
    """从系统目录导出全部业务表的建表语句（列 + 类型 + NOT NULL）。

    仓库 sql/ 只覆盖 12/24 张表，prices_monthly / dataset_rows /
    nominal_monthly 等最大的表是 db_init.py 运行时创建的。仅打包 sql/
    会让备份在换库恢复时缺表结构。这里从 pg_catalog 现取，保证
    归档自包含；刻意不导约束/触发器/默认值/RLS，因为冷备是只读副本，
    且 Supabase 平台对象（pg_cron/vault/net）在其它 PG 环境中不存在。
    """
    lines = ["-- 自动导出：全部业务表结构（由 db_backup.py 从 pg_catalog 生成）",
             "-- 不含约束 / 触发器 / RLS / 默认值；冷备只读副本使用。", ""]
    for table in tables:
        q = 'public."%s"' % table.replace('"', '""')
        try:
            cur.execute("""select a.attname, format_type(a.atttypid, a.atttypmod),
                                  a.attnotnull
                           from pg_attribute a
                           where a.attrelid = %s::regclass
                             and a.attnum > 0 and not a.attisdropped
                           order by a.attnum""", (q,))
            cols = cur.fetchall()
        except Exception as exc:
            lines.append("-- %s 结构读取失败：%s" % (table, str(exc)[:100]))
            continue
        if not cols:
            continue
        body = ",\n  ".join(
            '"%s" %s%s' % (name.replace('"', '""'), typ,
                           " not null" if notnull else "")
            for name, typ, notnull in cols)
        lines.append('create table if not exists %s (\n  %s\n);\n' % (q, body))
    return "\n".join(lines) + "\n"


def _fingerprints(cur, tables):
    """在库内计算轻量指纹，只回传 (行数, 哈希和)，不传任何表数据。

    历史冻结表（prices_monthly / dataset_rows / nominal_monthly 等）永不
    变化，它们占备份体积的 90% 以上。把这些表的「是否变化」判断下推到
    数据库，每日整库备份就只需为运行时表付出口流量。
    """
    out = {}
    for table in tables:
        try:
            cur.execute('select count(*), coalesce(sum(hashtext(t::text)::bigint),0) '
                        'from public."%s" t' % table.replace('"', '""'))
            n, h = cur.fetchone()
            out[table] = {"rows": int(n), "hash": str(h)}
        except Exception as exc:
            # 指纹不可用不能让备份失败；标记后强制全量拉该表。
            out[table] = {"rows": -1, "hash": "unavailable:" + type(exc).__name__}
    return out


def _previous_archive(path):
    """读取上一次归档的指纹与表字节（已按 manifest sha256 逐个校验）。"""
    p = Path(path)
    if not p or not Path(p).exists():
        return {}, {}
    try:
        with tarfile.open(p, "r:gz") as tar:
            members = {m.name: tar.extractfile(m).read() for m in tar.getmembers()}
        m = json.loads(members["manifest.json"])
        fps = m.get("table_fingerprints") or {}
        payload = {}
        for t, meta in (m.get("tables") or {}).items():
            raw = members.get("tables/%s.jsonl.gz" % t)
            if raw and hashlib.sha256(raw).hexdigest() == meta["sha256"]:
                payload[t] = {"rows": meta["rows"], "sha256": meta["sha256"],
                              "bytes": len(raw), "data": raw}
        return fps, payload
    except Exception as exc:
        print("WARN 上次归档不可复用，退化为全量：%s" % str(exc)[:120],
              file=sys.stderr)
        return {}, {}


def _dump_table(cur, table):
    """返回 (行数, gzip 字节, sha256)。表名来自 information_schema，安全。"""
    cur.execute('select * from public."%s"' % table.replace('"', '""'))
    cols = [d[0] for d in cur.description]
    out = io.BytesIO()
    rows = 0
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as gz:
        while True:
            batch = cur.fetchmany(2000)
            if not batch:
                break
            for row in batch:
                obj = {c: _encode(v) for c, v in zip(cols, row)}
                gz.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":"),
                                    default=_encode).encode("utf-8") + b"\n")
                rows += 1
    data = out.getvalue()
    return rows, data, hashlib.sha256(data).hexdigest()


def build(previous=None) -> dict:
    """生成自包含整库快照；previous 存在时复用未变化表的数据字节。

    归档内容始终是完整的（复用字节也写进新归档），因此恢复流程不变；
    只有「从 Supabase 传出来的字节」按变化量计费。
    """
    prev_fps, prev_payload = _previous_archive(previous) if previous else ({}, {})
    conn = connect()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("set statement_timeout = '300s'")
            tables = _tables(cur)
            table_ddl = _table_ddl(cur, tables)
            fingerprints = _fingerprints(cur, tables)
            schema_sql = _schema_sql(cur)
            runtime = _runtime_state(cur)
            payload, reused, fetched = {}, [], []
            for table in tables:
                fp = fingerprints[table]
                reuse = (table in prev_payload and fp["rows"] >= 0
                         and prev_fps.get(table) == fp)
                if reuse:
                    payload[table] = prev_payload[table]
                    reused.append(table)
                else:
                    rows, data, sha = _dump_table(cur, table)
                    payload[table] = {"rows": rows, "sha256": sha,
                                      "bytes": len(data), "data": data}
                    fetched.append(table)
    finally:
        conn.close()
    fetched_rows = sum(payload[t]["rows"] for t in fetched)
    print("[db-backup] 重新传输 %d 表 / %d 行，复用 %d 表（%s）"
          % (len(fetched), fetched_rows, len(reused),
             "有基线" if prev_payload else "首次全量"), file=sys.stderr, flush=True)
    return {"tables": tables, "schema_sql": schema_sql, "runtime": runtime,
            "ddl_files": _ddl_files(), "table_ddl": table_ddl,
            "payload": payload, "table_fingerprints": fingerprints,
            "reused_tables": reused, "fetched_tables": fetched}


def write_archive(target: Path, snapshot: dict) -> dict:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    generated = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    manifest = {
        "schema": 1,
        "kind": "full-logical-backup",
        "generated_at": generated,
        "source": "postgres-direct (read-only)",
        "excluded_tables": sorted(EXCLUDE_TABLES),
        "tables": {t: {"rows": snapshot["payload"][t]["rows"],
                       "sha256": snapshot["payload"][t]["sha256"],
                       "bytes": snapshot["payload"][t]["bytes"]}
                   for t in snapshot["tables"]},
        "total_tables": len(snapshot["tables"]),
        "total_rows": sum(snapshot["payload"][t]["rows"] for t in snapshot["tables"]),
        "table_fingerprints": snapshot.get("table_fingerprints", {}),
        "reused_tables": snapshot.get("reused_tables", []),
        "fetched_tables": snapshot.get("fetched_tables", list(snapshot["tables"])),
        "fetched_rows": sum(snapshot["payload"][t]["rows"]
                            for t in snapshot.get("fetched_tables", snapshot["tables"])),
        "ddl_files": {name: hashlib.sha256(text.encode("utf-8")).hexdigest()
                      for name, text in snapshot["ddl_files"].items()},
        "tables_schema_sha256": hashlib.sha256(
            snapshot.get("table_ddl", "").encode("utf-8")).hexdigest(),
        "runtime_objects": {"sequences": len(snapshot["runtime"]["sequences"]),
                            "cron_jobs": len(snapshot["runtime"]["cron_jobs"]),
                            "rls_enabled_tables": len(snapshot["runtime"]["rls_enabled_tables"]),
                            "triggers": len(snapshot["runtime"]["triggers"]),
                            "views": len(snapshot["runtime"]["views"]),
                            "functions": len(snapshot["runtime"]["functions"])},
    }
    tmp_fd, tmp = tempfile.mkstemp(prefix=".dbbackup-", suffix=".tar.gz",
                                   dir=str(target.parent))
    os.close(tmp_fd)
    try:
        members = {}
        add = lambda name, data: members.__setitem__(name, data)
        add("README.txt", README.format(
            generated_at=generated,
            excluded=", ".join(sorted(EXCLUDE_TABLES))).encode("utf-8"))
        add("schema.sql", snapshot["schema_sql"].encode("utf-8"))
        add("tables_schema.sql", snapshot.get("table_ddl", "").encode("utf-8"))
        add("runtime.json", json.dumps(snapshot["runtime"], ensure_ascii=False,
                                       indent=2, sort_keys=True).encode("utf-8"))
        for name, text in snapshot["ddl_files"].items():
            add("ddl/" + name, text.encode("utf-8"))
        for t in snapshot["tables"]:
            add("tables/%s.jsonl.gz" % t, snapshot["payload"][t]["data"])
        add("manifest.json", json.dumps(manifest, ensure_ascii=False,
                                        indent=2, sort_keys=True).encode("utf-8"))
        # 归档整体指纹：覆盖除自身外全部成员的名称+内容，任何字节改动
        # （含追加/截断/gzip trailer 篡改）都能被 verify_archive 发现。
        outer = hashlib.sha256(b"".join(
            name.encode("utf-8") + b"\0" + data
            for name, data in sorted(members.items()))).hexdigest()
        members["checksum.json"] = json.dumps(
            {"members_sha256": outer, "generated_at": generated},
            ensure_ascii=False, sort_keys=True).encode("utf-8")

        with tarfile.open(tmp, "w:gz") as tar:
            for name, data in sorted(members.items()):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mtime = 0
                tar.addfile(info, io.BytesIO(data))
        # 落盘后回读校验：每个成员可读、hash 与 manifest 一致
        with tarfile.open(tmp, "r:gz") as tar:
            readback = {m.name: tar.extractfile(m).read() for m in tar.getmembers()}
        check = json.loads(readback["manifest.json"])
        for t, meta in check["tables"].items():
            raw = readback["tables/%s.jsonl.gz" % t]
            if hashlib.sha256(raw).hexdigest() != meta["sha256"]:
                raise RuntimeError("备份回读校验失败：" + t)
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    manifest["archive"] = str(target)
    manifest["archive_bytes"] = target.stat().st_size
    manifest["archive_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    return manifest


def verify_archive(path: Path, expected_file_sha: str | None = None) -> dict:
    path = Path(path)
    with tarfile.open(path, "r:gz") as tar:
        members = {m.name: tar.extractfile(m).read() for m in tar.getmembers()}
    if "manifest.json" not in members:
        raise RuntimeError("缺少 manifest.json")
    m = json.loads(members["manifest.json"])
    problems = []
    # 文件级指纹：捕获 gzip trailer 篡改、追加字节、截断等 tar 结构外的变化
    if expected_file_sha:
        actual_file = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_file != expected_file_sha:
            problems.append("归档文件整体 sha256 不符（字节级损坏或篡改）")
    # 归档整体指纹（覆盖所有成员名称+内容），可发现追加/截断/字节篡改
    if "checksum.json" not in members:
        problems.append("缺少 checksum.json（无法验证归档整体完整性）")
    else:
        expected = (json.loads(members["checksum.json"]) or {}).get("members_sha256")
        actual = hashlib.sha256(b"".join(
            name.encode("utf-8") + b"\0" + data
            for name, data in sorted(members.items())
            if name != "checksum.json")).hexdigest()
        if expected != actual:
            problems.append("归档整体校验和不符（文件可能被篡改或损坏）")
    if set(m.get("excluded_tables", [])) != EXCLUDE_TABLES:
        problems.append("排除表清单与当前策略不一致")
    # 自包含恢复所需的 DDL 与运行时状态，缺一不可
    if not m.get("ddl_files"):
        problems.append("备份未包含建表 DDL（无法自包含恢复）")
    for name, digest in m.get("ddl_files", {}).items():
        key = "ddl/" + name
        if key not in members:
            problems.append("缺少 DDL：" + name)
        elif hashlib.sha256(members[key]).hexdigest() != digest:
            problems.append("DDL 校验和不符：" + name)
    schema_key = "tables_schema.sql"
    body = members.get(schema_key, b"")
    if not body.strip() or b"create table" not in body.lower():
        problems.append("tables_schema.sql 为空或不含建表语句（全表结构不可恢复）")
    elif m.get("tables_schema_sha256") != hashlib.sha256(body).hexdigest():
        problems.append("tables_schema.sql 校验和不符")
    if "runtime.json" not in members:
        problems.append("缺少 runtime.json（序列 / pg_cron / RLS 状态不可恢复）")
    else:
        try:
            json.loads(members["runtime.json"])
        except Exception:
            problems.append("runtime.json 不可解析")
    for t, meta in m.get("tables", {}).items():
        key = "tables/%s.jsonl.gz" % t
        if key not in members:
            problems.append("缺少表数据：" + t)
            continue
        raw = members[key]
        if hashlib.sha256(raw).hexdigest() != meta["sha256"]:
            problems.append("校验和不符：" + t)
        else:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
                lines = sum(1 for line in gz if line.strip())
            if lines != meta["rows"]:
                problems.append("行数不符：%s (%d != %d)" % (t, lines, meta["rows"]))
    return {"ok": not problems, "problems": problems,
            "tables": m.get("total_tables"), "rows": m.get("total_rows")}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", default="output/backups/trade-backup.tar.gz")
    p.add_argument("--verify", metavar="FILE", help="只读校验既有备份")
    p.add_argument("--expected-sha256", help="归档文件整体 sha256（由生成步骤输出）")
    p.add_argument("--previous", metavar="FILE",
                   help="上一次归档；未变化表复用其字节，只从数据库传变化表")
    a = p.parse_args()
    if a.verify:
        result = verify_archive(Path(a.verify), a.expected_sha256)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1
    manifest = write_archive(Path(a.output), build(a.previous))
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
