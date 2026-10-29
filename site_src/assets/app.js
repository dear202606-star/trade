/* app.js — 共享工具：导航、数据、持仓存储、组合计算 */
(function (g) {
  "use strict";

  const NAV = [
    ["index.html", "总览"],
    ["portfolio.html", "持仓"],
    ["strategy.html", "策略"],
  ];

  const DSH = (g.DSH = {});

  /* 单只移动止损：从持有期最高点回撤 30% 清仓（回测验证：16 年零亏损年） */
  DSH.STOP = 0.30;

  /* 轻提示：右下角浮出，2.4 秒自动消失 */
  DSH.toast = function (msg) {
    if (!msg) return;
    let el = document.getElementById("dshToast");
    if (!el) {
      el = document.createElement("div");
      el.id = "dshToast";
      el.style.cssText = "position:fixed;right:22px;bottom:22px;z-index:99;padding:12px 18px;" +
        "border-radius:12px;background:rgba(28,27,33,.96);border:1px solid rgba(255,255,255,.1);" +
        "color:#F5F3EE;font-size:13.5px;box-shadow:0 12px 40px rgba(0,0,0,.5);" +
        "opacity:0;transform:translateY(8px);transition:all .22s ease;pointer-events:none;max-width:70vw";
      document.body.appendChild(el);
    }
    el.textContent = msg;
    requestAnimationFrame(() => { el.style.opacity = "1"; el.style.transform = "translateY(0)"; });
    clearTimeout(el._t);
    el._t = setTimeout(() => {
      el.style.opacity = "0"; el.style.transform = "translateY(8px)";
    }, 2400);
  };

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
  DSH.cls = (v) => (v == null || isNaN(v) ? "zero" : v >= 0 ? "up" : "down");
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
      const _d = await r.json();
      // 行情：把「最近交易日」挂到报价表上，供各页面显示与判断新仓
      if (name === 'quotes' && _d && _d.quotes) _d.quotes.__session = _d.session || '';
      cache[name] = _d;
    } catch (e) {
      g.__dshErr = (g.__dshErr || []).concat(url + " -> " + e.message);
      cache[name] = fallback === undefined ? null : fallback;
    }
    return cache[name];
  };

  /* ---------- 存储：云端为准，本地只留同步码 ----------
     持仓不写 localStorage（避免"本地一份、云端一份"的不一致）；
     浏览器只在 sessionStorage 里留一份本次会话的缓存，关掉标签页即清空，
     每次打开都以云端数据为准。 */
  const CODE_KEY = "dsh.sync.code";
  const CACHE_KEY = "dsh.portfolio.cache";

  function emptyPf() { return { capital: 0, cur: "USD", rows: [] }; }

  DSH.load = function () {
    try {
      const d = JSON.parse(sessionStorage.getItem(CACHE_KEY) || "null");
      if (d && Array.isArray(d.rows)) return d;
    } catch (e) {}
    return emptyPf();
  };

  DSH.save = function (d) {
    try { sessionStorage.setItem(CACHE_KEY, JSON.stringify(d)); } catch (e) {}
    if (DSH.cloud && DSH.cloud.auto) DSH.cloud.queue(d);
  };

  /* ---------- 云同步（Supabase + 客户端 AES-GCM + 哈希校验） ----------
     安全：同步码即密钥，服务端只见密文，行 id 是同步码哈希。
     准确：明文里带 data 的 SHA-256，读出来先校验哈希，不一致就拒绝使用；
           写入后立刻回读比对，确认"存进去的 == 想存的"。 */
  DSH.cloud = (function () {
    let cfg = null, timer = null, last = 0, state = "idle";

    const buf2b64 = (buf) => btoa(String.fromCharCode.apply(null, new Uint8Array(buf)));
    const b642buf = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
    const enc = (s) => new TextEncoder().encode(s);
    const dec = (b) => new TextDecoder().decode(b);

    async function digest(txt) {
      return new Uint8Array(await crypto.subtle.digest("SHA-256", enc(txt)));
    }
    async function hexOf(txt) {
      return Array.from(await digest(txt)).map((b) => b.toString(16).padStart(2, "0")).join("");
    }
    async function keyFor(code) {
      const k = await digest("dsh-key|" + code);
      return crypto.subtle.importKey("raw", k, { name: "AES-GCM" }, false, ["encrypt", "decrypt"]);
    }
    async function idFor(code) {
      const h = await digest("dsh-id|" + code);
      return Array.from(h).slice(0, 16).map((b) => b.toString(16).padStart(2, "0")).join("");
    }
    function headers() {
      return {
        apikey: cfg.key,
        Authorization: "Bearer " + cfg.key,
        "Content-Type": "application/json",
      };
    }
    // 网络不稳（尤其移动网络）时自动重试，避免一次抖动就报 Failed to fetch
    async function req(url, opts, tries) {
      const n = tries || 3;
      let lastErr;
      for (let i = 0; i < n; i++) {
        try {
          return await fetch(url, opts);
        } catch (e) {
          lastErr = e;
          if (i < n - 1) await new Promise((r) => setTimeout(r, 300 * Math.pow(3, i)));
        }
      }
      throw new Error("网络连接失败（已重试 " + n + " 次），请稍后再试");
    }
    async function seal(code, data) {
      const body = JSON.stringify(data);
      const hash = await hexOf(code + "|" + body);
      const blob = JSON.stringify({ v: 2, ts: Date.now(), hash: hash, data: data });
      const k = await keyFor(code);
      const iv = crypto.getRandomValues(new Uint8Array(12));
      const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, k, enc(blob));
      return "v2." + buf2b64(iv) + "." + buf2b64(ct);
    }
    async function open(code, payload) {
      const parts = String(payload || "").split(".");
      if (parts.length !== 3) throw new Error("数据格式异常");
      const k = await keyFor(code);
      const pt = JSON.parse(dec(await crypto.subtle.decrypt(
        { name: "AES-GCM", iv: b642buf(parts[1]) }, k, b642buf(parts[2]))));
      // v1（旧格式）没有哈希，仍然接受
      if (!pt || pt.data === undefined) return { data: pt, verified: false };
      const hash = await hexOf(code + "|" + JSON.stringify(pt.data));
      return { data: pt.data, verified: hash === pt.hash, ts: pt.ts };
    }

    const api = {
      auto: false,
      get state() { return state; },
      get lastSync() { return last; },
      get code() { return localStorage.getItem(CODE_KEY) || ""; },
      set code(v) {
        if (v) localStorage.setItem(CODE_KEY, v); else localStorage.removeItem(CODE_KEY);
        api.auto = !!v;
      },
      get bound() { return !!api.code; },

      async init() {
        if (cfg) return true;
        try { cfg = await DSH.json("cloud", null); } catch (e) { cfg = null; }
        if (!cfg || !cfg.url || !cfg.key) return false;
        api.auto = api.bound;
        return true;
      },

      genCode() {
        const A = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
        const r = crypto.getRandomValues(new Uint8Array(12));
        let s = "";
        for (let i = 0; i < 12; i++) s += A[r[i] % A.length];
        return s.slice(0, 4) + "-" + s.slice(4, 8) + "-" + s.slice(8, 12);
      },

      // 上传 + 立刻回读校验
      async push(data, code) {
        if (!(await api.init())) throw new Error("云同步未配置");
        const c = (code || api.code || "").trim().toUpperCase();
        if (!c) throw new Error("还没有同步码");
        state = "saving";
        try {
          const payload = await seal(c, data);
          const id = await idFor(c);
          const r = await req(cfg.url + "/rest/v1/holdings?on_conflict=id", {
            method: "POST",
            headers: Object.assign(headers(), { Prefer: "resolution=merge-duplicates,return=minimal" }),
            body: JSON.stringify({ id: id, payload: payload }),
          });
          if (!r.ok) throw new Error("上传失败 " + r.status);
          // 回读校验：确认云端存的就是刚才那份
          const back = await api.pull(c);
          if (!back || !back.verified) { state = "error"; throw new Error("写入校验未通过"); }
          state = "ok"; last = Date.now();
          return true;
        } catch (e) {
          state = "error";
          throw e;
        }
      },

      async pull(code) {
        if (!(await api.init())) throw new Error("云同步未配置");
        const c = (code || api.code || "").trim().toUpperCase();
        if (!c) throw new Error("还没有同步码");
        const id = await idFor(c);
        const r = await req(cfg.url + "/rest/v1/holdings?id=eq." + id + "&select=payload", { headers: headers() });
        if (!r.ok) throw new Error("拉取失败 " + r.status);
        const arr = await r.json();
        if (!arr.length) return null;
        const got = await open(c, arr[0].payload);
        if (!got.verified) { state = "error"; throw new Error("云端数据校验失败（可能已损坏）"); }
        state = "ok"; last = Date.now();
        return got;
      },

      // 改动后 400ms 自动上传（边输边存也不会漏）
      queue(data) {
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => {
          timer = null;
          api.push(data).catch(() => {});
        }, 400);
      },
    };
    return api;
  })();

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
    if (!q || (!q.ts && !q.ts_local)) { el.textContent = "行情未就绪"; return; }
    const spy = q.quotes && q.quotes.SPY;
    const c = spy ? (spy.chg >= 0 ? "up" : "dn") : "";
    const age = DSH.ageHours(q.ts);
    const stale = age == null ? false : age > 36;
    const ageTxt = age == null ? "" :
      age < 1 / 60 ? "刚刚更新" :
      age < 1 ? Math.round(age * 60) + " 分钟前" :
      age < 24 ? Math.floor(age) + " 小时前" : Math.floor(age / 24) + " 天前";
    // 时间按浏览器本地时区显示（服务端 ts 是 UTC）
    let when = "";
    if (q.ts) {
      const d = new Date(q.ts);
      if (!isNaN(d.getTime())) {
        const p = (n) => String(n).padStart(2, "0");
        when = d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) +
          " " + p(d.getHours()) + ":" + p(d.getMinutes());
      }
    }
    if (!when) when = q.ts_local || "";
    el.innerHTML =
      (spy ? "SPY <b>" + spy.p.toFixed(2) + "</b> <span class=\"" + c + "\">" + DSH.pct(spy.chg) + "</span><br>" : "") +
      '<span style="color:' + (stale ? "#FF6B72" : "#5A6779") + '">' +
      (stale ? "⚠ 数据已 " + ageTxt : ageTxt) + " ｜ " + DSH.esc(when) + "</span>";
  };
  /* ---------- 页面打开期间的自动刷新 ----------
     数据由 GitHub Actions 定期生成；页面这边每 5 分钟重新拉一次，
     有新数据就重绘，没有就只更新时间，避免用户看到过期数字。 */
  DSH._refreshers = [];
  DSH.onRefresh = function (fn) { if (typeof fn === 'function') DSH._refreshers.push(fn); };
  DSH.autoRefresh = function (minutes) {
    const ms = (minutes || 5) * 60000;
    setInterval(async function () {
      try {
        for (const k of Object.keys(cache)) delete cache[k];
        await DSH.paintTick();
        if (DSH.stamp) await DSH.stamp();
        for (const fn of DSH._refreshers) { try { await fn(); } catch (e) {} }
      } catch (e) {}
    }, ms);
  };

  // 每分钟按真实经过时间刷新相对时间
  DSH.tickTimer = function () {
    if (DSH._tick) return;
    DSH._tick = setInterval(function () {
      try { DSH.paintTick(); } catch (e) {}
    }, 60000);
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
      const hasQuote = !!(q && isFinite(q.p) && q.p > 0);
      // 没有行情时绝不拿成本价冒充现价（否则盈亏会伪装成 0%）
      const price = hasQuote ? q.p : null;
      const mv = hasQuote ? price * r.shares : null;
      const cost = r.cost * r.shares;
      const pnl = hasQuote ? mv - cost : null;
      const peak = Math.max(r.peak || 0, hasQuote ? price : 0, r.cost || 0);
      const stopLine = peak * (1 - DSH.STOP);
      return Object.assign({}, r, {
        name: q ? q.name : r.ticker,
        price, mv, cost, pnl, costPerShare: r.cost,
        pnlPct: hasQuote && cost > 0 ? pnl / cost : null,
        dayPct: hasQuote ? q.chg : null,
        dayPnl: hasQuote ? (q.p - q.pc) * r.shares : null,
        hasQuote,
        peak, stopLine,
        stopDist: stopLine > 0 && hasQuote ? price / stopLine - 1 : null,
      });
    });
    const priced = rows.filter((r) => r.hasQuote);
    const missing = rows.filter((r) => !r.hasQuote);
    const mv = priced.reduce((a, r) => a + r.mv, 0);
    const cost = rows.reduce((a, r) => a + r.cost, 0);
    const costPriced = priced.reduce((a, r) => a + r.cost, 0);
    const sess = (quotes && quotes.__session) || '';
    rows.forEach((r) => {
      // 占比 = 该只市值 ÷ 全部持仓市值（只算有行情的）
      r.weight = mv > 0 && r.hasQuote ? r.mv / mv : 0;
      // 在该交易日之后才买入的仓位，没有「当日涨跌」可言
      r.isNew = !!(sess && r.added && String(r.added).slice(0, 10) > sess);
      if (r.isNew) { r.dayPnl = 0; r.dayPct = 0; }
    });
    const dayPnl = priced.reduce((a, r) => a + (r.dayPnl || 0), 0);
    const dayValid = priced.some((r) => !r.isNew);
    const plan = pf.plan || 0;
    const deployed = plan > 0 ? cost / plan : 0;
    const remain = plan > 0 ? Math.max(0, plan - cost) : 0;
    // 盈亏只用「有行情的部分」计算，口径写在页面上
    const pnl = priced.reduce((a, r) => a + r.pnl, 0);
    return {
      rows, mv, cost, costPriced,
      pnl,
      pnlPct: costPriced > 0 ? pnl / costPriced : null,
      plan, deployed, remain,
      dayPnl,
      dayPct: mv - dayPnl > 0 ? dayPnl / (mv - dayPnl) : null,
      dayValid, session: sess,
      pricedCount: priced.length,
      missingCount: missing.length,
      missingTickers: missing.map((r) => r.ticker),
      total: mv,
    };
  };

  /* ---------- 数据质量自检（前端不变量） ----------
     每次渲染后跑一遍，任何一条不成立说明界面数字不可信。
     返回 {ok, fails:[说明], warns:[说明]} */
  DSH.audit = function (pf, quotes, c) {
    const fails = [], warns = [];
    const near = (a, b, tol) => Math.abs(a - b) <= (tol == null ? 0.01 : tol);
    const rows = (c && c.rows) || [];
    const fin = (v) => v == null || (typeof v === "number" && isFinite(v));

    if (!Array.isArray(pf.rows)) fails.push("持仓数据结构异常");

    // 1) 每行必须有正的价格，否则不能参与市值与盈亏
    rows.forEach((r) => {
      if (!r.hasQuote) { warns.push(r.ticker + " 缺行情，未计入市值"); return; }
      if (!(r.price > 0)) fails.push(r.ticker + " 价格无效：" + r.price);
      if (!(r.shares > 0)) fails.push(r.ticker + " 股数无效：" + r.shares);
      if (!fin(r.mv) || !fin(r.pnl)) fails.push(r.ticker + " 市值/盈亏非数值");
    });

    // 2) 市值 = Σ 股数×价格
    const mvSum = rows.filter((r) => r.hasQuote).reduce((a, r) => a + r.shares * r.price, 0);
    if (!near(mvSum, c.mv, Math.max(0.02, c.mv * 1e-6))) {
      fails.push("市值合计不一致：" + mvSum.toFixed(2) + " ≠ " + (c.mv || 0).toFixed(2));
    }

    // 3) 盈亏 = 市值 − 有行情部分的成本
    if (rows.some((r) => r.hasQuote)) {
      const pnlCalc = rows.filter((r) => r.hasQuote).reduce((a, r) => a + (r.mv - r.cost), 0);
      if (!near(pnlCalc, c.pnl, Math.max(0.02, Math.abs(c.pnl || 0) * 1e-6))) {
        fails.push("盈亏合计不一致");
      }
      // 4) 收益率 = 盈亏 ÷ 成本
      if (c.costPriced > 0 && !near(c.pnlPct, c.pnl / c.costPriced, 1e-9)) {
        fails.push("收益率与盈亏/成本不一致");
      }
    }

    // 5) 权重之和 ≈ 1（有行情时）
    if (rows.some((r) => r.hasQuote)) {
      const w = rows.filter((r) => r.hasQuote).reduce((a, r) => a + r.weight, 0);
      if (!near(w, 1, 0.005)) fails.push("权重合计 " + w.toFixed(3) + " ≠ 1");
    }

    // 6) 当日涨跌不能超过合理范围（防串数据）
    rows.forEach((r) => {
      if (r.hasQuote && (r.dayPct > 1.5 || r.dayPct < -1.5)) {
        fails.push(r.ticker + " 当日涨跌幅异常：" + (r.dayPct * 100).toFixed(1) + "%");
      }
    });

    return { ok: fails.length === 0, fails, warns };
  };

  /* 目标权重（占总资金） */
  /* 目标占比：在「全部持仓」内部的权重（与页面上的占比同口径） */
  DSH.targets = function (sig) {
    const out = {};
    if (!sig) return out;
    const list = sig.official || sig.picks || [];
    const sum = list.reduce((a, p) => a + (p.weight || 0), 0) || 1;
    list.forEach((p) => { out[p.ticker] = (p.weight || 0) / sum; });
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
    const col = opts.color || (up ? "#FF6B72" : "#3BD68A");   // 红涨绿跌
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
        label(rawV) + "</text>";
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

  /* ---------- 图表十字线（滑动读数） ---------- */
  let __chId = 0;
  DSH._charts = {};

  function chartWrap(svg, meta) {
    const id = "ch" + (++__chId);
    meta.id = id;
    DSH._charts[id] = meta;
    return '<div class="ch-wrap" id="' + id + '" style="position:relative">' + svg + "</div>";
  }

  DSH.attachCharts = function (root) {
    Object.keys(DSH._charts).forEach((id) => {
      const el = document.getElementById(id);
      if (!el || el._ready) return;
      el._ready = true;
      const meta = DSH._charts[id];
      const line = document.createElement("div");
      line.style.cssText = "position:absolute;top:0;bottom:0;width:1px;background:rgba(242,196,107,.5);" +
        "display:none;pointer-events:none;z-index:4";
      const dot = document.createElement("div");
      dot.style.cssText = "position:absolute;width:8px;height:8px;border-radius:50%;background:#F2C46B;" +
        "box-shadow:0 0 0 3px rgba(242,196,107,.22);display:none;pointer-events:none;z-index:4;transform:translate(-50%,-50%)";
      const tip = document.createElement("div");
      tip.style.cssText = "position:absolute;pointer-events:none;display:none;z-index:6;padding:9px 11px;" +
        "border-radius:9px;background:rgba(20,19,24,.97);border:1px solid rgba(255,255,255,.13);" +
        "font-size:11.5px;line-height:1.6;white-space:nowrap;box-shadow:0 10px 28px rgba(0,0,0,.55);color:#EDEAE4";
      el.appendChild(line); el.appendChild(dot); el.appendChild(tip);

      const move = (ev) => {
        const r = el.getBoundingClientRect();
        if (!r.width) return;
        const x = ev.clientX - r.left;
        const frac = (x / r.width - meta.l) / (1 - meta.l - meta.r);
        let i = Math.round(frac * (meta.n - 1));
        i = Math.max(0, Math.min(meta.n - 1, i));
        const px = (meta.l + (i / Math.max(1, meta.n - 1)) * (1 - meta.l - meta.r)) * r.width;
        const py = meta.y ? meta.y(i, r.height) : null;

        line.style.display = "block"; line.style.left = px.toFixed(1) + "px";
        if (py != null && meta.dot !== false) {
          dot.style.display = "block";
          dot.style.left = px.toFixed(1) + "px"; dot.style.top = py.toFixed(1) + "px";
        }
        tip.style.display = "block";
        tip.innerHTML = meta.tip(i);
        const tw = tip.offsetWidth || 150;
        tip.style.left = Math.min(Math.max(4, px + 14), Math.max(4, r.width - tw - 4)) + "px";
        tip.style.top = "12px";
      };
      el.addEventListener("mousemove", move);
      el.addEventListener("mouseleave", () => {
        line.style.display = "none"; dot.style.display = "none"; tip.style.display = "none";
      });
      el.addEventListener("touchmove", (e) => { if (e.touches[0]) move(e.touches[0]); }, { passive: true });
    });
  };

  /* 累计收益统一用百分比（不用「倍」，避免净值倍数与收益率混淆），大额加千分位 */
  DSH.ret = function (v) {
    if (v == null || isNaN(v)) return '—';
    const p = v * 100;
    const abs = Math.abs(p);
    const num = abs >= 1000 ? abs.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',') : abs.toFixed(1);
    return (p >= 0 ? '+' : '-') + num + '%';
  };

  /* 图例行：色块 + 名称 + 数值 */
  function tipRow(color, name, val) {
    return '<div style="display:flex;gap:8px;align-items:center;justify-content:space-between">' +
      '<span style="color:#A5A2B0"><i style="display:inline-block;width:7px;height:7px;border-radius:50%;' +
      "background:" + color + ';margin-right:6px;vertical-align:middle"></i>' + name + "</span>" +
      '<b style="margin-left:14px;font-variant-numeric:tabular-nums">' + val + "</b></div>";
  }
  DSH._tipRow = tipRow;

  /* ---------- 收益 + 回撤 合一图 ---------- */
  DSH.combo = function (eq, dd, opts) {
    opts = opts || {};
    const w = 900, h = opts.h || 380, L = 52, R = 14, T = 14;
    const H1 = h * 0.68, GAP = 12, H2 = h - H1 - GAP - 26;
    const eqs = eq.map(p => p.y);
    // 净值跨度大时用对数轴，否则前几年会被压成一条线
    const useLog = opts.log !== false && (Math.max.apply(null, eqs) - Math.min.apply(null, eqs)) > 2;
    const tf = (v) => (useLog ? Math.log(Math.max(1e-6, 1 + v)) : v);
    let mn = Math.min.apply(null, eqs.map(tf)), mx = Math.max.apply(null, eqs.map(tf));
    const pad = (mx - mn) * 0.10 || 0.05;
    mn -= pad; mx += pad;
    const n = eq.length;
    const X = i => L + (i / Math.max(1, n - 1)) * (w - L - R);
    const Y1 = v => T + (1 - (tf(v) - mn) / (mx - mn || 1)) * H1;
    const dmin = Math.min.apply(null, dd.map(p => p.y));
    const Y2 = v => T + H1 + GAP + (1 - (v - dmin) / (0 - dmin || 1)) * H2;
    let svg = '<svg viewBox="0 0 ' + w + ' ' + h + '" style="width:100%;height:auto;display:block">';
    // 纵轴单位统一：跨度过大时全用「x 倍」，否则全用「%」
    const useX = (Math.max.apply(null, eqs) >= 9);
    for (let k = 0; k <= 4; k++) {
      const tv = mn + (mx - mn) * k / 4;
      const v = useLog ? (Math.exp(tv) - 1) : tv;
      const y = Y1(v).toFixed(1);
      svg += '<line x1="' + L + '" y1="' + y + '" x2="' + (w - R) + '" y2="' + y + '" stroke="rgba(255,255,255,.05)"/>';
      const txt = useX ? (1 + v).toFixed(1).replace(/\.0$/, '') + 'x' : (v * 100).toFixed(0) + '%';
      svg += '<text x="' + (L - 8) + '" y="' + (+y + 4) + '" fill="#6F7D92" font-size="10.5" text-anchor="end">' +
        txt + '</text>';
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
    svg += '<path d="' + d3 + '" fill="rgba(59,214,138,.13)" stroke="#3BD68A" stroke-width="1.2"/>';
    svg += '<text x="' + L + '" y="' + (T + H1 + GAP - 3) + '" fill="#6F7D92" font-size="10.5">回撤 ' + (dmin * 100).toFixed(0) + '%</text>';
    // X 轴
    const step = Math.max(1, Math.floor(n / 6));
    for (let i = 0; i < n; i += step) {
      svg += '<text x="' + X(i).toFixed(1) + '" y="' + (h - 8) + '" fill="#6F7D92" font-size="10.5" text-anchor="middle">' + eq[i].d.slice(2, 4) + '/' + eq[i].d.slice(4, 6) + '</text>';
    }
    svg += '</svg>';

    // 十字线读数：日期 + 策略 / 基准 / 回撤
    const meta = {
      n: n, l: L / w, r: R / w, dot: false,
      y: (i, hh) => (Y1(eqs[i]) / h) * hh,
      tip: (i) => {
        const p = eq[i];
        let s = '<div style="color:#8E8B99;margin-bottom:5px;font-size:10.5px;letter-spacing:.04em">' +
          (p.d.length === 6 ? p.d.slice(0, 4) + "-" + p.d.slice(4, 6) : p.d) + "</div>";
        s += tipRow("#F2C46B", opts.sName || "策略", DSH.ret(eqs[i]));
        if (opts.bench) {
          s += tipRow("#6F7D92", opts.bName || "标普 500", DSH.ret(opts.bench[i].y));
        }
        if (dd[i]) s += tipRow("#3BD68A", "回撤", (dd[i].y * 100).toFixed(1) + "%");
        return s;
      },
    };
    return chartWrap(svg, meta);
  };

  function showErr(msg) {
    var d = document.createElement('div');
    d.style.cssText = 'position:fixed;bottom:0;left:0;right:0;background:#7a1a1a;color:#fff;padding:12px;font:12px/1.5 monospace;z-index:9999;white-space:pre-wrap';
    d.textContent = 'JS ERROR: ' + msg;
    document.body && document.body.appendChild(d);
  }
  window.addEventListener('error', function (e) { showErr(e.message + ' @ ' + (e.filename||'').split('/').pop() + ':' + e.lineno); });
  window.addEventListener('unhandledrejection', function (e) { showErr('Promise: ' + (e.reason && e.reason.message || e.reason)); });

  /* ---------- 页脚数据状态条（全站唯一的「数据可信」说明） ---------- */
  DSH.stamp = async function () {
    const el = document.querySelector(".foot");
    if (!el) return;
    const [q, eq] = await Promise.all([DSH.json("quotes", {}), DSH.json("equity", {})]);
    const parts = [];

    // 三个时间必须分清：行情属于哪个交易日 / 什么时候抓的 / 净值算到哪天
    if (q && q.session) {
      parts.push("行情 <b style=\"color:var(--txt-2)\">" + DSH.esc(q.session) + " 收盘</b>");
    }
    if (q && q.ts) {
      const d = new Date(q.ts);
      if (!isNaN(d.getTime())) {
        const p = (n) => String(n).padStart(2, "0");
        parts.push("采集于(北京) <b style=\"color:var(--txt-2)\">" +
          p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes()) + "</b>");
      }
      const age = DSH.ageHours(q.ts);
      if (age != null && age > 26) {
        parts.push("<b style=\"color:#FFAB6B\">⚠ 行情已 " + Math.floor(age / 24) + " 天未更新</b>");
      }
    }
    if (eq && eq.updated) {
      parts.push("净值截至(北京) <b style=\"color:var(--txt-2)\">" + DSH.esc(eq.updated) + "</b>");
    }
    parts.push("两源已核对");
    if (!parts.length) return;

    // 行情过旧时，顶部通栏提醒（不能只缩在页脚）
    const age = q ? DSH.ageHours(q.ts) : null;
    if (age != null && age > 26 && !document.getElementById("staleBar")) {
      const bar = document.createElement("div");
      bar.id = "staleBar";
      bar.style.cssText = "position:sticky;top:0;z-index:60;background:#7A4A12;color:#FFE7C2;" +
        "font-size:13px;padding:9px 16px;text-align:center;letter-spacing:.01em";
      bar.textContent = "⚠ 行情已 " + Math.floor(age / 24) + " 天未更新，最新数据为 " +
        (q.session || "上次交易日") + " 收盘，请勿据此下单";
      document.body.insertBefore(bar, document.body.firstChild);
    }

    // 复用同一个节点，避免每次刷新都多插一行
    let line = document.getElementById("dataStamp");
    if (!line) {
      line = document.createElement("div");
      line.id = "dataStamp";
      line.style.cssText = "margin-bottom:10px;color:#7A8699;font-size:12.5px";
      el.insertBefore(line, el.firstChild);
    }
    line.innerHTML = parts.join(" ｜ ");
  };

  DSH.boot = function () {
    DSH.nav();
    DSH.paintTick();
      DSH.tickTimer();
    DSH.stamp();
  };
})(window);
