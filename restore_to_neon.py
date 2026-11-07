#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本地整库逻辑备份灌入 Neon，作为异地只读冷备。

设计边界
  · 只导入「表结构 + 表数据」。Supabase 平台特有对象（pg_cron / pg_net /
    vault / Auth / Storage / API keys / 角色口令）不属于 public schema，
    Neon 也没有这些扩展，因此不导入触发器、RLS policy、守卫函数。
    冷备是只读副本，不需要生产库的 append-only 守卫。
  · 连接串只从本地 output/neon_cold_backup.local.json 读取，
    该文件已在 .gitignore 中，绝不进入仓库。
  · 导入前逐表校验行数与 manifest 一致，不一致立即报错。
  · 可重复执行（--reset 先清空已导入的业务表）。

用法
  python restore_to_neon.py --archive ../backups/trade-20261107.tar.gz
  python restore_to_neon.py --archive ... --verify-only
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import sys
import tarfile
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, execute_values

ROOT = Path(__file__).resolve().parent
CRED = ROOT / "output" / "neon_cold_backup.local.json"


def connection_string() -> str:
    if not CRED.exists():
        raise RuntimeError("缺少本地连接串文件：" + str(CRED))
    cs = json.loads(CRED.read_text(encoding="utf-8-sig"))["connection_string"]
    # libpq 的某些构建不认 channel_binding 参数；sslmode=require 已足够。
    return cs.replace("&channel_binding=require", "")


def extract_create_tables(sql_text: str) -> list[str]:
    """按括号平衡提取 DDL 中的 CREATE TABLE 语句，跳过注释与函数体。"""
    text = re.sub(r"--[^\n]*", "", sql_text)
    low = text.lower()
    out, pos = [], 0
    while True:
        m = re.search(r"\bcreate\s+table\b", low[pos:])
        if not m:
            break
        start = pos + m.start()
        p = text.find("(", start)
        if p < 0:
            break
        depth, j, in_str = 0, p, False
        while j < len(text):
            ch = text[j]
            if ch == "'":
                in_str = not in_str
            elif not in_str:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        break
            j += 1
        semi = text.find(";", j)
        end = semi + 1 if semi >= 0 else len(text)
        stmt = text[start:end].strip()
        if "if not exists" not in stmt.lower():
            stmt = re.sub(r"(?i)^create\s+table\s+",
                          "create table if not exists ", stmt, count=1)
        out.append(stmt)
        pos = end
    return out


def load_archive(path: Path) -> dict:
    with tarfile.open(path, "r:gz") as tar:
        members = {m.name: tar.extractfile(m).read() for m in tar.getmembers()}
    manifest = json.loads(members["manifest.json"])
    ddl = {n: members[n].decode("utf-8", "ignore")
           for n in members if n.startswith("ddl/") and n.endswith(".sql")}
    tables = {}
    for t in manifest["tables"]:
        key = "tables/%s.jsonl.gz" % t
        if key in members:
            tables[t] = gzip.decompress(members[key]).decode("utf-8")
    return {"manifest": manifest, "ddl": ddl, "tables": tables,
            "members": members}


def build_schema(conn, arch: dict) -> dict:
    # 优先用归档内「从 pg_catalog 导出的全表结构」；旧归档才回退仓库 sql/
    # （后者只覆盖 12/24 张表，缺 prices_monthly 等最大的表）。
    sources = arch["ddl"]
    if "tables_schema.sql" in arch["members"]:
        sources = {"tables_schema.sql":
                   arch["members"]["tables_schema.sql"].decode("utf-8", "ignore")}
    print("[restore] 建表结构来源：%s" % ", ".join(sorted(sources)), flush=True)
    stmts, seen = [], set()
    for name in sorted(sources):
        for stmt in extract_create_tables(sources[name]):
            m = re.search(r"(?i)create\s+table\s+(?:if\s+not\s+exists\s+)?"
                          r"(?:public\.)?\"?([a-zA-Z0-9_]+)\"?", stmt)
            key = m.group(1) if m else stmt[:80]
            if key in seen:
                continue
            seen.add(key)
            stmts.append((key, stmt))
    ok, failed = [], []
    with conn.cursor() as cur:
        for key, stmt in stmts:
            try:
                cur.execute(stmt)
                ok.append(key)
            except Exception as exc:
                conn.rollback()
                failed.append({"table": key, "error": str(exc)[:180]})
    conn.commit()
    return {"created": ok, "failed": failed}


def column_types(conn, table: str) -> dict:
    with conn.cursor() as cur:
        cur.execute("""select column_name, data_type from information_schema.columns
                       where table_schema='public' and table_name=%s""", (table,))
        return {r[0]: r[1] for r in cur.fetchall()}


def restore_table(conn, table: str, text: str, batch: int = 2000) -> int:
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not rows:
        return 0
    types = column_types(conn, table)
    cols = [c for c in rows[0].keys() if c in types]
    if not cols:
        raise RuntimeError("目标库无对应列：" + table)
    collist = ",".join('"%s"' % c for c in cols)
    sql = 'insert into public."%s" (%s) values %%s' % (table, collist)
    total = 0
    with conn.cursor() as cur:
        for i in range(0, len(rows), batch):
            chunk = []
            for r in rows[i:i + batch]:
                vals = []
                for c in cols:
                    v = r.get(c)
                    if v is not None and types[c] in ("jsonb", "json") \
                            and isinstance(v, (dict, list)):
                        v = Json(v)
                    vals.append(v)
                chunk.append(vals)
            execute_values(cur, sql, chunk, page_size=batch)
            total += len(chunk)
    conn.commit()
    return total


def verify(conn, manifest: dict) -> dict:
    expected = {t: m["rows"] for t, m in manifest["tables"].items()}
    actual, problems = {}, []
    with conn.cursor() as cur:
        for t in sorted(expected):
            try:
                cur.execute('select count(*) from public."%s"' % t)
                actual[t] = cur.fetchone()[0]
            except Exception as exc:
                conn.rollback()
                actual[t] = -1
                problems.append("%s 无法读取：%s" % (t, str(exc)[:100]))
    for t, n in expected.items():
        if actual.get(t) != n:
            problems.append("%s 行数 %s != 期望 %s" % (t, actual.get(t), n))
    return {"ok": not problems, "expected_rows": sum(expected.values()),
            "actual_rows": sum(v for v in actual.values() if v > 0),
            "tables": len(expected), "problems": problems}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive", required=True, help="本地 .tar.gz 整库备份")
    p.add_argument("--verify-only", action="store_true", help="只校验行数，不建表不导入")
    p.add_argument("--reset", action="store_true", help="导入前清空业务表（重灌）")
    p.add_argument("--tables", help="只处理逗号分隔的表名（调试用）")
    a = p.parse_args()

    arch = load_archive(Path(a.archive))
    manifest = arch["manifest"]
    targets = sorted(arch["tables"])
    if a.tables:
        want = set(a.tables.split(","))
        targets = [t for t in targets if t in want]
    print("[restore] 归档 %s：%d 表 / %d 行 / %d DDL"
          % (Path(a.archive).name, manifest["total_tables"],
             manifest["total_rows"], len(arch["ddl"])), flush=True)

    conn = psycopg2.connect(connection_string(), connect_timeout=30)
    try:
        if not a.verify_only:
            schema = build_schema(conn, arch)
            print("[restore] 建表 %d，失败 %d" %
                  (len(schema["created"]), len(schema["failed"])), flush=True)
            for f in schema["failed"][:5]:
                print("  - %s: %s" % (f["table"], f["error"]), flush=True)
            if a.reset:
                with conn.cursor() as cur:
                    for t in targets:
                        cur.execute('truncate table public."%s"' % t)
                conn.commit()
            for i, t in enumerate(targets, 1):
                n = restore_table(conn, t, arch["tables"][t])
                print("[restore] %2d/%d %-28s %8d 行"
                      % (i, len(targets), t, n), flush=True)
        result = verify(conn, manifest)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 0 if result["ok"] else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
