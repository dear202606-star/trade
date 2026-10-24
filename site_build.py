# -*- coding: utf-8 -*-
"""site_build.py — 把研究报告 + 实时信号合成 GitHub Pages 站点

产物：
  docs/index.html    策略模型研究与行动步骤（顶部带实时信号卡）
  docs/signals.html  信号看板（可切换月份）
"""
import os, json, shutil

import dashboard

BASE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(BASE, "output", "策略模型研究与行动步骤.html")
HIST = os.path.join(BASE, "output", "signal_history.json")
DOCS = os.path.join(BASE, "docs")

LIVE_CSS = """
<style id="live-css">
#live-bar{background:linear-gradient(135deg,#1a2130,#131a26);border:1px solid #2f3b52;
border-radius:14px;padding:18px 22px;margin:0 0 26px}
#live-bar .row{display:flex;flex-wrap:wrap;gap:22px;align-items:center}
#live-bar .k{font-size:12px;color:#93a4bd;margin-bottom:2px}
#live-bar .v{font-size:20px;font-weight:800;color:#ffc857;letter-spacing:-.4px}
#live-bar .pill{display:inline-block;padding:3px 12px;border-radius:20px;font-size:13px;font-weight:700}
#live-bar .on{background:#12351f;color:#5fe08a}
#live-bar .off{background:#3a2018;color:#ffab6b}
#live-bar .tk{display:inline-block;background:#1b2434;color:#ffd98a;border-radius:6px;
padding:2px 9px;margin-right:6px;font-family:Consolas,monospace;font-weight:700;font-size:14px}
#live-bar a{color:#4da3ff;font-size:13px;text-decoration:none}
#live-bar a:hover{text-decoration:underline}
#live-bar .u{font-size:11.5px;color:#6f8199;margin-top:10px}
</style>
"""

LIVE_HTML = """
<div id="live-bar" style="max-width:1100px;margin:26px auto 0;font-family:'Microsoft YaHei',sans-serif">
  <div class="row">
    <div><div class="k">最新信号</div><div class="v" id="lb-month">—</div></div>
    <div><div class="k">大盘开关</div><div class="v"><span class="pill" id="lb-trend">—</span></div></div>
    <div><div class="k">本期持仓</div><div class="v" id="lb-picks">—</div></div>
    <div><div class="k">建议仓位</div><div class="v" id="lb-exp">—</div></div>
    <div style="margin-left:auto"><a href="signals.html">查看信号看板与历史 →</a></div>
  </div>
  <div class="u" id="lb-note">由 GitHub Actions 自动更新</div>
</div>
"""

LIVE_JS = """
<script>
(function(){
  var h = __DATA__;
  if(!h || !h.length) return;
  var r = h[0];
  document.getElementById('lb-month').textContent = r.month;
  var t = document.getElementById('lb-trend');
  t.textContent = r.trend; t.className = 'pill ' + (r.trend==='满仓'?'on':'off');
  document.getElementById('lb-picks').innerHTML = r.picks.map(function(p){
    return '<span class="tk">'+p.ticker+' '+(p.weight*100).toFixed(0)+'%</span>';
  }).join('');
  document.getElementById('lb-exp').textContent = (r.exposure*100).toFixed(0)+'%';
  document.getElementById('lb-note').textContent =
    '数据截至 '+r.month+' ｜ 生成于 '+r.date+' ｜ 共 '+h.length+' 期信号 ｜ 仅供研究，不构成投资建议';
})();
</script>
"""


def main():
    hist = []
    if os.path.exists(HIST):
        with open(HIST, encoding="utf-8") as f:
            hist = json.load(f)

    os.makedirs(DOCS, exist_ok=True)

    # ---------- index.html ----------
    with open(REPORT, encoding="utf-8") as f:
        html = f.read()
    if 'id="live-css"' not in html:
        html = html.replace("</head>", LIVE_CSS + "</head>")
    if 'id="live-bar"' not in html:
        idx = html.find("<body>")
        if idx > 0:
            html = html[: idx + 6] + LIVE_HTML + html[idx + 6:]
        else:
            html = LIVE_HTML + html
    if "/*LIVE_JS*/" not in html and 'lb-month' in html:
        html = html.replace("</body>", LIVE_JS.replace("__DATA__", json.dumps(hist, ensure_ascii=False)) + "</body>")

    out = os.path.join(DOCS, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)

    # ---------- signals.html ----------
    dashboard.build(hist, os.path.join(DOCS, "signals.html"))

    # ---------- 附带下载 ----------
    for fn in ("策略模型研究与行动步骤.pdf", "策略模型研究与行动步骤.xlsx"):
        src = os.path.join(BASE, "output", fn)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(DOCS, fn))

    print(f"站点已生成: {out}")
    print(f"信号看板: {os.path.join(DOCS, 'signals.html')}  ({len(hist)} 期)")


if __name__ == "__main__":
    main()
