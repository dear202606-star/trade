"""把站点展示数据统一发布到在线数据库（取代 Pages 上的 data/*.json）。

2026-11-06：页面此前一部分数据走 Supabase 在线视图、另一部分读
``data/*.json`` 静态快照，双源不一致会让移动端显示「数据未就绪」而
桌面正常。本脚本把构建期生成的全部展示数据写入 ``site_dataset``，
并在发布前从 Pages 产物中剔除（--prune），使在线库成为唯一数据源。

用法
  python publish_site_dataset.py --install --source docs/data --prune
  python publish_site_dataset.py --verify          # 只读校验线上 key 齐备
  python publish_site_dataset.py --keys --source docs/data   # 本地 dry-run
"""
import argparse
import hashlib
import io
import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
DDL = BASE / "sql" / "122_site_dataset.sql"

# 前端实际会读取的数据集（runtime 4 类走专用在线视图，不在此列）
# 说明：history.json 由 site_build.py 在构建期生成、用于 signals.html 渲染，
# 但从未发布到 Pages，前端也不读取，因此不作为在线数据集的必需项。
REQUIRED = (
    "curve", "strategy_history", "health", "name_zh", "sectors",
)
# B 口径（页面默认）专有的数据集，仅在完整数据管线的构建产物中存在；
# 本地 site_build.py 不带 variant_b 输出，故 dry-run 只提示、不阻断。
REQUIRED_B = ("b/curve", "b/strategy_history")
# 引导配置必须保留在 Pages 上，否则浏览器无从知道数据库地址与公开 key
KEEP_STATIC = {"cloud"}


def sector_dataset():
    """合并冻结行业表与历史退市覆盖，供持仓表显示 GICS 行业。"""
    out = {}
    for name in ("sector_frozen.json", "sector_overrides.json"):
        path = BASE / "static" / name
        if not path.exists():
            raise RuntimeError("缺少行业映射：" + str(path))
        data = json.loads(path.read_text(encoding="utf-8"))
        out.update({k: v for k, v in data.items()
                    if not k.startswith("_") and isinstance(v, str)})
    if len(out) < 400:
        raise RuntimeError("行业映射条目异常偏少：%d" % len(out))
    return out


def collect(source):
    """读取构建期数据目录，返回 {key: payload}；key 去掉 .json 后缀。"""
    root = Path(source)
    if not root.is_dir():
        raise RuntimeError("数据目录不存在：" + str(root))
    result = {}
    for path in sorted(root.rglob("*.json")):
        key = path.relative_to(root).with_suffix("").as_posix()
        if key in KEEP_STATIC:
            continue
        try:
            result[key] = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError("数据不可解析 %s：%s" % (path, exc)) from exc
    result["sectors"] = sector_dataset()
    return result


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def connection():
    import psycopg2  # 本地无依赖时由调用方给出清晰错误
    import db_sources
    try:
        return db_sources.db()
    except (OSError, IOError):
        raise RuntimeError("缺少 SUPABASE_DB_PASSWORD：在线数据发布只能在 CI 或配置凭据后执行")


def install(cur):
    cur.execute("set statement_timeout = '120s'")
    cur.execute(DDL.read_text(encoding="utf-8"))
    print("DDL 已执行：", DDL.name)


def prune_files(source):
    """发布产物中只保留 cloud.json；返回 (删除数, 保留文件名列表)。"""
    root = Path(source)
    removed = 0
    for path in sorted(root.rglob("*.json")):
        if path.relative_to(root).with_suffix("").as_posix() in KEEP_STATIC:
            continue
        path.unlink()
        removed += 1
    # 清空空目录（如 data/b/），但保留 data/ 本身
    for d in sorted((p for p in root.rglob("*") if p.is_dir()),
                    key=lambda p: len(p.parts), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()
    return removed, [p.name for p in root.rglob("*.json")]


def publish(records, prune=False, source=None):
    conn = connection()
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            from psycopg2.extras import execute_values
            execute_values(cur, """insert into public.site_dataset(key,payload,source_sha256)
                values %s on conflict(key) do update set
                  payload=excluded.payload,source_sha256=excluded.source_sha256,updated_at=now()
                  where public.site_dataset.source_sha256 <> excluded.source_sha256""",
                [(k, json.dumps(v, ensure_ascii=False), digest(v)) for k, v in records.items()],
                page_size=50)
            cur.execute("select count(*),max(updated_at) from public.site_dataset")
            total, updated = cur.fetchone()
        conn.commit()
        print(json.dumps({"published": len(records), "online_rows": total,
                          "updated_at": str(updated), "keys": sorted(records)},
                         ensure_ascii=False))
    finally:
        conn.close()

    if prune:
        removed, keep = prune_files(source)
        print(json.dumps({"pruned": removed, "kept": keep}, ensure_ascii=False))
        if keep != ["cloud.json"]:
            raise RuntimeError("Pages 产物仍含非引导数据文件：%s" % keep)


def verify():
    """只读校验：线上数据集齐备且与本地必需集合一致。"""
    import urllib.request
    cfg = json.loads((BASE / "site_src" / "cloud.json").read_text(encoding="utf-8"))
    url = cfg["url"].rstrip("/") + "/rest/v1/v_site_dataset?select=key,source_sha256,updated_at"
    req = urllib.request.Request(url, headers={"apikey": cfg["key"],
                                               "Authorization": "Bearer " + cfg["key"]})
    with urllib.request.urlopen(req, timeout=25) as resp:
        rows = json.load(resp)
    online = {r["key"] for r in rows}
    missing = sorted((set(REQUIRED) | set(REQUIRED_B)) - online)
    print(json.dumps({"online_keys": len(online), "required": len(REQUIRED) + len(REQUIRED_B),
                      "missing": missing}, ensure_ascii=False))
    return not missing


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", default="docs/data")
    p.add_argument("--install", action="store_true", help="先执行建表 DDL")
    p.add_argument("--prune", action="store_true", help="发布后从 Pages 产物剔除数据 JSON")
    p.add_argument("--keys", action="store_true", help="本地 dry-run，不连库")
    p.add_argument("--verify", action="store_true", help="只读校验线上数据集")
    a = p.parse_args()

    if a.verify:
        return 0 if verify() else 1
    records = collect(a.source)
    missing = sorted(set(REQUIRED) - set(records))
    if missing:
        print("FATAL: 构建产物缺少必需数据集 %s" % missing, file=sys.stderr)
        return 1
    missing_b = sorted(set(REQUIRED_B) - set(records))
    if a.keys:
        print(json.dumps({"keys": sorted(records), "variant_b_missing": missing_b,
                          "sha256": {k: digest(v)[:12] for k, v in records.items()}},
                         ensure_ascii=False))
        return 0
    if missing_b:
        print("FATAL: 完整数据管线产物缺少 B 口径数据集 %s" % missing_b, file=sys.stderr)
        return 1
    if a.install:
        conn = connection()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                install(cur)
        finally:
            conn.close()
    publish(records, prune=a.prune, source=a.source)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
