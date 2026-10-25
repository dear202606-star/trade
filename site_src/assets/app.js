/* app.js — 共享工具：导航、数据读取、持仓存储、格式化 */
(function (g) {
  "use strict";

  const NAV = [
    ["index.html", "总览"],
    ["portfolio.html", "我的持仓"],
    ["plan.html", "行动计划"],
    ["signals.html", "信号看板"],
    ["analysis.html", "数据质量"],
    ["report.html", "策略报告"],
  ];

  const DSH = (g.DSH = {});

  /* ---------- 格式化 ---------- */
  DSH.money = (v, cur) => {
    if (v == null || isNaN(v)) return "—";
    const sym = cur === "CNY" ? "¥" : "$";
    const s = Math.abs(v) >= 1e4
      ? v.toLocaleString("en-US", { maximumFractionDigits: 0 })
      : v.toLocaleString("en-US", { maximumFractionDigits: 2 });
    return sym + s;
  };
  DSH.pct = (v, d) => (v == null || isNaN(v) ? "—" : (v * 100 >= 0 ? "+" : "") + (v * 100).toFixed(d == null ? 2 : d) + "%");
  DSH.cls = (v) => (v == null || isNaN(v) ? "flat" : v > 0.00005 ? "pos" : v < -0.00005 ? "neg" : "flat");
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
    return { capital: 0, cur: "USD", rows: [], updated: null };
  };
  DSH.save = function (d) {
    d.updated = new Date().toISOString();
    localStorage.setItem(KEY, JSON.stringify(d));
  };

  /* ---------- 导航 ---------- */
  DSH.nav = function () {
    const here = (location.pathname.split("/").pop() || "index.html").toLowerCase();
    const html =
      '<div class="topnav"><div class="in">' +
      '<div class="brand">LEADER<span>·</span>QUANT</div>' +
      NAV.map(([h, t]) => `<a class="navlink${h === here ? " on" : ""}" href="${h}">${t}</a>`).join("") +
      '<div class="navspacer"></div><div class="live" id="navLive">行情加载中…</div>' +
      "</div></div>";
    document.write(html);
  };

  DSH.paintLive = async function () {
    const q = await DSH.json("quotes", {});
    const el = document.getElementById("navLive");
    if (!el) return;
    if (!q || !q.ts_local) {
      el.innerHTML = "行情未就绪";
      return;
    }
    const spy = q.quotes && q.quotes.SPY;
    el.innerHTML =
      "行情 <b>" + DSH.esc(q.ts_local) + "</b><br>" +
      (spy ? "SPY " + spy.p.toFixed(2) + " " + DSH.pct(spy.chg) : "") +
      (q.fx ? " ｜ USD/CNY " + q.fx.toFixed(3) : "");
  };

  /* ---------- 计算 ---------- */
  DSH.compute = function (pf, quotes, fx) {
    const rate = pf.cur === "CNY" ? 1 : 1; // 展示币种
    const rows = pf.rows.map((r) => {
      const q = quotes && quotes[r.ticker];
      const price = q ? q.p : r.lastPrice || r.cost;
      const mv = price * r.shares;
      const cost = r.cost * r.shares;
      const pnl = mv - cost;
      return Object.assign({}, r, {
        name: q ? q.name : r.ticker,
        price, mv, cost, pnl,
        pnlPct: cost > 0 ? pnl / cost : 0,
        dayPct: q ? q.chg : 0,
        dayPnl: q ? (q.p - q.pc) * r.shares : 0,
        hasQuote: !!q,
      });
    });
    const mv = rows.reduce((a, r) => a + r.mv, 0);
    const cost = rows.reduce((a, r) => a + r.cost, 0);
    const pnl = mv - cost;
    const dayPnl = rows.reduce((a, r) => a + (r.dayPnl || 0), 0);
    // 占比统一按「总投入」为分母，才能和 30% 目标仓位直接比较
    rows.forEach((r) => (r.weight = pf.capital > 0 ? r.mv / pf.capital : (mv > 0 ? r.mv / mv : 0)));
    const cash = Math.max(0, (pf.capital || 0) - cost);
    return {
      rows, mv, cost, pnl,
      pnlPct: cost > 0 ? pnl / cost : 0,
      dayPnl,
      dayPct: mv > 0 ? dayPnl / (mv - dayPnl) : 0,
      cash,
      total: mv + cash,
    };
  };

  DSH.fx = async function () {
    const q = await DSH.json("quotes", {});
    return (q && q.fx) || 7.1;
  };

  /* 错误可视化，便于排查 */
  function showErr(msg) {
    var d = document.createElement('div');
    d.style.cssText = 'position:fixed;bottom:0;left:0;right:0;background:#7a1a1a;color:#fff;padding:10px;font:12px monospace;z-index:9999;white-space:pre-wrap';
    d.textContent = 'JS ERROR: ' + msg;
    document.body && document.body.appendChild(d);
  }
  window.addEventListener('error', function (e) { showErr(e.message + ' @ ' + (e.filename||'').split('/').pop() + ':' + e.lineno); });
  window.addEventListener('unhandledrejection', function (e) { showErr('Promise: ' + (e.reason && e.reason.message || e.reason)); });

  DSH.boot = function () {
    DSH.nav();
    DSH.paintLive();
  };
})(window);
