# -*- coding: utf-8 -*-
"""按专家委员会意见修复：
1) 今日涨跌统一口径：day = Σ 股数×(最新价−昨收)，基数用当前市值；两页同源
2) 首屏答案做成最大字，并补「全周期」第三层
3) 信号页与主站同源（不再内联旧快照）
"""
import io
import re

# ---------- 1. DSH.today 重构：用真实持股与昨日收盘 ----------
JS = "site_src/assets/app.js"
s = io.open(JS, encoding="utf-8").read()

old = s[s.find("  DSH.today = function"):s.find("  /* 目标权重（占总资金） */")]
new = '''  DSH.today = function (signal, quotes, equity) {
    // 口径：day = Σ 股数×(最新价−昨收)，基数 = 当前市值（不是目标权重）
    const qs = (quotes && quotes.quotes) || {};
    const pos = (equity && equity.position) || [];
    const amt = +(localStorage.getItem("dsh.amt") || 100000);
    const exp = (equity && equity.exposure != null) ? equity.exposure : 1;
    let day = 0, mv = 0, covered = 0;
    pos.forEach(function (p) {
      const q = qs[p.ticker];
      const base = p.entry > 0 ? (amt * p.weight * exp) / p.entry : 0;   // 股数
      const price = (q && q.p > 0) ? q.p : p.last;                       // 优先实时价
      const prev = (q && q.pc > 0) ? q.pc : null;
      if (!base || !price) return;
      mv += base * price;
      if (prev) { day += base * (price - prev); covered++; }
    });
    const cash = amt * (1 - exp);
    const totalMv = mv + cash;
    if (!covered || !totalMv) return null;
    const age = DSH.ageHours(quotes && quotes.ts);
    const live = age != null && age <= 0.75;
    return {
      amount: day,
      pct: totalMv - day > 0 ? day / (totalMv - day) : null,
      session: (quotes && quotes.session) || "",
      asOf: (quotes && quotes.ts_local) || "",
      live: live,
      label: "今日",
    };
  };

'''
s = s.replace(old, new)
io.open(JS, "w", encoding="utf-8").write(s)
print("DSH.today 已按真实持股重写")

# ---------- 2. 首页：答案最大 + 三层信息 ----------
IDX = "site_src/index.html"
h = io.open(IDX, encoding="utf-8").read()

# 传参补 equity
h = h.replace("DSH.today(sig, q)", "DSH.today(sig, q, EQ0)")
h = h.replace("DSH.today(sig, q, EQ0) &&", "DSH.today(sig, q, EQ0) &&")

# 首屏：h1 加大、并把三层信息放进副行
h = h.replace('<h2 id="verdict">—</h2>', '<h2 id="verdict" style="font-size:34px;line-height:1.18">—</h2>')
if "id=\"threeLayer\"" not in h:
    h = h.replace('<div class="sub" id="verdictSub" style="margin:6px 0 0;font-size:13px"></div>',
                  '<div class="sub" id="verdictSub" style="margin:6px 0 0;font-size:13px"></div>\n'
                  '        <div id="threeLayer" style="margin-top:12px;font-size:12.5px;color:var(--txt-3);line-height:1.9"></div>')

# 生成三层信息（今日 / 年内 / 全周期）
anchor = "  /* ---- 策略账户（理论账户，所有人一致） ---- */"
add = '''  // 三层时间视野：当下(今日) / 中期(年内) / 远期(全周期)
  (function () {
    const el = document.getElementById("threeLayer");
    if (!el) return;
    const t = DSH.today(sig, q, await DSH.json("equity", null));
    const cv = await DSH.json("curve", null);
    const parts = [];
    if (t && t.pct != null) {
      parts.push("今日 <b class=\\"" + DSH.cls(t.pct) + "\\">" + DSH.pct(t.pct) + "</b>（" + DSH.money(t.amount) + "）");
    }
    const eq0 = await DSH.json("equity", null);
    if (eq0 && eq0.stats) {
      const y = eq0.base_date ? (+String(eq0.base_date).slice(0, 4) + 1) : "";
      parts.push(y + " 年内 <b class=\\"" + DSH.cls(eq0.stats.strategy) + "\\">" + DSH.pct(eq0.stats.strategy) + "</b>");
    }
    if (cv && cv.points && cv.points.length) {
      const tot = cv.points[cv.points.length - 1].s - 1;
      parts.push("全周期 <b class=\\"" + DSH.cls(tot) + "\\">" + DSH.pct(tot, 0) + "</b>（2010–2025）");
    }
    el.innerHTML = parts.join(" ｜ ");
  })();

'''
if "threeLayer" in h and "三层时间视野" not in h:
    h = h.replace(anchor, add + anchor, 1)

io.open(IDX, "w", encoding="utf-8").write(h)
print("首屏三层信息已加")
