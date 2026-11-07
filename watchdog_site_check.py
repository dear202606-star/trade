"""watchdog_site_check.py — 校验「页面真正读到的那份数据」是否新鲜完整。

2026-11-07 背景
  看门狗原先检查 Pages 上的 health.json / b/strategy_history.json /
  quality.json（在线单源改造后早已下线，恒 404），以及走 PostgREST 的
  在线视图（项目 egress 超额时恒 402）。结果是数据库与页面都正常时也会
  持续误报，真正的问题反而被噪音淹没。

  现在页面在在线库不可用时读取 CI 直连导出的同源镜像 data/snapshot.json。
  本模块直接校验这份镜像：完整性、生成时效、各层业务时效、健康标记、
  历史年度连续性，以及收益基准与预览的依赖关系。全部只读、无数据库依赖，
  因此网关被限流时看门狗依然能给出准确结论。
"""
import datetime as dt
import json
import urllib.request

from watchdog_checks import history_problems, published_dependency_problems

SITE = "https://dear202606-star.github.io/trade/data/snapshot.json"

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


def load(url=SITE, timeout=25):
    req = urllib.request.Request(url + ("&" if "?" in url else "?") +
                                 "nocache=" + str(int(dt.datetime.now().timestamp())))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def snapshot_problems(doc, now=None):
    """返回问题列表；空列表代表数据链路健康。"""
    now = now or dt.datetime.now(dt.timezone.utc)
    problems = []
    if not isinstance(doc, dict) or doc.get("schema") != 1:
        return ["镜像格式无效（schema 缺失或版本不符）"]
    if doc.get("source") != "postgres-direct":
        problems.append("镜像来源异常：%s" % doc.get("source"))

    datasets = doc.get("datasets") or {}
    missing = sorted(REQUIRED - set(datasets))
    if missing:
        problems.append("镜像缺失数据集：%s" % ", ".join(missing))
    empty = sorted(k for k in REQUIRED if k in datasets and not datasets[k])
    if empty:
        problems.append("镜像数据集为空：%s" % ", ".join(empty))

    age = _age_hours(doc.get("generated_at"), now)
    if age is None:
        problems.append("镜像缺少有效的 generated_at")
    elif age < -1:
        problems.append("镜像生成时间异常（来自未来 %.1f 小时）" % -age)
    elif age > MIRROR_MAX_AGE_H:
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
    problems = []
    try:
        problems = snapshot_problems(load())
    except Exception as exc:
        problems = ["镜像读取失败：%s" % str(exc)[:120]]
    # 实时层（直连）回执与镜像检查合并，避免两处告警口径不一致
    try:
        with open("output/realtime_check.json", encoding="utf-8") as fh:
            problems.extend(json.load(fh).get("problems") or [])
    except Exception as exc:
        problems.append("在线报价监控回执缺失：%s" % str(exc)[:100])
    with open("problems.txt", "w", encoding="utf-8") as fh:
        fh.write("\n".join(problems))
    print("PROBLEMS=%d" % len(problems))
    for p in problems:
        print(" -", p)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
