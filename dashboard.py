# -*- coding: utf-8 -*-
"""signals.html —— 历史信号（一张表，不重复首页与账户页的信息）

只回答一个问题：每年 12 月末按规则选出了谁、当年结果如何。
当年持仓、当日操作、净值曲线都在「总览 / 当期策略表现 / 策略表现」里，这里不重复。
"""
import json
import os

NAV = [
    ("index.html", "总览"),
    ("portfolio.html", "持仓"),
    ("strategy.html", "策略"),
    ("signals.html", "信号"),
]
CSS = """
:root{--bg:#080A0F;--txt:#EDF1F7;--txt2:#A9B6C9;--txt3:#6F7D92;--gold:#F2C46B;--up:#FF6B72;--down:#3BD68A;--line:rgba(255,255,255,.07)}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);
font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:28px 20px 60px}
.nav{display:flex;gap:6px;align-items:center;padding:14px 0 26px}
.logo{font-weight:800;letter-spacing:-.02em;margin-right:18px}
.logo i{color:var(--gold);font-style:normal}
.nav a{padding:8px 14px;border-radius:9px;color:var(--txt2);text-decoration:none;font-size:14px;min-height:44px;display:inline-flex;align-items:center}
.nav a.on{background:rgba(242,196,107,.13);color:var(--gold)}
h1{font-size:30px;margin:6px 0 6px;letter-spacing:-.02em}
.sub{color:var(--txt3);font-size:12.5px;margin-bottom:20px}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;color:var(--txt3);font-weight:500;font-size:12px;padding:10px 8px;border-bottom:1px solid var(--line)}
td{padding:12px 8px;border-bottom:1px solid var(--line);vertical-align:top}
.num{text-align:right;font-variant-numeric:tabular-nums}
.up{color:var(--up)}.down{color:var(--down)}
.tk{font-weight:700}
.w{color:var(--txt3);font-size:11.5px}
.foot{margin-top:28px;color:var(--txt3);font-size:12px}
@media (max-width:560px){h1{font-size:24px}td,th{padding:9px 5px;font-size:13px}}
"""


def _fmt(v, d=2, pct=True):
    if v is None:
        return "—"
    return ("%+." + str(d) + "f%%") % (v * 100) if pct else ("%." + str(d) + "f") % v


def build(hist, out_path):
    base = os.path.dirname(os.path.abspath(__file__))
    docs = os.path.join(base, "docs", "data")
    sh = {}
    sig = {}
    try:
        sh = json.load(open(os.path.join(docs, "strategy_history.json"), encoding="utf-8"))
    except Exception:
        pass
    try:
        sig = json.load(open(os.path.join(docs, "signal.json"), encoding="utf-8"))
    except Exception:
        pass

    rows = []
    for r in (sh.get("years") or []):
        holds = " · ".join(
            '<span class="tk">%s</span> <span class="w">%.0f%%</span>' % (p["ticker"], p["weight"] * 100)
            for p in r.get("holdings") or [])
        cls = "up" if (r.get("ret") or 0) >= 0 else "down"
        rows.append(
            "<tr><td>%d</td><td>%s</td><td class='num %s'>%s</td><td class='num'>%s</td></tr>"
            % (r["year"], holds, cls, _fmt(r.get("ret"), 1), _fmt(r.get("dd"), 1)))

    cur = ""
    if sig:
        off = sig.get("official") or []
        if off:
            cur = ("<div class='sub' style='margin-top:22px'>%s 年度持仓：%s</div>"
                   % (sig.get("official_year", ""),
                      " · ".join("%s %.0f%%" % (p["ticker"], p["weight"] * 100) for p in off)))

    nav = "".join('<a href="%s"%s>%s</a>' % (h, ' class="on"' if h == "signals.html" else "", t)
                  for h, t in NAV)

    html = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" type="image/svg+xml" href="favicon.svg">
<title>历史信号 · LEADER QUANT</title><style>%s</style></head><body><div class="wrap">
<div class="nav"><div class="logo">LEADER<i>·</i>QUANT</div>%s</div>
<h1>历史信号</h1>
<div class="sub">每年 12 月末按过去 12 个月涨幅取前 3 名，次年全年持有</div>
<table><thead><tr><th>年度</th><th>持仓（权重）</th><th class="num">当年收益</th><th class="num">年内回撤</th></tr></thead>
<tbody>%s</tbody></table>
%s
<div class="foot">仅供研究，不构成投资建议</div>
</div></body></html>""" % (CSS, nav, "".join(rows), cur)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path
