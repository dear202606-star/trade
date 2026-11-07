"""watchdog_site_check.py — 校验「页面真正读到的那份数据」是否新鲜完整。

2026-11-08 改造
  页面不再读取任何本地文件镜像。主库 REST 402 时浏览器自动切换到
  Turso 在线备库（libsql HTTP + 只读 token），故看门狗改为直接读取
  同一份备库数据：14 个展示 key 的完整性、各层业务时效、健康标记、
  历史年度连续性、收益基准与预览依赖，全部只读、不消耗 Supabase
  出口流量；再叠加 verify_replication.py 的三库一致性回执与
  realtime_check.py 的直连回执，形成「数据 + 复制 + 实时」三层监控。
"""
import datetime as dt
import json
import urllib.request

from watchdog_checks import history_problems, published_dependency_problems

SITE_CLOUD = "https://dear202606-star.github.io/trade/data/cloud.json"

REQUIRED = {
    "quotes", "equity", "b/equity", "signal", "b/signal",
    "preview", "b/preview", "curve", "b/curve",
    "strategy_history", "b/strategy_history", "name_zh", "sectors", "health",
}

# 各层的最大可接受陈旧度（小时）。镜像按 DAILY 策略每个交易日刷新 2 次，
# 周末不发布，故 72 小时覆盖「周五盘后 → 周一盘前」的 62 小时间隔；
# 工作日的真实停更由 published_dependency_problems 的 quote_version 检查兜住，
# 因此放宽此阈值不会掩盖停更，只会消除周末误报。
MIRROR_MAX_AGE_H = 72
QUOTE_MAX_AGE_H = 72        # 覆盖周末与节假日
RESULT_MAX_AGE_H = 96       # 净值/预览为日更计算结果


def _parse(ts):
    if not ts:
        return None
    try:
        s = str(ts).strip().replace("Z", "+00:00")
        d = dt.datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d
    except (ValueError, TypeError):
        return None


def _age_hours(ts, now):
    d = _parse(ts)
    return None if d is None else (now - d).total_seconds() / 3600.0


def load(url=None, timeout=25):
    """从公开 cloud.json 取 Turso 只读端点，一次读回全部展示 key。

    与浏览器故障切换时使用的数据源完全一致（同源同 token），因此
    「看门狗说没问题」与「用户实际看到的页面」不会出现两套口径。
    """
    cfg_url = url or SITE_CLOUD
    req = urllib.request.Request(cfg_url + ("&" if "?" in cfg_url else "?") +
                                 "nocache=" + str(int(dt.datetime.now().timestamp())))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        cfg = json.load(resp)
    eps = cfg.get("endpoints") or []
    ep = next((e for e in eps if e.get("type") == "turso" and e.get("url") and e.get("key")), None)
    if not ep:
        raise RuntimeError("公开配置缺少 Turso 备库端点")
    body = {"baton": None, "requests": [
        {"type": "execute", "stmt": {
            "sql": "select key, payload, updated_at from site_dataset", "args": []}},
        {"type": "close"}]}
    req2 = urllib.request.Request(ep["url"] + "/v2/pipeline",
        data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Authorization": "Bearer " + ep["key"],
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req2, timeout=timeout) as resp:
        raw = json.load(resp)
    item = (raw.get("results") or [])[0]
    if not item or item.get("type") != "ok":
        raise RuntimeError("备库查询返回失败")
    rows = ((item.get("response") or {}).get("result") or {}).get("rows") or []
    datasets, meta = {}, {}
    for row in rows:
        key = row[0]["value"]
        if key.startswith("__"):
            continue
        datasets[key] = json.loads(row[1]["value"])
        meta[key] = {"updated_at": row[2]["value"] if len(row) > 2 else ""}
    q = datasets.get("quotes") or {}
    generated = q.get("ts") or max(
        (m["updated_at"] for m in meta.values() if m["updated_at"]), default="")
    return {"schema": 1, "source": "turso-http", "generated_at": generated,
            "datasets": datasets, "meta": meta}


def snapshot_problems(doc, now=None):
    """返回问题列表；空列表代表数据链路健康。"""
    now = now or dt.datetime.now(dt.timezone.utc)
    problems = []
    if not isinstance(doc, dict) or doc.get("schema") != 1:
        return ["展示数据格式无效（schema 缺失或版本不符）"]
    if doc.get("source") not in ("postgres-direct", "turso-http"):
        problems.append("数据来源异常：%s" % doc.get("source"))

    datasets = doc.get("datasets") or {}
    missing = sorted(REQUIRED - set(datasets))
    if missing:
        problems.append("镜像缺失数据集：%s" % ", ".join(missing))
    empty = sorted(k for k in REQUIRED if k in datasets and not datasets[k])
    if empty:
        problems.append("镜像数据集为空：%s" % ", ".join(empty))

    age = _age_hours(doc.get("generated_at"), now)
    # turso-http 的 generated_at 取行情业务时间，下面 QUOTE_MAX_AGE_H 已专门
    # 判定新鲜度；只有旧文件镜像才需要额外的整体生成时效检查，避免重复误报。
    if age is None:
        problems.append("展示数据缺少有效时间戳")
    elif age < -1:
        problems.append("数据时间异常（来自未来 %.1f 小时）" % -age)
    elif doc.get("source") == "postgres-direct" and age > MIRROR_MAX_AGE_H:
        problems.append("镜像停更 %.1f 小时（阈值 %d）" % (age, MIRROR_MAX_AGE_H))

    if not missing:
        # 行情时效
        q = datasets.get("quotes") or {}
        qa = _age_hours(q.get("ts") or q.get("ts_local"), now)
        if qa is None:
            problems.append("行情缺少有效时间戳")
        elif qa > QUOTE_MAX_AGE_H:
            problems.append("行情停更 %.1f 小时（阈值 %d）" % (qa, QUOTE_MAX_AGE_H))
        if not q.get("ok") or not (q.get("quotes") or {}).get("SPY"):
            problems.append("行情快照缺少 SPY 基准")

        # 净值与预览时效（meta 来自库内 source_as_of / created_at）
        meta = doc.get("meta") or {}
        for key in ("equity", "b/equity", "preview", "b/preview"):
            ra = _age_hours((meta.get(key) or {}).get("updated_at"), now)
            if ra is None:
                problems.append("%s 缺少有效更新时间" % key)
            elif ra > RESULT_MAX_AGE_H:
                problems.append("%s 停更 %.1f 小时（阈值 %d）" % (key, ra, RESULT_MAX_AGE_H))

        # 结构级门禁，与导出脚本保持同一套口径
        for key in ("equity", "b/equity"):
            eq = datasets.get(key) or {}
            if not (eq.get("position") or []):
                problems.append("%s 持仓为空" % key)
            st = eq.get("stats") or {}
            if not isinstance(st.get("strategy"), (int, float)):
                problems.append("%s 缺少策略净值" % key)
        for key in ("signal", "b/signal"):
            if not ((datasets.get(key) or {}).get("official") or []):
                problems.append("%s 缺少官方持仓" % key)

        # 健康标记与历史连续性（沿用既有校验器）
        h = datasets.get("health") or {}
        issues = h.get("issues")
        if not isinstance(issues, list) or any(not isinstance(x, str) for x in issues):
            problems.append("health.issues 无效")
        else:
            problems.extend(x for x in issues if not x.startswith("行情"))
        if h.get("status") not in ("ok", "degraded"):
            problems.append("health.status=%s" % h.get("status"))
        try:
            problems.extend(history_problems(datasets["b/strategy_history"], now))
        except Exception as exc:
            problems.append("历史年度校验失败：%s" % str(exc)[:100])
        try:
            for prefix in ("", "b/"):
                problems.extend(prefix + p for p in published_dependency_problems(
                    datasets[prefix + "equity"], datasets[prefix + "preview"], q))
        except Exception as exc:
            problems.append("收益/预览依赖校验失败：%s" % str(exc)[:100])
    return problems


def main():
    problems, warnings = [], []
    # ① 页面实际读到的备库数据
    try:
        problems = snapshot_problems(load())
    except Exception as exc:
        # 主库正常时页面仍可服务，备库短暂不可达只记 warning；
        # 真正的「备库不可达」由 ② 三库一致性回执判为 problem。
        warnings.append("备库读取失败（页面仍可由主库服务）：%s" % str(exc)[:120])
    # ② 三库一致性 / 端点健康（verify_replication.py 的回执）
    try:
        with open("output/replication_check.json", encoding="utf-8") as fh:
            rep=json.load(fh)
        problems.extend(rep.get("problems") or [])
        warnings.extend(rep.get("warnings") or [])
    except Exception as exc:
        warnings.append("三库一致性回执缺失：%s" % str(exc)[:100])
    # ③ 实时层（直连）回执，避免两处告警口径不一致
    try:
        with open("output/realtime_check.json", encoding="utf-8") as fh:
            receipt=json.load(fh)
        problems.extend(receipt.get("problems") or [])
        warnings.extend(receipt.get("warnings") or [])
    except Exception as exc:
        problems.append("在线报价监控回执缺失：%s" % str(exc)[:100])
    with open("problems.txt", "w", encoding="utf-8") as fh:
        fh.write("\n".join(problems))
    print("PROBLEMS=%d WARNINGS=%d" % (len(problems),len(warnings)))
    for p in problems:
        print(" -", p)
    for w in warnings:
        print(" ~", w)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
