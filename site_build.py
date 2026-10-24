# -*- coding: utf-8 -*-
"""site_build.py — 把《排名策略对比》更新为「带实时数据」的版本，并发布到 GitHub Pages

核心：output/排名策略对比.html 既是源文件也是成品。
      每次运行只替换其中被标记的两块区域（幂等，不会重复注入）：
        <!--LIVE:BEGIN--> ... <!--LIVE:END-->     顶部实时信号卡
        <!--PICKS:BEGIN--> ... <!--PICKS:END-->   第七节「本期该买什么」

产物：
  output/排名策略对比.html              （原地更新，入库）
  output/策略模型研究与行动步骤.html     （同内容副本）
  docs/index.html                       （Pages 首页）
  docs/signals.html                     （信号看板）
"""
import json
import os
import re
import shutil

import dashboard
import report_live

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "output", "排名策略对比.html")
COPY = os.path.join(BASE, "output", "策略模型研究与行动步骤.html")
HIST = os.path.join(BASE, "output", "signal_history.json")
DOCS = os.path.join(BASE, "docs")

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
#live-bar a:hover{text-decoration:underline}
#live-bar .u{font-size:11.5px;color:#6f8199;margin-top:10px}
</style>"""

LIVE_HTML = """<div id="live-bar"><div class="box">
<div class="row">
  <div><div class="k">最新信号</div><div class="v" id="lb-month">—</div></div>
  <div><div class="k">大盘开关</div><div class="v"><span class="pill" id="lb-trend">—</span></div></div>
  <div><div class="k">正式持仓（年度锁定）</div><div class="v" id="lb-picks">—</div></div>
  <div><div class="k">建议仓位</div><div class="v" id="lb-exp">—</div></div>
  <div style="margin-left:auto"><a href="signals.html">查看信号看板与历史 →</a></div>
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
  document.getElementById('lb-exp').textContent = (r.exposure*100).toFixed(0)+'%';
  document.getElementById('lb-note').textContent =
    (r.action ? r.action+' ｜ ' : '') + '生成于 '+r.date+' ｜ 共 '+h.length+' 期信号 ｜ 仅供研究，不构成投资建议';
})();
</script>"""

B_LIVE, E_LIVE = "<!--LIVE:BEGIN-->", "<!--LIVE:END-->"
B_PICK, E_PICK = "<!--PICKS:BEGIN-->", "<!--PICKS:END-->"


def _replace(html, begin, end, block, anchor="<body>"):
    """有标记就替换，没有就在 <body> 后插入一次（幂等）"""
    if begin in html:
        return re.sub(re.escape(begin) + r".*?" + re.escape(end),
                      lambda m: block, html, count=1, flags=re.S)
    if anchor in html:
        return html.replace(anchor, anchor + "\n" + block, 1)
    return block + html


def render(hist):
    with open(SRC, encoding="utf-8") as f:
        html = f.read()

    live = (B_LIVE + "\n" + LIVE_CSS + "\n" + LIVE_HTML + "\n"
            + LIVE_JS.replace("__DATA__", json.dumps(hist, ensure_ascii=False)) + "\n" + E_LIVE)

    if B_PICK in html:
        picks = B_PICK + "\n" + report_live.render(hist[0] if hist else None) + "\n" + E_PICK
        html = _replace(html, B_PICK, E_PICK, picks)

    html = _replace(html, B_LIVE, E_LIVE, live)
    return html


def main():
    hist = []
    if os.path.exists(HIST):
        with open(HIST, encoding="utf-8") as f:
            hist = json.load(f)

    html = render(hist)
    for p in (SRC, COPY):
        with open(p, "w", encoding="utf-8") as f:
            f.write(html)
    os.makedirs(DOCS, exist_ok=True)
    with open(os.path.join(DOCS, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

    dashboard.build(hist, os.path.join(DOCS, "signals.html"))

    for fn in ("策略模型研究与行动步骤.pdf", "策略模型研究与行动步骤.xlsx"):
        s = os.path.join(BASE, "output", fn)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(DOCS, fn))

    print(f"已更新: {SRC}  ({len(html)//1024} KB)")
    print(f"副本  : {COPY}")
    print(f"站点  : {DOCS}" + os.sep + f"index.html  |  signals.html  ({len(hist)} 期信号)")


if __name__ == "__main__":
    main()
