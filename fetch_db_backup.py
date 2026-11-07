#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把最近一次成功的整库备份从 GitHub artifact 拉回本地并校验。

定时由 Windows 计划任务调用（install_backup_task.ps1），无需人工触发。
产物落在工作区 backups/：trade-YYYYMMDD.tar.gz（按生成日期去重），
每个文件都经过 db_backup.py --verify 的成员级 + 文件级完整性复核。

用法
  python fetch_db_backup.py                 # 拉最近一次成功备份
  python fetch_db_backup.py --run-id ID    # 指定某次工作流运行
  python fetch_db_backup.py --list         # 只列出本地已有备份
  python fetch_db_backup.py --prune 10     # 额外清理超过 10 份的旧归档
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEST = ROOT.parent / "backups"
REPO = "dear202606-star/trade"
ARTIFACT = "trade-db-backup"


def run(args, binary=False, timeout=900):
    p = subprocess.run(args, capture_output=True, text=not binary, timeout=timeout)
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", "ignore") \
            if isinstance(p.stderr, bytes) else str(p.stderr)
        raise RuntimeError("命令失败 %s：%s" % (args[:3], err[:200]))
    return p.stdout


def latest_success_run() -> int:
    out = run(["gh", "run", "list", "--repo", REPO,
               "--workflow", "db-backup.yml", "--limit", "30",
               "--json", "databaseId,conclusion,createdAt",
               "--jq", '[.[] | select(.conclusion=="success")][0]'])
    row = json.loads(out or "null")
    if not row:
        raise RuntimeError("找不到成功的整库备份工作流运行")
    return int(row["databaseId"])


def artifact_id(run_id: int) -> int:
    out = run(["gh", "api",
               "repos/%s/actions/runs/%d/artifacts" % (REPO, run_id),
               "--jq", '.artifacts[] | select(.name=="%s") | .id' % ARTIFACT])
    ids = [x for x in out.splitlines() if x.strip()]
    if not ids:
        raise RuntimeError("运行 %d 没有 %s 产物（可能已过期或被预算门禁跳过）"
                           % (run_id, ARTIFACT))
    return int(ids[0])


def download(run_id: int) -> Path:
    aid = artifact_id(run_id)
    raw = run(["gh", "api",
               "repos/%s/actions/artifacts/%d/zip" % (REPO, aid)],
              binary=True, timeout=900)
    if not raw or raw[:2] != b"PK":
        raise RuntimeError("artifact 下载内容不是 zip（%d 字节）" % len(raw or b""))
    DEST.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = [n for n in z.namelist() if n.endswith(".tar.gz")]
        if not names:
            raise RuntimeError("artifact 里没有 .tar.gz 归档")
        name = names[0]
        data = z.read(name)
    stamp = "".join(c for c in Path(name).stem if c.isdigit())[:8] or \
        str(run_id)[:8]
    target = DEST / ("trade-%s.tar.gz" % stamp)
    if target.exists() and len(target.read_bytes()) == len(data):
        print("[fetch] 本地已存在同名且大小一致的归档，跳过写入：%s" % target.name)
    else:
        tmp = target.with_suffix(".tar.gz.part")
        tmp.write_bytes(data)
        tmp.replace(target)
    return target


def verify(path: Path) -> dict:
    p = subprocess.run([sys.executable, str(ROOT / "db_backup.py"),
                        "--verify", str(path)],
                       capture_output=True, text=True, cwd=str(ROOT), timeout=180)
    out = (p.stdout or "").strip()
    try:
        return json.loads(out.splitlines()[-1])
    except Exception:
        return {"ok": False, "problems": ["verify 输出不可解析：" + out[:160]]}


def local_archives() -> list[Path]:
    if not DEST.exists():
        return []
    return sorted(DEST.glob("trade-*.tar.gz"), reverse=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-id", type=int, help="指定工作流运行 ID")
    ap.add_argument("--list", action="store_true", help="只列出本地归档")
    ap.add_argument("--prune", type=int, default=0, metavar="N",
                    help="只保留最近 N 份，其余报告出来但不删除")
    a = ap.parse_args()

    if a.list:
        for p in local_archives():
            print("%-32s %10.2f MB" % (p.name, p.stat().st_size / 1048576))
        return 0

    run_id = a.run_id or latest_success_run()
    print("[fetch] 源运行 %d" % run_id, flush=True)
    target = download(run_id)
    print("[fetch] 落地 %s (%.2f MB)" %
          (target, target.stat().st_size / 1048576), flush=True)
    result = verify(target)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if not result.get("ok"):
        print("FATAL: 完整性复核未通过，保留文件供排查但不视为有效备份",
              file=sys.stderr)
        return 1

    files = local_archives()
    print("[fetch] 本地共 %d 份有效归档：" % len(files))
    for p in files[:5]:
        print("   %-32s %10.2f MB" % (p.name, p.stat().st_size / 1048576))
    if a.prune and len(files) > a.prune:
        print("[fetch] 超过保留数 %d 的旧归档（未删除，需人工确认）：" % a.prune)
        for p in files[a.prune:]:
            print("   " + str(p))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
