/* app.js — 共享工具：导航、数据、持仓存储、组合计算 */
(function (g) {
  "use strict";

  const NAV = [
    ["index.html", "总览"],
    ["portfolio.html", "我的持仓"],
    ["plan.html", "行动计划"],
    ["about.html", "说明"],
  ];

  const DSH = (g.DSH = {});

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
    el.innerHTML =
      (spy ? "SPY <b>" + spy.p.toFixed(2) + "</b> <span class=\"" + c + "\">" + DSH.pct(spy.chg) + "</span><br>" : "") +
      '<span style="color:#5A6779">' + DSH.esc(q.ts_local) + " ｜ USD/CNY " + (q.fx ? q.fx.toFixed(3) : "—") + "</span>";
  };

  /* ---------- 组合计算 ---------- */
  DSH.compute = function (pf, quotes, fx) {
    const rows = pf.rows.map((r) => {
      const q = quotes && quotes[r.ticker];
      const price = q ? q.p : (r.cost || 0);
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

  function showErr(msg) {
    var d = document.createElement('div');
    d.style.cssText = 'position:fixed;bottom:0;left:0;right:0;background:#7a1a1a;color:#fff;padding:12px;font:12px/1.5 monospace;z-index:9999;white-space:pre-wrap';
    d.textContent = 'JS ERROR: ' + msg;
    document.body && document.body.appendChild(d);
  }
  window.addEventListener('error', function (e) { showErr(e.message + ' @ ' + (e.filename||'').split('/').pop() + ':' + e.lineno); });
  window.addEventListener('unhandledrejection', function (e) { showErr('Promise: ' + (e.reason && e.reason.message || e.reason)); });

  DSH.boot = function () {
    DSH.nav();
    DSH.paintTick();
  };
})(window);
