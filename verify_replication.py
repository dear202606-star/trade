# -*- coding: utf-8 -*-
"""verify_replication.py — 三库展示数据一致性与端点健康校验（看门狗回执）。

常态（看门狗每小时一次）：只比对 Turso(A) 与 Neon(B) 两个备库的 14 个
展示 key 及 payload 规范哈希，不触碰主库、不产生 Supabase 出口流量。
可选 --with-primary 会用 PostgreSQL 直连从主库重算一份指纹做三方比对
（仅在人工演练时使用，避免给 egress 配额增加固定负担）。

产出 output/replication_check.json：{problems, warnings, ...}
本脚本**永远返回 0**，由 watchdog_site_check.py 汇总后决定退出码，避免
「监控脚本自身失败」被误判成「数据停更」而触发自愈派发。
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import export_site_snapshot as ess  # noqa: E402
import sync_site_dataset as sync  # noqa: E402

RECEIPT = BASE / "output" / "replication_check.json"
REQUIRED = set(ess.ALL_KEYS)


def _utcnow():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def turso_state():
    url, token = sync.turso_credentials()
    rows = sync.turso_execute(url, token, [
        ("select key, payload_sha256, updated_at from site_dataset", [])])[0]
    state = {r[0]: {"sha256": r[1], "updated_at": r[2]}
             for r in rows if not str(r[0]).startswith("__")}
    return state, len(rows)


def neon_state():
    dsn = sync.neon_dsn()
    if not dsn:
        raise RuntimeError("缺少 NEON_DSN")
    import psycopg2
    conn = psycopg2.connect(dsn, connect_timeout=25)
    try:
        with conn.cursor() as cur:
            cur.execute("select key, payload_sha256, updated_at from public.site_dataset_mirror")
            rows = cur.fetchall()
    finally:
        conn.close()
    return ({r[0]: {"sha256": r[1], "updated_at": str(r[2])} for r in rows}, len(rows))


def primary_state():
    datasets, meta = ess.collect()
    problems = ess.validate(datasets)
    if problems:
        raise RuntimeError("主库数据集校验失败：" + "; ".join(problems))
    return {r[0]: r[3] for r in sync.build_rows(datasets, meta)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--with-primary", action="store_true", help="额外直连主库做三方比对")
    ap.add_argument("--json", action="store_true", help="打印完整回执 JSON")
    args = ap.parse_args()

    problems, warnings, states = [], [], {}
    for name, fn in (("turso", turso_state), ("neon", neon_state)):
        try:
            states[name] = fn()
        except Exception as exc:
            problems.append("%s 备库不可达：%s" % (name, str(exc)[:140]))

    if len(states) == 2:
        t, n = states["turso"][0], states["neon"][0]
        missing_t = sorted(REQUIRED - set(t))
        missing_n = sorted(REQUIRED - set(n))
        if missing_t:
            problems.append("Turso 缺失展示 key：%s" % ", ".join(missing_t))
        if missing_n:
            problems.append("Neon 缺失展示 key：%s" % ", ".join(missing_n))
        diff = sorted(k for k in REQUIRED
                      if k in t and k in n and t[k]["sha256"] != n[k]["sha256"])
        if diff:
            problems.append("主备 payload 哈希不一致：%s" % ", ".join(diff))
        extra = sorted((set(t) | set(n)) - REQUIRED)
        if extra:
            warnings.append("备库含非展示 key（忽略）：%s" % ", ".join(extra))

    primary = None
    if args.with_primary:
        try:
            primary = primary_state()
            for name in ("turso", "neon"):
                if name in states:
                    bad = sorted(k for k in REQUIRED
                                 if k in states[name][0] and
                                 states[name][0][k]["sha256"] != primary.get(k))
                    if bad:
                        problems.append("主库与 %s 不一致：%s" % (name, ", ".join(bad)))
        except Exception as exc:
            warnings.append("主库比对跳过：%s" % str(exc)[:140])
    else:
        warnings.append("未启用主库实时比对；常态由 realtime_check.py 直连校验")

    receipt = {
        "checked_at": _utcnow(),
        "required_keys": len(REQUIRED),
        "turso_rows": states.get("turso", ({}, None))[1],
        "neon_rows": states.get("neon", ({}, None))[1],
        "primary_compared": primary is not None,
        "digest": {name: sorted((k, v["sha256"][:12]) for k, v in states[name][0].items())
                   for name in states},
        "problems": problems,
        "warnings": warnings,
    }
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print("REPLICATION PROBLEMS=%d WARNINGS=%d" % (len(problems), len(warnings)))
    for p in problems:
        print(" -", p)
    for w in warnings:
        print(" ~", w)
    if args.json:
        print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
