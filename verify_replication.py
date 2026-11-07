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
# Neon 是「日更持久副本」：db-sync.yml 每工作日收盘后（21:10 UTC）才唤醒一次。
# 盘中 Turso 已追平而 Neon 仍滞后属于设计内状态，不能误报成故障，否则看门狗
# 每次行情更新都会开 Issue 并触发自愈。超过此窗口仍未追平才升级为 problem
# （4 天足以覆盖周末与单日假期）。
NEON_LAG_OK_HOURS = 96


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


def neon_age_hours(state):
    """Neon 镜像最近一次写入距今小时数；无有效时间戳时返回 None。

    注：payload 未变化时 upsert 不会刷新 updated_at，因此这里量到的是
    「最近一次内容变化」。只有在 A/B 出现哈希差异时才用它判断是否超时。
    """
    stamps = []
    for info in state.values():
        raw = str(info.get("updated_at") or "").strip()
        if not raw:
            continue
        try:
            moment = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=dt.timezone.utc)
        stamps.append(moment)
    if not stamps:
        return None
    return (dt.datetime.now(dt.timezone.utc) - max(stamps)).total_seconds() / 3600.0


def classify_diff(diff, age_hours):
    """A/B 哈希差异定性：短期滞后是 warning，长期不追平才是 problem。"""
    if not diff:
        return [], []
    keys = ", ".join(diff)
    if age_hours is None:
        return (["Neon 副本缺少有效写入时间戳且哈希不一致：%s" % keys], [])
    if age_hours > NEON_LAG_OK_HOURS:
        return (["Neon 副本超过 %.0f 小时未追平：%s" % (age_hours, keys)], [])
    return ([], ["Neon 为日更持久副本，按设计允许滞后"
                 "（每工作日 21:10 UTC 由 db-sync.yml 追平）：%s" % keys])


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
    neon_age = None
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
        neon_age = neon_age_hours(n)
        lag_problems, lag_warnings = classify_diff(diff, neon_age)
        problems.extend(lag_problems)
        warnings.extend(lag_warnings)
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
        "neon_last_write_age_hours": round(neon_age, 1) if neon_age is not None else None,
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
