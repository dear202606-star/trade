#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""external_heartbeat.py — 平台外心跳安装器（Supabase pg_cron + pg_net）。

为什么需要（2026-11-10 复盘）
  GitHub Actions 免费版 schedule 是尽力而为，且看门狗 / 自愈本身也挂在
  schedule 上，构成共模故障。reconcile.yml 已把判定改成「数据齐不齐」，
  但所有触发源仍在 GitHub 平台内。本脚本在**数据库侧**装一个独立时钟：
  pg_cron 每 N 分钟用 pg_net 向 GitHub 发一次 repository_dispatch，
  唤醒只读场次对账。GitHub 整体故障期间请求会失败，但恢复后最多 N 分钟
  必然有下一拍到达，缺口随即被补齐 —— 不依赖 GitHub 调度器自愈。

安全与额度（勿回退）
  1. 只唤起「场次对账」，绝不派发采集 / 净值 / 回填；数据已齐时对账零动作。
  2. 低频（默认 20 分钟），单次出站约 300 字节。不同于 11-06 停掉的
     trade-dispatch / trade-dispatch-market（每 5 分钟拉整条采集链，
     直接造成 1139 次/10 天、egress 399% 超额）。
  3. PAT 只从 Supabase Vault 读取，SQL 里保存的是「子查询引用」，
     明文不进仓库、不进日志、不进 cron.job.command。
  4. 幂等：enable 可反复执行；disable 只摘自己的作业，不碰其它 cron.job。

用法（CI 内，需要 GitHub Secret SUPABASE_DB_PASSWORD）
  python external_heartbeat.py --inspect
  python external_heartbeat.py --enable --tick-minutes 20
  python external_heartbeat.py --disable
  python external_heartbeat.py --verify
  python external_heartbeat.py --self-test          # 离线，不连库
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
REF = "fnvahsyrzmmxwovbsqpc"
HOST = "aws-0-ap-northeast-1.pooler.supabase.com"
REPO = "dear202606-star/trade"
JOB = "trade-reconcile-tick"
EVENT_TYPE = "reconcile-tick"
VAULT_SECRET = "gh_dispatch_token"
DEFAULT_TICK_MIN = 20
MIN_TICK_MIN, MAX_TICK_MIN = 5, 60
# 只对名字像 GitHub 凭据的 Vault 项做端点探测，避免拿数据库口令去打 API。
TOKEN_NAME_HINTS = ("gh", "git", "pat", "dispatch")

ENDPOINTS = {
    "repository_dispatch": "https://api.github.com/repos/%s/dispatches" % REPO,
    "workflow_dispatch": "https://api.github.com/repos/%s/actions/workflows/reconcile.yml/dispatches" % REPO,
}
BODIES = {
    "repository_dispatch": {"event_type": EVENT_TYPE,
                            "client_payload": {"source": "pg-cron"}},
    "workflow_dispatch": {"ref": "main", "inputs": {"source": "pg-cron"}},
}


# ---------------------------------------------------------------- 纯函数层

def choose_mode(probes):
    """按探测结果挑可用的 GitHub 端点；repository_dispatch 优先（语义更通用）。"""
    for mode in ("repository_dispatch", "workflow_dispatch"):
        code = probes.get(mode)
        if isinstance(code, int) and 200 <= code < 300:
            return mode
    return None


def cron_expression(tick_minutes):
    """心跳间隔 → pg_cron 表达式。只接受 5..60，防止误设成每 5 分钟的高频作业。"""
    n = int(tick_minutes)
    if not MIN_TICK_MIN <= n <= MAX_TICK_MIN:
        raise ValueError("心跳间隔必须在 %d..%d 分钟之间，拒绝 %r（避免 egress 事故重演）"
                         % (MIN_TICK_MIN, MAX_TICK_MIN, tick_minutes))
    return "*/%d * * * *" % n


def _sq(value):
    """SQL 字符串字面量转义。"""
    return "'" + str(value).replace("'", "''") + "'"


def build_command(mode):
    """生成 pg_cron 的 command；只引用 Vault，绝不含明文 token。"""
    if mode not in ENDPOINTS:
        raise ValueError("未知心跳模式: %r" % mode)
    return (
        "$$select net.http_post(\n"
        "  url := %s,\n"
        "  headers := jsonb_build_object(\n"
        "    'Accept', 'application/vnd.github+json',\n"
        "    'X-GitHub-Api-Version', '2022-11-28',\n"
        "    'Content-Type', 'application/json',\n"
        "    'Authorization', 'Bearer ' || (select decrypted_secret\n"
        "       from vault.decrypted_secrets where name = %s limit 1)),\n"
        "  body := %s::jsonb,\n"
        "  timeout_milliseconds := 10000\n"
        ")$$" % (_sq(ENDPOINTS[mode]), _sq(VAULT_SECRET),
                 _sq(json.dumps(BODIES[mode], separators=(",", ":"))))
    )


def build_enable_sql(mode, tick_minutes):
    """幂等安装：先摘同名旧作业，再按当前配置重建。"""
    return (
        "do $$\n"
        "begin\n"
        "  if exists (select 1 from cron.job where jobname = %s) then\n"
        "    perform cron.unschedule(%s);\n"
        "  end if;\n"
        "end $$;\n"
        "select cron.schedule(%s, %s, %s);\n"
        % (_sq(JOB), _sq(JOB), _sq(JOB),
           _sq(cron_expression(tick_minutes)), build_command(mode))
    )


def build_disable_sql():
    return ("do $$\nbegin\n"
            "  if exists (select 1 from cron.job where jobname = %s) then\n"
            "    perform cron.unschedule(%s);\n"
            "  end if;\nend $$;\n" % (_sq(JOB), _sq(JOB)))


# ---------------------------------------------------------------- 数据库层

def db_password():
    pw = os.environ.get("SUPABASE_DB_PASSWORD", "").strip()
    if not pw:
        path = BASE / "output" / "_supabase_dbpass.txt"
        if path.exists():
            pw = path.read_text(encoding="utf-8").strip()
    if not pw:
        raise RuntimeError("缺少 SUPABASE_DB_PASSWORD（CI 由 Secret 注入）")
    return pw


def connect():
    import psycopg2  # 延迟导入：reconcile.yml 的纯标准库自检也能 import 本模块
    return psycopg2.connect(host=HOST, port=6543, user="postgres." + REF,
                            password=db_password(), dbname="postgres",
                            connect_timeout=25, sslmode="require")


def _rows(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def probe_endpoint(mode, token):
    """用候选 PAT 打一次真实 dispatch，返回 HTTP 状态（凭据绝不打印）。"""
    req = urllib.request.Request(
        ENDPOINTS[mode], data=json.dumps(BODIES[mode]).encode("utf-8"), method="POST",
        headers={"Accept": "application/vnd.github+json",
                 "Authorization": "Bearer " + token,
                 "Content-Type": "application/json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "trade-external-heartbeat"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception as exc:                       # noqa: BLE001 - 只记类型，不含凭据
        return "error:" + type(exc).__name__


def inspect(conn, probe=True):
    """只读探查 + 可选端点探测；输出 JSON 不含任何凭据明文。"""
    out = {"checked_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()}
    try:
        out["extensions"] = sorted(r[0] for r in _rows(
            conn, "select extname from pg_extension "
                  "where extname in ('pg_cron','pg_net','supabase_vault')"))
    except Exception as exc:                      # noqa: BLE001
        out["extensions_error"] = type(exc).__name__
    try:
        out["cron_jobs"] = [{"name": r[0], "schedule": r[1], "active": r[2]}
                            for r in _rows(conn, "select jobname, schedule, active "
                                                "from cron.job order by jobid")]
    except Exception as exc:                      # noqa: BLE001
        out["cron_jobs_error"] = type(exc).__name__

    secrets, tokens = [], {}
    try:
        names = [r[0] for r in _rows(conn, "select name from vault.decrypted_secrets")]
        secrets = sorted(names)
        for name in names:
            if any(h in str(name).lower() for h in TOKEN_NAME_HINTS):
                rows = _rows(conn, "select decrypted_secret from vault.decrypted_secrets "
                                   "where name = %s limit 1", (name,))
                if rows and rows[0][0]:
                    tokens[name] = rows[0][0]
    except Exception as exc:                      # noqa: BLE001
        out["vault_error"] = type(exc).__name__
    out["vault_secret_names"] = secrets
    out["token_candidates"] = sorted(tokens)

    probes, token_by_mode = {}, {}
    if probe and tokens:
        # 优先试历史专用凭据，再退到其它名字像 PAT 的项。
        ordered = ([VAULT_SECRET] if VAULT_SECRET in tokens else []) + \
                  [n for n in sorted(tokens) if n != VAULT_SECRET]
        for mode in ("repository_dispatch", "workflow_dispatch"):
            for name in ordered:
                code = probe_endpoint(mode, tokens[name])
                probes[mode] = code
                if isinstance(code, int) and 200 <= code < 300:
                    token_by_mode[mode] = name
                    break
                # 401/403 是凭据权限问题，换下一个候选；5xx/网络问题不换。
                if not (isinstance(code, int) and 400 <= code < 500):
                    break
    out["probes"] = probes
    out["token_by_mode"] = token_by_mode
    out["recommended_mode"] = choose_mode(probes)
    return out


def enable(conn, tick_minutes, mode=None, probe=True):
    info = inspect(conn, probe=probe)
    mode = mode or info.get("recommended_mode")
    if not mode:
        raise RuntimeError("Vault 中没有可唤起 GitHub 的有效 PAT；"
                           "探测结果=%s；请轮换 gh_dispatch_token" % info.get("probes"))
    if VAULT_SECRET not in (info.get("vault_secret_names") or []):
        # SQL 里写死引用 gh_dispatch_token；若用的是备用项，改成同名引用会拿不到。
        raise RuntimeError("缺少 Vault 项 %s，拒绝安装（SQL 只引用该名称）" % VAULT_SECRET)
    sql = build_enable_sql(mode, tick_minutes)
    with conn.cursor() as cur:
        cur.execute(sql)
        jobid = cur.fetchone()[0]
    conn.commit()
    return {"enabled": True, "jobid": jobid, "mode": mode,
            "schedule": cron_expression(tick_minutes), "job": JOB,
            "vault_secret": VAULT_SECRET, "probes": info.get("probes")}


def disable(conn):
    with conn.cursor() as cur:
        cur.execute(build_disable_sql())
    conn.commit()
    return {"enabled": False, "job": JOB}


def is_tick_run(run):
    """识别「平台外心跳」运行，不把手动 workflow_dispatch 误算成心跳。

    reconcile.yml 的 run-name 带上 source=pg-cron；老数据没有 displayTitle
    时退回按事件类型判断（repository_dispatch 只可能来自平台外）。
    """
    if "displayTitle" in (run or {}):
        return "pg-cron" in str(run.get("displayTitle") or "")
    return (run or {}).get("event") in ("repository_dispatch", "workflow_dispatch")


def health(runs, now, max_age_min=90):
    """纯函数：判断平台外心跳是否仍在到达，供 CI 输出告警（绝不阻断主流程）。"""
    now = now if isinstance(now, dt.datetime) else dt.datetime.fromisoformat(str(now).replace("Z", "+00:00"))
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    ticks = []
    for r in runs or []:
        if not is_tick_run(r):
            continue
        try:
            at = dt.datetime.fromisoformat(str(r["createdAt"]).replace("Z", "+00:00"))
        except (KeyError, ValueError, TypeError):
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=dt.timezone.utc)
        ticks.append(at)
    if not ticks:
        return {"healthy": False, "age_min": None, "reason": "未发现任何外部心跳到达记录"}
    age_min = int((now - max(ticks)).total_seconds() / 60)
    return {"healthy": age_min <= max_age_min, "age_min": age_min,
            "reason": "最近一次心跳 %d 分钟前" % age_min}


def github_health():
    """不连库：由 reconcile.yml 在每次对账时顺带检查心跳存活。"""
    import subprocess
    try:
        raw = subprocess.run(
            ["gh", "run", "list", "--workflow", "reconcile.yml", "--limit", "60",
             "--json", "event,createdAt,displayTitle"],
            check=False, capture_output=True, text=True, timeout=40).stdout.strip()
        runs = json.loads(raw or "[]")
    except Exception:                             # noqa: BLE001
        runs = None
    result = health(runs, dt.datetime.now(dt.timezone.utc))
    if not result["healthy"]:
        print("::warning::平台外心跳疑似失联（%s）；请运行 heartbeat-setup.yml 的 verify"
              % result["reason"])
    return result


def verify(conn):
    out = {"checked_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()}
    try:
        out["jobs"] = [{"name": r[0], "schedule": r[1], "active": r[2]}
                       for r in _rows(conn, "select jobname, schedule, active "
                                           "from cron.job where jobname = %s", (JOB,))]
    except Exception as exc:                      # noqa: BLE001
        out["jobs_error"] = type(exc).__name__
    try:
        out["recent_http"] = [{"status": r[0], "at": str(r[1])}
                              for r in _rows(conn,
                                             "select status_code, created from net._http_response "
                                             "order by id desc limit 8")]
    except Exception as exc:                      # noqa: BLE001
        out["recent_http_error"] = type(exc).__name__
    # GitHub 侧回执：最近一次由外部心跳唤起的对账运行
    import subprocess
    try:
        raw = subprocess.run(
            ["gh", "run", "list", "--workflow", "reconcile.yml", "--limit", "50",
             "--json", "databaseId,event,createdAt,displayTitle,conclusion"],
            check=False, capture_output=True, text=True, timeout=40).stdout.strip()
        runs = json.loads(raw or "[]")
        ticks = [r for r in runs if is_tick_run(r)]
        out["last_tick_run"] = ticks[0] if ticks else None
        out["tick_run_count"] = len(ticks)
    except Exception as exc:                      # noqa: BLE001
        out["github_error"] = type(exc).__name__
    return out


# ---------------------------------------------------------------- CLI

def self_test():
    cases = []

    def check(label, cond):
        cases.append((label, bool(cond)))

    check("默认间隔在安全区间", MIN_TICK_MIN <= DEFAULT_TICK_MIN <= MAX_TICK_MIN)
    check("20 分钟表达式", cron_expression(20) == "*/20 * * * *")
    check("5 分钟边界允许", cron_expression(5) == "*/5 * * * *")
    try:
        cron_expression(1)
        check("拒绝 1 分钟高频", False)
    except ValueError:
        check("拒绝 1 分钟高频", True)
    try:
        cron_expression(61)
        check("拒绝 61 分钟", False)
    except ValueError:
        check("拒绝 61 分钟", True)

    check("优先 repository_dispatch",
          choose_mode({"repository_dispatch": 204, "workflow_dispatch": 204})
          == "repository_dispatch")
    check("repository 403 时降级 workflow",
          choose_mode({"repository_dispatch": 403, "workflow_dispatch": 204})
          == "workflow_dispatch")
    check("全部失败则拒绝安装",
          choose_mode({"repository_dispatch": 401, "workflow_dispatch": 403}) is None)
    check("网络错误不算成功",
          choose_mode({"repository_dispatch": "error:URLError"}) is None)

    for mode in ENDPOINTS:
        cmd = build_command(mode)
        check("%s 引用 Vault 而非明文" % mode,
              "vault.decrypted_secrets" in cmd and VAULT_SECRET in cmd)
        check("%s 不带 Bearer 明文" % mode, "Bearer ' ||" in cmd)
        check("%s 指向对账端点" % mode, ENDPOINTS[mode] in cmd)
    sql = build_enable_sql("repository_dispatch", 20)
    check("安装 SQL 幂等", "cron.unschedule" in sql and "cron.schedule" in sql)
    check("安装 SQL 不含采集工作流", "quotes.yml" not in sql)
    check("安装 SQL 频率正确", "'*/20 * * * *'" in sql)
    check("停用 SQL 只摘自己", JOB in build_disable_sql() and "trade-dispatch" not in build_disable_sql())

    now = dt.datetime.fromisoformat("2026-11-10T16:00:00+00:00")
    fresh = [{"event": "repository_dispatch", "createdAt": "2026-11-10T15:40:00Z"},
             {"event": "schedule", "createdAt": "2026-11-10T15:47:00Z"}]
    check("心跳新鲜判健康", health(fresh, now)["healthy"])
    check("心跳年龄正确", health(fresh, now)["age_min"] == 20)
    check("心跳超时判失联", not health(
        [{"event": "workflow_dispatch", "createdAt": "2026-11-10T14:00:00Z"}], now)["healthy"])
    check("无心跳记录判失联", not health([], now)["healthy"])
    check("查不到运行历史也只告警", not health(None, now)["healthy"])
    check("忽略无关事件", not health(
        [{"event": "schedule", "createdAt": "2026-11-10T15:59:00Z"}], now)["healthy"])
    check("标题含 pg-cron 才算心跳", is_tick_run({"displayTitle": "场次对账 · pg-cron",
                                        "event": "workflow_dispatch"}))
    check("手动派发不算心跳", not is_tick_run({"displayTitle": "场次对账 · manual",
                                      "event": "workflow_dispatch"}))
    check("无标题时按事件兜底", is_tick_run({"event": "repository_dispatch"}))
    check("schedule 运行不算心跳", not is_tick_run({"displayTitle": "场次对账 · schedule",
                                          "event": "schedule"}))
    check("手动运行不会掩盖心跳失联", not health(
        [{"displayTitle": "场次对账 · manual", "event": "workflow_dispatch",
          "createdAt": "2026-11-10T15:59:00Z"}], now)["healthy"])

    failed = [label for label, ok in cases if not ok]
    print(json.dumps({"self_test": "FAIL" if failed else "OK",
                      "cases": len(cases), "failed": failed}, ensure_ascii=False))
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inspect", action="store_true", help="只读探查（默认动作）")
    ap.add_argument("--enable", action="store_true", help="幂等安装 / 重建心跳作业")
    ap.add_argument("--disable", action="store_true", help="摘除心跳作业")
    ap.add_argument("--verify", action="store_true", help="查看作业、HTTP 回执与 GitHub 运行")
    ap.add_argument("--health", action="store_true", help="不连库，只检查心跳是否仍在到达（CI 告警）")
    ap.add_argument("--tick-minutes", type=int, default=DEFAULT_TICK_MIN)
    ap.add_argument("--mode", choices=sorted(ENDPOINTS), help="强制指定端点（一般自动选择）")
    ap.add_argument("--no-probe", action="store_true", help="enable 时跳过端点实测")
    ap.add_argument("--self-test", action="store_true", help="离线纯函数自测")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if args.health:
        print(json.dumps(github_health(), ensure_ascii=False))
        return 0

    action = ("enable" if args.enable else
              "disable" if args.disable else
              "verify" if args.verify else "inspect")
    conn = connect()
    try:
        if action == "inspect":
            result = inspect(conn, probe=not args.no_probe)
        elif action == "enable":
            result = enable(conn, args.tick_minutes, args.mode, probe=not args.no_probe)
        elif action == "disable":
            result = disable(conn)
        else:
            result = verify(conn)
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
