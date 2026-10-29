# -*- coding: utf-8 -*-
"""site_build.py — 构建整站到 docs/

流程：
  1. 复制 site_src/*（页面、样式、脚本）→ docs/
  2. 写数据文件 docs/data/{signal,history,curve}.json
  3. 生成 docs/signals.html（信号历史）
  4. 生成 docs/signals.html（信号看板）

注意：output/排名策略对比.html 仍是「源 + 成品」合一，只有报告页用到它。
"""
import json
import os
import re
import shutil
from datetime import datetime as dt, timezone

import dashboard
import report_live

BASE = os.path.dirname(os.path.abspath(__file__))
SRC_REPORT = os.path.join(BASE, "output", "排名策略对比.html")
HIST = os.path.join(BASE, "output", "signal_history.json")
QUAL = os.path.join(BASE, "output", "data_quality.json")
SRC_SITE = os.path.join(BASE, "site_src")
DOCS = os.path.join(BASE, "docs")
DATA = os.path.join(DOCS, "data")

LIVE_CSS = """<style>
#live-bar{max-width:1040px;margin:26px auto 0;padding:0 8px;font-family:'Microsoft YaHei',sans-serif}
#live-bar .box{background:linear-gradient(135deg,#1a2130,#131a26);border:1px solid #2f3b52;border-radius:14px;padding:18px 22px}
#live-bar .row{display:flex;flex-wrap:wrap;gap:24px;align-items:center}
#live-bar .k{font-size:12px;color:#93a4bd;margin-bottom:2px}
#live-bar .v{font-size:20px;font-weight:800;color:#ffc857;letter-spacing:-.4px}
#live-bar .pill{display:inline-block;padding:3px 12px;border-radius:20px;font-size:13px;font-weight:700}
#live-bar .on{background:#12351f;color:#5fe08a}
#live-bar .off{background:#3a2018;color:#ffab6b}
#live-bar .tk{display:inline-block;background:#1b2434;color:#ffd98a;border-radius:6px;padding:2px 9px;margin-right:6px;font-family:Consolas,monospace;font-weight:700;font-size:14px}
#live-bar a{color:#4da3ff;font-size:13px;text-decoration:none}
#live-bar .u{font-size:11.5px;color:#6f8199;margin-top:10px}
</style>"""

LIVE_HTML = """<div id="live-bar"><div class="box">
<div class="row">
  <div><div class="k">最新信号</div><div class="v" id="lb-month">—</div></div>
  <div><div class="k">大盘开关</div><div class="v"><span class="pill" id="lb-trend">—</span></div></div>
  <div><div class="k">正式持仓（年度锁定）</div><div class="v" id="lb-picks">—</div></div>
  <div><div class="k">建议仓位</div><div class="v" id="lb-exp">—</div></div>
  <div style="margin-left:auto"><a href="index.html">← 返回总览</a></div>
</div>
<div class="u" id="lb-note">由 GitHub Actions 自动更新</div>
</div></div>"""

LIVE_JS = """<script>
(function(){
  var h = __DATA__;
  if(!h || !h.length) return;
  var r = h[0];
  document.getElementById('lb-month').textContent = r.month;
  var t = document.getElementById('lb-trend');
  t.textContent = r.trend; t.className = 'pill ' + (r.trend==='满仓'?'on':'off');
  document.getElementById('lb-picks').innerHTML = (r.official||r.picks).map(function(p){
    return '<span class="tk">'+p.ticker+' '+(p.weight*100).toFixed(0)+'%</span>';
  }).join('');
  document.getElementById('lb-exp').textContent = (r.exp*100).toFixed(0)+'%';
  document.getElementById('lb-note').textContent =
    (r.action ? r.action+' ｜ ' : '') + '生成于 '+r.date+' ｜ 仅供研究，不构成投资建议';
})();
</script>"""

B_LIVE, E_LIVE = "<!--LIVE:BEGIN-->", "<!--LIVE:END-->"
B_PICK, E_PICK = "<!--PICKS:BEGIN-->", "<!--PICKS:END-->"


def _replace(html, begin, end, block, anchor="<body>"):
    if begin in html:
        return re.sub(re.escape(begin) + r".*?" + re.escape(end),
                      lambda m: block, html, count=1, flags=re.S)
    if anchor in html:
        return html.replace(anchor, anchor + "\n" + block, 1)
    return block + html


def build_report(hist):
    with open(SRC_REPORT, encoding="utf-8") as f:
        html = f.read()
    live = (B_LIVE + "\n" + LIVE_CSS + "\n" + LIVE_HTML + "\n"
            + LIVE_JS.replace("__DATA__", json.dumps(hist, ensure_ascii=False)) + "\n" + E_LIVE)
    if B_PICK in html:
        picks = B_PICK + "\n" + report_live.render(hist[0] if hist else None) + "\n" + E_PICK
        html = _replace(html, B_PICK, E_PICK, picks)
    html = _replace(html, B_LIVE, E_LIVE, live)
    return html


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
    for f in ("assets/app.css", "assets/app.js"):
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
        for a in ("assets/app.css", "assets/app.js"):
            new = re.sub(re.escape(a) + r"(\?v=[0-9a-f]+)?", a + "?v=" + v, new)
        if new != html:
            with open(p, "w", encoding="utf-8") as f:
                f.write(new)
            n += 1
    print(f"  静态资源版本：{v}（{n} 个页面已更新引用）")
    return v



NAV_BAR = """<div style="position:sticky;top:0;z-index:50;background:#0b0f17f2;border-bottom:1px solid #232f44;">
<div style="max-width:1180px;margin:0 auto;padding:0 22px;display:flex;align-items:center;gap:4px;height:56px;
font-family:'Microsoft YaHei',system-ui,sans-serif;">
<a href="index.html" style="font-weight:800;color:#e8eef8;text-decoration:none;margin-right:18px;font-size:15px;">LEADER<span style="color:#ffc857;">·</span>QUANT</a>
<a href="index.html" style="padding:6px 12px;border-radius:9px;color:#8fa2bd;text-decoration:none;font-size:14px;">总览</a>
<a href="portfolio.html" style="padding:6px 12px;border-radius:9px;color:#8fa2bd;text-decoration:none;font-size:14px;">持仓</a>
<a href="strategy.html" style="padding:6px 12px;border-radius:9px;color:#8fa2bd;text-decoration:none;font-size:14px;">策略</a>
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
                nav = nav.replace('background:#182233;color:#ffc857;text-decoration:none;font-size:14px;font-weight:700;','color:#8fa2bd;font-weight:400;text-decoration:none;font-size:14px;padding:6px 12px;border-radius:9px;')
                nav = nav.replace('href="%s" style="padding:6px 12px;border-radius:9px;color:#8fa2bd;' % active,
                                  'href="%s" style="padding:6px 12px;border-radius:9px;background:#182233;color:#ffc857;font-weight:700;' % active)
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

    # 云同步配置：publishable key 本就是公开的，数据靠「同步码 + 客户端加密」保护
    # 优先用随源码入库的公开配置（publishable key 可公开），其次本地私密副本
    cfgp = os.path.join(BASE, "site_src", "cloud.json")
    if not os.path.exists(cfgp):
        cfgp = os.path.join(BASE, "output", "_supabase.json")
    if os.path.exists(cfgp):
        try:
            cfg = json.load(open(cfgp, encoding="utf-8"))
            key = cfg.get("key") or cfg.get("publishable_key")
            if cfg.get("url") and key:
                with open(os.path.join(DATA, "cloud.json"), "w", encoding="utf-8") as f:
                    json.dump({"url": cfg["url"], "key": key}, f, ensure_ascii=False)
            else:
                print("WARN: cloud.json 缺少 url 或 key")
        except Exception as e:
            print("WARN: 云同步配置写入失败", e)

    if hist:
        with open(os.path.join(DATA, "signal.json"), "w", encoding="utf-8") as f:
            json.dump(with_validity(hist[0]), f, ensure_ascii=False)
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
