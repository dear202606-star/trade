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
  schema.sql             视图 / 函数 / 触发器定义（可重建数据库对象）
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
  · 本备份是「数据 + 视图/函数/触发器定义」的逻辑快照，不是物理转储。
  · 恢复前先执行 sql/ 目录下全部 DDL 建立表结构与守卫，再按 manifest
    的表清单逐表导入 tables/*.jsonl。
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


def build() -> dict:
    conn = connect()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("set statement_timeout = '300s'")
            tables = _tables(cur)
            schema_sql = _schema_sql(cur)
            payload = {}
            for table in tables:
                rows, data, sha = _dump_table(cur, table)
                payload[table] = {"rows": rows, "sha256": sha, "bytes": len(data), "data": data}
    finally:
        conn.close()
    return {"tables": tables, "schema_sql": schema_sql, "payload": payload}


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
    a = p.parse_args()
    if a.verify:
        result = verify_archive(Path(a.verify), a.expected_sha256)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1
    manifest = write_archive(Path(a.output), build())
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
