"""export_site_snapshot.py — 从在线库直连导出一份「同源展示镜像」到 Pages。

背景（2026-11-07）
  Supabase 项目的 PostgREST 网关曾因 exceed_egress_quota 返回 HTTP 402，
  而页面此前所有展示数据都只走该网关，导致数据库一切正常却全站白屏。
  同时前端每 60 秒全量重拉所有数据集，是配额被打穿的直接放大器。

设计原则
  1. 在线库仍是**唯一主源与唯一写入口**；本脚本只读，不新造第二份数据。
  2. 镜像由 CI 通过 **PostgreSQL 直连**（不经 REST 网关）自动导出，
     因此 REST 被限流时依然能生成，且与库内数据逐字同源。
  3. 分层：低频/历史数据集由浏览器直接读镜像（零 REST 流量）；
     日内数据集在线库优先、失败自动回退镜像。
  4. Fail-closed：任一必需数据集缺失或结构不合法即整体失败，
     并且**先写临时文件、校验通过后才原子替换**已有镜像，
     绝不用半份或坏数据覆盖上一份可用镜像。

用法
  python export_site_snapshot.py --target _site_release/data/snapshot.json
  python export_site_snapshot.py --dry-run          # 只校验，不落盘
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent

# 页面实际读取的全部数据集。命名必须与前端 DSH.json(name) 完全一致。
STATIC_KEYS = (
    "curve", "b/curve", "strategy_history", "b/strategy_history",
    "name_zh", "sectors", "health",
)
RUNTIME_KEYS = (
    "quotes", "equity", "b/equity", "signal", "b/signal",
    "preview", "b/preview",
)
ALL_KEYS = STATIC_KEYS + RUNTIME_KEYS


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _utcnow():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def connect():
    """只走 PostgreSQL 直连；REST 网关的配额状态与这里无关。"""
    import db_sources
    try:
        return db_sources.db()
    except (OSError, IOError) as exc:
        raise RuntimeError(
            "缺少 SUPABASE_DB_PASSWORD：镜像导出只能在 CI 或配置凭据后执行"
        ) from exc


def _rows(cur, sql, params=None):
    cur.execute(sql, params or ())
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def collect():
    """返回 (datasets, meta)。任一步异常都向上抛，由调用方 fail-closed。"""
    datasets, meta = {}, {}
    conn = connect()
    try:
        conn.autocommit = True      # 纯只读查询；不设 readonly，兼容连接池事务模式
        with conn.cursor() as cur:
            cur.execute("set statement_timeout = '60s'")

            # ── 1. 历史 / 映射类：site_dataset 表 ──────────────────────────
            for row in _rows(cur, """
                select key, payload, source_sha256, updated_at
                from public.site_dataset
            """):
                key = row["key"]
                if key not in STATIC_KEYS:
                    continue
                datasets[key] = row["payload"]
                meta[key] = {
                    "sha256": row["source_sha256"] or digest(row["payload"]),
                    "updated_at": str(row["updated_at"]),
                    "origin": "site_dataset",
                }

            # ── 2. 行情快照：v_live_quote_display（单行、仅展示所需标的）──
            q = _rows(cur, "select payload from public.v_live_quote_display")
            if len(q) != 1:
                raise RuntimeError("行情展示视图必须且只能有 1 行，实际 %d" % len(q))
            datasets["quotes"] = q[0]["payload"]
            meta["quotes"] = {
                "sha256": digest(q[0]["payload"]),
                "updated_at": str(q[0]["payload"].get("ts") or q[0]["payload"].get("ts_local") or ""),
                "origin": "v_live_quote_display",
            }

            # ── 3. 在线净值 / 次年预览：v_live_strategy_results ───────────
            for row in _rows(cur, """
                select variant, kind, payload, source_as_of, created_at
                from public.v_live_strategy_results
            """):
                key = ("b/" if row["variant"] == "B" else "") + row["kind"]
                if key not in ("equity", "b/equity", "preview", "b/preview"):
                    continue
                datasets[key] = row["payload"]
                meta[key] = {
                    "sha256": digest(row["payload"]),
                    "updated_at": str(row["source_as_of"] or row["created_at"]),
                    "origin": "v_live_strategy_results",
                }

            # ── 4. 策略信号 / 大盘开关：live_strategy_anchor ───────────────
            for row in _rows(cur, """
                select distinct on (variant) variant, hold_year, revision,
                       payload, created_at
                from public.live_strategy_anchor
                order by variant, hold_year desc, revision desc
            """):
                key = ("b/" if row["variant"] == "B" else "") + "signal"
                payload = row["payload"] or {}
                datasets[key] = payload.get("signal")
                meta[key] = {
                    "sha256": digest(payload.get("signal")),
                    "updated_at": str(row["created_at"]),
                    "origin": "live_strategy_anchor",
                    "hold_year": row["hold_year"],
                    "revision": row["revision"],
                }
    finally:
        conn.close()
    return datasets, meta


def validate(datasets):
    """结构级门禁：缺任一必需项、或核心字段不合法，一律拒绝发布。"""
    problems = []

    missing = [k for k in ALL_KEYS if k not in datasets or datasets[k] is None]
    if missing:
        problems.append("缺失数据集: %s" % ", ".join(missing))
        return problems  # 后续逐项校验依赖这些键存在，直接返回

    q = datasets["quotes"]
    if not isinstance(q, dict) or not q.get("ok") or not isinstance(q.get("quotes"), dict) \
            or "SPY" not in q["quotes"]:
        problems.append("quotes 缺少 ok / quotes.SPY")

    for key in ("equity", "b/equity"):
        eq = datasets[key]
        if not isinstance(eq, dict):
            problems.append("%s 非对象" % key)
            continue
        if not isinstance(eq.get("position"), list) or not eq["position"]:
            problems.append("%s.position 为空" % key)
        stats = eq.get("stats") or {}
        if not isinstance(stats.get("strategy"), (int, float)) or \
                not math.isfinite(float(stats["strategy"])):
            problems.append("%s.stats.strategy 非法" % key)

    for key in ("signal", "b/signal"):
        sig = datasets[key]
        if not isinstance(sig, dict) or not isinstance(sig.get("official"), list) \
                or not sig["official"]:
            problems.append("%s.official 为空" % key)

    for key in ("preview", "b/preview"):
        if not isinstance(datasets[key], dict):
            problems.append("%s 非对象" % key)

    for key in ("curve", "b/curve", "strategy_history", "b/strategy_history"):
        v = datasets[key]
        if not isinstance(v, (dict, list)) or not v:
            problems.append("%s 为空" % key)

    for key in ("name_zh", "sectors"):
        v = datasets[key]
        if not isinstance(v, dict) or len(v) < 100:
            problems.append("%s 映射条目异常偏少 (%s)" % (key, type(v).__name__))

    if not isinstance(datasets["health"], dict):
        problems.append("health 非对象")

    return problems


def write_atomic(target, document):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(document, ensure_ascii=False, separators=(",", ":"),
                     sort_keys=True).encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=".snapshot-", suffix=".json",
                               dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        # 落盘后再读回解析，确认字节完整（防止半写文件被部署）
        if json.loads(Path(tmp).read_text(encoding="utf-8")) != document:
            raise RuntimeError("镜像落盘回读校验失败")
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return len(raw)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--target", default="_site_release/data/snapshot.json")
    p.add_argument("--dry-run", action="store_true", help="只校验不落盘")
    a = p.parse_args()

    datasets, meta = collect()
    problems = validate(datasets)
    if problems:
        for msg in problems:
            print("FATAL: " + msg, file=sys.stderr)
        print(json.dumps({"ok": False, "problems": problems,
                          "keys_found": sorted(datasets)}, ensure_ascii=False))
        return 1

    document = {
        "schema": 1,
        "generated_at": _utcnow(),
        "source": "postgres-direct",
        "note": "CI 从在线库自动导出的同源只读镜像；主源仍是在线库，非手工维护的第二份数据",
        "datasets": datasets,
        "meta": meta,
    }
    if a.dry_run:
        print(json.dumps({"ok": True, "dry_run": True,
                          "keys": sorted(datasets),
                          "bytes_estimate": len(json.dumps(document, ensure_ascii=False))},
                         ensure_ascii=False))
        return 0

    size = write_atomic(a.target, document)
    print(json.dumps({"ok": True, "target": str(a.target), "bytes": size,
                      "generated_at": document["generated_at"],
                      "keys": sorted(datasets),
                      "oldest": min((m["updated_at"] for m in meta.values()
                                     if m.get("updated_at")), default=""),
                      "newest": max((m["updated_at"] for m in meta.values()
                                     if m.get("updated_at")), default="")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
