# -*- coding: utf-8 -*-
"""site_build.py — 构建整站到 docs/

流程：
  1. 复制 site_src/*（页面、样式、脚本）→ docs/
  2. 写数据文件 docs/data/*.json（构建中间产物）
  3. 生成 docs/signals.html（信号看板）

2026-11-06：数据文件不再发布到 Pages，由 publish_site_dataset.py
写入在线库后从产物中剔除；页面统一连接在线库展示。
"""
import json
import os
import re
import shutil
from datetime import datetime as dt, timezone

import dashboard

BASE = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(BASE, "output", "signal_history.json")
QUAL = os.path.join(BASE, "output", "data_quality.json")
SRC_SITE = os.path.join(BASE, "site_src")
DOCS = os.path.join(BASE, "docs")
DATA = os.path.join(DOCS, "data")

def copy_site():
    if not os.path.isdir(SRC_SITE):
        return 0
    n = 0
    for root, _dirs, files in os.walk(SRC_SITE):
        rel = os.path.relpath(root, SRC_SITE)
        dst = DOCS if rel == "." else os.path.join(DOCS, rel)
        os.makedirs(dst, exist_ok=True)
        for fn in files:
            shutil.copy2(os.path.join(root, fn), os.path.join(dst, fn))
            n += 1
    return n


def asset_version():
    """用 css/js 内容哈希做版本号，避免部署后浏览器还在用旧配色/旧脚本"""
    import hashlib
    h = hashlib.md5()
    # 2026-11-05：只对页面真正引用的资源做哈希。此前 app.css / page.css /
    # theme.css 无任何页面引用，改动这些死文件会触发全站缓存失效。
    for f in ("assets/dashboard.css", "assets/app.js", "assets/page.js"):
        p = os.path.join(SRC_SITE, f)
        if os.path.exists(p):
            with open(p, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()[:8]


def bust_cache():
    """给所有页面的 css/js 引用加上 ?v=哈希"""
    v = asset_version()
    n = 0
    for fn in os.listdir(DOCS):
        if not fn.endswith(".html"):
            continue
        p = os.path.join(DOCS, fn)
        try:
            with open(p, encoding="utf-8") as f:
                html = f.read()
        except Exception:
            continue
        new = html
        for a in ("assets/dashboard.css", "assets/app.js", "assets/page.js"):
            new = re.sub(re.escape(a) + r"(\?v=[0-9a-f]+)?", a + "?v=" + v, new)
        if new != html:
            with open(p, "w", encoding="utf-8") as f:
                f.write(new)
            n += 1
    print(f"  静态资源版本：{v}（{n} 个页面已更新引用）")
    return v



# 2026-11-05：导航条原为旧深色主题（#0b0f17 + 金色），与当前暖白主站
# 视觉完全断裂；同时 active 高亮替换的目标字符串并不存在，从未生效。
NAV_BAR = """<div style="position:sticky;top:0;z-index:50;background:rgba(248,251,249,.94);border-bottom:1px solid #e3ebe6;backdrop-filter:blur(18px);">
<div style="max-width:1280px;margin:0 auto;padding:0 32px;display:flex;align-items:center;gap:4px;height:56px;
font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;">
<a href="index.html" style="font-weight:850;color:#193c36;text-decoration:none;margin-right:18px;font-size:15px;letter-spacing:-.4px;">LEADER<span style="color:#00977b;">·</span>QUANT</a>
<a class="nav-link" href="index.html" style="padding:6px 12px;border-radius:9px;color:#62766b;text-decoration:none;font-size:13px;">总览</a>
<a class="nav-link" href="portfolio.html" style="padding:6px 12px;border-radius:9px;color:#62766b;text-decoration:none;font-size:13px;">持仓</a>
<a class="nav-link" href="strategy.html" style="padding:6px 12px;border-radius:9px;color:#62766b;text-decoration:none;font-size:13px;">策略</a>
</div></div>"""


def inject_nav(path, active=""):
    try:
        with open(path, encoding="utf-8") as f:
            html = f.read()
        if "LEADER" in html.split("<body")[0]:
            return
        i = html.find("<body")
        j = html.find(">", i) + 1
        with open(path, "w", encoding="utf-8") as f:
            nav = NAV_BAR
            if active:
                # 高亮当前页：用主站的浅绿选中态，替换目标与 NAV_BAR 保持一致。
                nav = nav.replace('class="nav-link" href="%s"' % active,
                                  'class="nav-link" aria-current="page" href="%s" data-nav-active="1"' % active)
                nav = nav.replace('data-nav-active="1" style="padding:6px 12px;border-radius:9px;color:#62766b;',
                                  'data-nav-active="1" style="padding:6px 12px;border-radius:9px;background:#e5f5e8;color:#225b43;font-weight:650;')
            f.write(html[:j] + nav + html[j:])
    except Exception as e:
        print("WARN: 导航注入失败", e)


def with_validity(rec):
    """给信号补上「有效期」字段：正式持仓按年度锁定，当月排名只作参考"""
    if not isinstance(rec, dict):
        return rec
    om = str(rec.get("official_month") or rec.get("month") or "")
    if len(om) >= 4 and om[:4].isdigit():
        y = int(om[:4]) + 1
        rec.setdefault("official_year", y)
        rec.setdefault("valid_from", "%d-01-01" % y)
        rec.setdefault("valid_to", "%d-12-31" % y)
        rec.setdefault("rebalance_hint", "%d-12 最后一个交易日" % y)
    rec.setdefault("ranking_month", rec.get("month"))
    return rec


def main():
    hist = []
    if os.path.exists(HIST):
        with open(HIST, encoding="utf-8") as f:
            hist = json.load(f)

    os.makedirs(DATA, exist_ok=True)
    n = copy_site()

    # 云同步配置：只发布随源码入库的 publishable key。
    # 2026-11-05：原逻辑在 site_src/cloud.json 缺失时回退读取本地私密的
    # output/_supabase.json，且不区分 publishable / service key，存在把高权限
    # 凭据写进公开 docs/data/cloud.json 的风险。此处改为单一来源 + 类型校验。
    cfgp = os.path.join(BASE, "site_src", "cloud.json")
    if os.path.exists(cfgp):
        try:
            cfg = json.load(open(cfgp, encoding="utf-8"))
            key = cfg.get("key") or cfg.get("publishable_key") or ""
            if not str(key).startswith("sb_publishable_") or "service_role" in str(key):
                print("FATAL: cloud.json 不是 publishable key，拒绝写入公开站点")
                raise SystemExit(1)
            if cfg.get("url"):
                with open(os.path.join(DATA, "cloud.json"), "w", encoding="utf-8") as f:
                    json.dump({"url": cfg["url"], "key": key}, f, ensure_ascii=False)
            else:
                print("WARN: cloud.json 缺少 url")
        except SystemExit:
            raise
        except Exception as e:
            print("WARN: 云同步配置写入失败", e)

    if hist:
        with open(os.path.join(DATA, "signal.json"), "w", encoding="utf-8") as f:
            json.dump(with_validity(hist[0]), f, ensure_ascii=False)
    # 中文名映射（行情源缺中文名时使用）
    try:
        import shutil as _sh
        _sh.copy(os.path.join(BASE, "static", "name_zh.json"),
                 os.path.join(DATA, "name_zh.json"))
    except Exception as _e:
        print("WARN 中文名映射复制失败：%s" % _e)
    # ---- 健康状况（供页面显示红条，避免"停更却无人知"）----
    try:
        hq = json.load(open(os.path.join(DOCS, "data", "quotes.json"), encoding="utf-8"))
        he = json.load(open(os.path.join(DOCS, "data", "equity.json"), encoding="utf-8"))
        hs = json.load(open(os.path.join(DOCS, "data", "signal.json"), encoding="utf-8"))
        now = dt.now(timezone.utc)
        issues = []
        if not hq.get("ok", False):
            issues.append("行情覆盖率不足 %.0f%%" % (hq.get("coverage", 0) * 100))
        age_h = None
        try:
            ts = dt.strptime(hq["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            age_h = round((now - ts).total_seconds() / 3600, 1)
            if age_h > 26:
                issues.append("行情 %.0f 小时未更新" % age_h)
        except Exception:
            issues.append("行情时间戳异常")
        if he.get("last_session") and he.get("points") and he["points"][-1]["d"] != he["last_session"]:
            issues.append("净值末点与已收盘日不一致")
        if not hs.get("official"):
            issues.append("缺少年度持仓")
        health = {
            "status": "ok" if not issues else "degraded",
            "issues": issues,
            "quotes_ts": hq.get("ts"),
            "quotes_age_hours": age_h,
            "session": hq.get("session"),
            "coverage": hq.get("coverage"),
            "last_session": he.get("last_session"),
            "equity_updated": he.get("updated"),
            "signal_month": hs.get("month"),
            "checked_at": dt.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        with open(os.path.join(DOCS, "data", "health.json"), "w", encoding="utf-8") as f:
            json.dump(health, f, ensure_ascii=False)
        print("   健康状态：%s%s" % (health["status"], ("｜" + "；".join(issues)) if issues else ""))
    except Exception as e:
        print("WARN 健康状态生成失败：%s" % e)

    # 以下产物与健康状态无关，必须无条件产出。
    # 2026-11-05：原先这两段被缩进进 except 块，导致正常构建缺
    # docs/data/history.json 与 docs/signals.html（仅在健康状态生成失败时才写）。
    with open(os.path.join(DATA, "history.json"), "w", encoding="utf-8") as f:
        json.dump([with_validity(r) for r in hist], f, ensure_ascii=False)

    # 净值为 equity.py 直接写入 docs/data/，这里无需复制
    # 数据质量明细（output/data_quality.json）仅供内部核查，不再发布到站点
    dashboard.build(hist, os.path.join(DOCS, "signals.html"))
    inject_nav(os.path.join(DOCS, "signals.html"), "signals.html")

    for fn in ("策略模型研究与行动步骤.pdf", "策略模型研究与行动步骤.xlsx"):
        s = os.path.join(BASE, "output", fn)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(DOCS, fn))

    # 兜底：本次数据不完整时，用上次完整结果补位
    for fn in ("strategy_history.json", "curve.json"):
        dst = os.path.join(DATA, fn)
        src = os.path.join(BASE, "output", fn)
        if not os.path.exists(dst) and os.path.exists(src):
            os.makedirs(DATA, exist_ok=True)
            shutil.copy2(src, dst)
            print("  沿用上次完整结果：" + fn)

    # 冻结的选股快照（供线上校验「分年持仓是否被改动」）
    pk = os.path.join(BASE, "output", "backtest_picks.json")
    if os.path.exists(pk):
        shutil.copy2(pk, os.path.join(DATA, "backtest_picks.json"))

    bust_cache()

    print(f"站点已构建：{n} 个静态文件 → {DOCS}")
    print("  index / portfolio / strategy / signals")
    print(f"  数据：signal.json history.json curve.json（{len(hist)} 期信号）")


if __name__ == "__main__":
    main()
