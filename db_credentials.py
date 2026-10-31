# -*- coding: utf-8 -*-
"""db_credentials.py — 把数据源凭据写入 Supabase 的 data_sources 表

安全设计
  · 表启用 RLS，且不建任何 policy → anon/authenticated 完全读不到
  · 只有 postgres（service）连接能读写，我们正是用它
  · 库里只存「凭据引用 + 掩码指纹」，明文同时写本地 gitignored 文件，两处互为备份
"""
import hashlib
import io
import os
import sys

import psycopg2

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
REF = "fnvahsyrzmmxwovbsqpc"
HOST = "aws-0-ap-northeast-1.pooler.supabase.com"

DDL = """
create table if not exists data_sources (
  id          serial primary key,
  source_id   text not null,              -- alphavantage | eodhd | kaggle ...
  display    text,
  capability text,                        -- history | recent_check | bulk | membership
  credential jsonb,                       -- {key|token, account, plan, quota}
  fingerprint text,                        -- 凭据 sha256 前 12 位（可核对，不可反推）
  status      text default 'active',      -- active | expired | revoked
  added_at    timestamptz default now(),
  note        text,
  unique (source_id)
);
alter table data_sources enable row level security;
-- 故意不创建任何 policy：只有 service/postgres 能访问
"""

META = {
    "alphavantage": {"display": "Alpha Vantage", "capability": "history(total_return)",
                     "plan": "free 25/day", "quota": 25},
    "eodhd": {"display": "EODHD", "capability": "recent_check(13m) + paid bulk",
              "plan": "free 20/day", "quota": 20},
    "kaggle": {"display": "Kaggle", "capability": "bulk dataset download",
               "plan": "free", "quota": None},
}


def fp(v):
    return hashlib.sha256(v.encode()).hexdigest()[:12]


def db():
    pw = io.open(os.path.join(BASE, "output", "_supabase_dbpass.txt"), encoding="utf-8").read().strip()
    return psycopg2.connect(host=HOST, port=6543, user="postgres." + REF, password=pw,
                            dbname="postgres", connect_timeout=20, sslmode="require")


def main():
    # 凭据来源：本地 gitignored 文件（唯一明文来源）
    kv = {}
    p = os.path.join(BASE, "output", "_api_keys.local.txt")
    for line in io.open(p, encoding="utf-8"):
        if "=" in line and not line.startswith("#"):
            a, b = line.strip().split("=", 1)
            kv[a] = b
    # Kaggle token 由用户提供（截图），写入本地文件后统一入库
    kaggle = os.environ.get("KAGGLE_API_TOKEN", "").strip()
    if kaggle:
        kv["kaggle_token"] = kaggle
        if "kaggle_token=" not in io.open(p, encoding="utf-8").read():
            io.open(p, "a", encoding="utf-8").write("kaggle_token=%s\n" % kaggle)

    rows = [
        ("alphavantage", {"key": kv.get("alphavantage_key", ""),
                          "account": kv.get("email", ""), "plan": "free"}),
        ("eodhd", {"token": kv.get("eodhd_token", ""),
                   "account": kv.get("email", ""), "plan": "free"}),
        ("kaggle", {"token": kv.get("kaggle_token", ""),
                    "account": kv.get("email", ""), "plan": "free"}),
    ]

    with db() as c, c.cursor() as cur:
        cur.execute(DDL)
        for sid, cred in rows:
            secret = cred.get("key") or cred.get("token") or ""
            if not secret:
                print("  跳过 %s（无凭据）" % sid)
                continue
            m = META.get(sid, {})
            cur.execute("""insert into data_sources(source_id, display, capability, credential,
                                                   fingerprint, note)
                           values (%s,%s,%s,%s,%s,%s)
                           on conflict (source_id) do update
                           set credential = excluded.credential,
                               fingerprint = excluded.fingerprint,
                               status = 'active', added_at = now()""",
                        (sid, m.get("display"), m.get("capability"),
                         psycopg2.extras.Json(cred) if hasattr(psycopg2, "extras") else None,
                         fp(secret),
                         "%s ｜ %s" % (m.get("plan"), "额度 %s" % m.get("quota") if m.get("quota") else "无限制")))
            print("  ✅ %-14s 指纹 %s ｜ %s" % (sid, fp(secret), m.get("plan")))
        cur.execute("select source_id, display, capability, fingerprint, status from data_sources order by id")
        print("\n库里凭据表：")
        for r in cur.fetchall():
            print("  %-14s %-14s %-32s %s %s" % r)
    print("\n已启用 RLS，anon 无法读取该表")
    return 0


if __name__ == "__main__":
    import psycopg2.extras  # noqa
    sys.exit(main())
