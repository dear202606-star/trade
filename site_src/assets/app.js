/* app.js — 共享工具：导航、数据、持仓存储、组合计算 */
(function (g) {
  "use strict";

  const NAV = [
    ["index.html", "总览"],
    ["portfolio.html", "我的持仓"],
    ["strategy.html", "策略表现"],
  ];

  const DSH = (g.DSH = {});

  /* 单只移动止损：从持有期最高点回撤 30% 清仓（回测验证：16 年零亏损年） */
  DSH.STOP = 0.30;

  /* 更新每只票的持有期最高价并落盘 */
  DSH.updatePeaks = function (pf, quotes) {
    let changed = false;
    (pf.rows || []).forEach((r) => {
      const q = quotes && quotes[r.ticker];
      const px = q ? q.p : 0;
      const hi = Math.max(r.peak || 0, px, r.cost || 0);
      if (hi > (r.peak || 0) + 1e-9) { r.peak = hi; changed = true; }
    });
    if (changed) DSH.save(pf);
    return pf;
  };

  /* ---------- 格式化 ---------- */
  DSH.money = (v, cur) => {
    if (v == null || isNaN(v)) return "—";
    const sym = cur === "CNY" ? "¥" : "$";
    const a = Math.abs(v);
    const s = a >= 1e6 ? (a / 1e6).toFixed(2) + "M"
      : a >= 1e4 ? Math.round(a).toLocaleString("en-US")
      : a.toLocaleString("en-US", { maximumFractionDigits: 2 });
    return (v < 0 ? "-" : "") + sym + s;
  };
  DSH.pct = (v, d) => (v == null || isNaN(v) ? "—" : (v >= 0 ? "+" : "") + (v * 100).toFixed(d == null ? 2 : d) + "%");
  DSH.cls = (v) => (v == null || isNaN(v) || Math.abs(v) < 1e-9 ? "zero" : v > 0 ? "up" : "down");
  DSH.esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  /* ---------- 数据 ---------- */
  const cache = {};
  DSH.json = async function (name, fallback) {
    if (cache[name] !== undefined) return cache[name];
    let url = "data/" + name + ".json";
    try {
      url = "data/" + name + ".json?t=" + Date.now();
      const r = await fetch(url, { cache: "no-store" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      cache[name] = await r.json();
    } catch (e) {
      g.__dshErr = (g.__dshErr || []).concat(url + " -> " + e.message);
      cache[name] = fallback === undefined ? null : fallback;
    }
    return cache[name];
  };

  /* ---------- 本地存储 ---------- */
  const KEY = "dsh.portfolio.v1";
  DSH.load = function () {
    try {
      const d = JSON.parse(localStorage.getItem(KEY) || "null");
      if (d && Array.isArray(d.rows)) return d;
    } catch (e) {}
    return { capital: 0, cur: "USD", rows: [] };
  };
  DSH.save = function (d) { localStorage.setItem(KEY, JSON.stringify(d)); };

  /* ---------- 导航 ---------- */
  DSH.nav = function () {
    const here = (location.pathname.split("/").pop() || "index.html").toLowerCase();
    document.write(
      '<div class="nav"><div class="nav-in">' +
      '<div class="logo">LEADER<i>·</i>QUANT</div>' +
      NAV.map(([h, t]) => '<a class="n' + (h === here ? " on" : "") + '" href="' + h + '">' + t + "</a>").join("") +
      '<div class="sp"></div><div class="tick" id="navTick">连接行情…</div></div></div>'
    );
  };

  DSH.paintTick = async function () {
    const q = await DSH.json("quotes", {});
    const el = document.getElementById("navTick");
    if (!el) return;
    if (!q || !q.ts_local) { el.textContent = "行情未就绪"; return; }
    const spy = q.quotes && q.quotes.SPY;
    const c = spy ? (spy.chg >= 0 ? "up" : "dn") : "";
    const age = DSH.ageHours(q.ts);
    const stale = age == null ? false : age > 36;
    const ageTxt = age == null ? "" :
      age < 1 ? "刚刚更新" :
      age < 24 ? Math.floor(age) + " 小时前" : Math.floor(age / 24) + " 天前";
    el.innerHTML =
      (spy ? "SPY <b>" + spy.p.toFixed(2) + "</b> <span class=\"" + c + "\">" + DSH.pct(spy.chg) + "</span><br>" : "") +
      '<span style="color:' + (stale ? "#FF6B72" : "#5A6779") + '">' +
      (stale ? "⚠ 数据已 " + ageTxt : ageTxt) + " ｜ " + DSH.esc(q.ts_local) + "</span>";
  };

  /* 距上次更新的小时数 */
  DSH.ageHours = function (iso) {
    if (!iso) return null;
    const t = Date.parse(iso);
    if (isNaN(t)) return null;
    return Math.max(0, (Date.now() - t) / 3600000);
  };

  /* ---------- 组合计算 ---------- */
  DSH.compute = function (pf, quotes, fx) {
    const rows = pf.rows.map((r) => {
      const q = quotes && quotes[r.ticker];
      const price = q ? q.p : (r.cost || 0);
      const mv = price * r.shares;
      const cost = r.cost * r.shares;
      const pnl = mv - cost;
      const peak = Math.max(r.peak || 0, price, r.cost || 0);
      const stopLine = peak * (1 - DSH.STOP);
      return Object.assign({}, r, {
        name: q ? q.name : r.ticker,
        price, mv, cost, pnl,
        pnlPct: cost > 0 ? pnl / cost : 0,
        dayPct: q ? q.chg : 0,
        dayPnl: q ? (q.p - q.pc) * r.shares : 0,
        hasQuote: !!q,
        peak, stopLine,
        stopDist: stopLine > 0 ? price / stopLine - 1 : null,
      });
    });
    const mv = rows.reduce((a, r) => a + r.mv, 0);
    const cost = rows.reduce((a, r) => a + r.cost, 0);
    const dayPnl = rows.reduce((a, r) => a + (r.dayPnl || 0), 0);
    const cap = pf.capital || 0;
    rows.forEach((r) => (r.weight = cap > 0 ? r.mv / cap : (mv > 0 ? r.mv / mv : 0)));
    const cash = Math.max(0, cap - cost);
    return {
      rows, mv, cost,
      pnl: mv - cost,
      pnlPct: cost > 0 ? (mv - cost) / cost : 0,
      dayPnl,
      dayPct: mv - dayPnl > 0 ? dayPnl / (mv - dayPnl) : 0,
      cash, total: mv + cash,
      posPct: cap > 0 ? mv / cap : 0,
    };
  };

  /* ---------- 目标权重（占总资金） ---------- */
  DSH.targets = function (sig) {
    const out = {};
    if (!sig) return out;
    (sig.official || sig.picks || []).forEach((p) => {
      out[p.ticker] = p.weight * sig.exposure * 0.30;
    });
    return out;
  };

  /* ---------- 图表：迷你走势 ---------- */
  DSH.spark = function (pts, opts) {
    opts = opts || {};
    const w = opts.w || 260, h = opts.h || 56, pad = 3;
    if (!pts || pts.length < 2) return '<div style="height:' + h + 'px"></div>';
    const ys = pts.map((p) => p.y);
    const min = Math.min.apply(null, ys), max = Math.max.apply(null, ys);
    const span = max - min || 1;
    const X = (i) => pad + (i / (pts.length - 1)) * (w - pad * 2);
    const Y = (v) => h - pad - ((v - min) / span) * (h - pad * 2);
    let d = "", area = "";
    pts.forEach((p, i) => {
      const x = X(i).toFixed(1), y = Y(p.y).toFixed(1);
      d += (i ? "L" : "M") + x + " " + y;
    });
    area = d + "L" + X(pts.length - 1).toFixed(1) + " " + (h - pad) + "L" + X(0).toFixed(1) + " " + (h - pad) + "Z";
    const up = ys[ys.length - 1] >= ys[0];
    const col = opts.color || (up ? "#3BD68A" : "#FF6B72");
    const id = "g" + Math.random().toString(36).slice(2, 8);
    return '<svg viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="none" style="width:100%;height:' + h + 'px;display:block">' +
      '<defs><linearGradient id="' + id + '" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0%" stop-color="' + col + '" stop-opacity=".28"/>' +
      '<stop offset="100%" stop-color="' + col + '" stop-opacity="0"/></linearGradient></defs>' +
      '<path d="' + area + '" fill="url(#' + id + ')"/>' +
      '<path d="' + d + '" fill="none" stroke="' + col + '" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>' +
      "</svg>";
  };

  /* ---------- 图表：带坐标的折线 ---------- */
  DSH.lineChart = function (series, opts) {
    opts = opts || {};
    const w = 900, h = opts.h || 320, L = 54, R = 16, T = 16, B = 28;
    const all = series.reduce((a, s) => a.concat(s.pts.map((p) => p.y)), []);
    if (!all.length) return "";
    let min = Math.min.apply(null, all), max = Math.max.apply(null, all);
    const logScale = !!opts.log && min > 0;
    if (logScale) { min = Math.log(min); max = Math.log(max); }
    const pad = (max - min) * 0.08 || 0.05;
    min -= pad; max += pad;
    const label = opts.fmt || ((v) => (logScale ? Math.exp(v) : v) * 100 + "%");
    if (opts.fmt) { /* 自定义格式时不做百分比换算 */ }
    const n = Math.max.apply(null, series.map((s) => s.pts.length));
    const X = (i) => L + (i / Math.max(1, n - 1)) * (w - L - R);
    const Y = (v) => T + (1 - ((logScale ? Math.log(v) : v) - min) / (max - min || 1)) * (h - T - B);
    let svg = '<svg viewBox="0 0 ' + w + " " + h + '" style="width:100%;height:auto;display:block">';
    // 网格 + Y 轴
    for (let k = 0; k <= 4; k++) {
      const rawV = logScale ? Math.exp(min + (max - min) * k / 4) : (min + (max - min) * k / 4);
      const y = (T + (1 - ((logScale ? Math.log(rawV) : rawV) - min) / (max - min || 1)) * (h - T - B)).toFixed(1);
      svg += '<line x1="' + L + '" y1="' + y + '" x2="' + (w - R) + '" y2="' + y +
        '" stroke="rgba(255,255,255,.06)" stroke-width="1"/>';
      svg += '<text x="' + (L - 9) + '" y="' + (+y + 4) + '" fill="#6F7D92" font-size="11" text-anchor="end">' +
        (v * 100).toFixed(0) + "%</text>";
    }
    series.forEach((s) => {
      let d = "";
      s.pts.forEach((p, i) => { d += (i ? "L" : "M") + X(i).toFixed(1) + " " + Y(p.y).toFixed(1); });
      svg += '<path d="' + d + '" fill="none" stroke="' + s.color + '" stroke-width="' + (s.width || 2) +
        '" stroke-linejoin="round" stroke-linecap="round"' + (s.dash ? ' stroke-dasharray="4 4"' : "") + "/>";
    });
    // X 轴标签
    const pts = series[0].pts;
    const step = Math.max(1, Math.floor(pts.length / 6));
    for (let i = 0; i < pts.length; i += step) {
      const d0 = pts[i].d;
      svg += '<text x="' + X(i).toFixed(1) + '" y="' + (h - 8) + '" fill="#6F7D92" font-size="11" text-anchor="middle">' +
        d0.slice(4, 6) + "/" + d0.slice(6, 8) + "</text>";
    }
    svg += "</svg>";
    return svg;
  };

  /* ---------- 收益 + 回撤 合一图 ---------- */
  DSH.combo = function (eq, dd, opts) {
    opts = opts || {};
    const w = 900, h = opts.h || 380, L = 52, R = 14, T = 14;
    const H1 = h * 0.68, GAP = 12, H2 = h - H1 - GAP - 26;
    const eqs = eq.map(p => p.y);
    let mn = Math.min.apply(null, eqs), mx = Math.max.apply(null, eqs);
    const pad = (mx - mn) * 0.12 || 0.05;
    mn -= pad; mx += pad;
    const n = eq.length;
    const X = i => L + (i / Math.max(1, n - 1)) * (w - L - R);
    const Y1 = v => T + (1 - (v - mn) / (mx - mn || 1)) * H1;
    const dmin = Math.min.apply(null, dd.map(p => p.y));
    const Y2 = v => T + H1 + GAP + (1 - (v - dmin) / (0 - dmin || 1)) * H2;
    let svg = '<svg viewBox="0 0 ' + w + ' ' + h + '" style="width:100%;height:auto;display:block">';
    for (let k = 0; k <= 3; k++) {
      const v = mn + (mx - mn) * k / 3, y = Y1(v).toFixed(1);
      svg += '<line x1="' + L + '" y1="' + y + '" x2="' + (w - R) + '" y2="' + y + '" stroke="rgba(255,255,255,.05)"/>';
      svg += '<text x="' + (L - 8) + '" y="' + (+y + 4) + '" fill="#6F7D92" font-size="10.5" text-anchor="end">' + (v * 100).toFixed(0) + '%</text>';
    }
    // 策略曲线
    let d1 = '';
    eq.forEach((p, i) => { d1 += (i ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y1(p.y).toFixed(1); });
    svg += '<path d="' + d1 + '" fill="none" stroke="#F2C46B" stroke-width="2.2" stroke-linejoin="round"/>';
    // 基准
    if (opts.bench) {
      let d2 = '';
      opts.bench.forEach((p, i) => { d2 += (i ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y1(p.y).toFixed(1); });
      svg += '<path d="' + d2 + '" fill="none" stroke="#6F7D92" stroke-width="1.4" stroke-dasharray="4 4"/>';
    }
    // 回撤区域
    let d3 = '';
    dd.forEach((p, i) => { d3 += (i ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y2(p.y).toFixed(1); });
    d3 += 'L' + X(dd.length - 1).toFixed(1) + ' ' + Y2(0).toFixed(1) + 'L' + X(0).toFixed(1) + ' ' + Y2(0).toFixed(1) + 'Z';
    svg += '<path d="' + d3 + '" fill="rgba(255,107,114,.16)" stroke="#FF6B72" stroke-width="1.2"/>';
    svg += '<text x="' + L + '" y="' + (T + H1 + GAP - 3) + '" fill="#6F7D92" font-size="10.5">回撤 ' + (dmin * 100).toFixed(0) + '%</text>';
    // X 轴
    const step = Math.max(1, Math.floor(n / 6));
    for (let i = 0; i < n; i += step) {
      svg += '<text x="' + X(i).toFixed(1) + '" y="' + (h - 8) + '" fill="#6F7D92" font-size="10.5" text-anchor="middle">' + eq[i].d.slice(2, 4) + '/' + eq[i].d.slice(4, 6) + '</text>';
    }
    svg += '</svg>';
    return svg;
  };

  function showErr(msg) {
    var d = document.createElement('div');
    d.style.cssText = 'position:fixed;bottom:0;left:0;right:0;background:#7a1a1a;color:#fff;padding:12px;font:12px/1.5 monospace;z-index:9999;white-space:pre-wrap';
    d.textContent = 'JS ERROR: ' + msg;
    document.body && document.body.appendChild(d);
  }
  window.addEventListener('error', function (e) { showErr(e.message + ' @ ' + (e.filename||'').split('/').pop() + ':' + e.lineno); });
  window.addEventListener('unhandledrejection', function (e) { showErr('Promise: ' + (e.reason && e.reason.message || e.reason)); });

  /* ---------- 页脚数据时间戳 ---------- */
  DSH.stamp = async function () {
    const el = document.querySelector(".foot");
    if (!el) return;
    const [q, eq] = await Promise.all([DSH.json("quotes", {}), DSH.json("equity", {})]);
    const parts = [];
    if (q && q.ts_local) {
      const age = DSH.ageHours(q.ts);
      const stale = age != null && age > 36;
      parts.push("行情更新 <b style=\"color:" + (stale ? "var(--red)" : "var(--txt-2)") + "\">" +
        DSH.esc(q.ts_local) + "</b>（" + (age == null ? "—" : age < 1 ? "刚刚" : Math.floor(age) + " 小时前") + "）");
    }
    if (eq && eq.updated) parts.push("净值更新 <b style=\"color:var(--txt-2)\">" + DSH.esc(eq.updated) + "</b>");
    if (!parts.length) return;
    const line = document.createElement("div");
    line.style.cssText = "margin-bottom:10px;color:#55637A;font-size:11.5px";
    line.innerHTML = parts.join(" ｜ ") + " ｜ 每日自动更新（北京时间 20:00 前）";
    el.insertBefore(line, el.firstChild);
  };

  DSH.boot = function () {
    DSH.nav();
    DSH.paintTick();
    DSH.stamp();
  };
})(window);
