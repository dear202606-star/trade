/* app.js — 共享工具：导航、数据、持仓存储、组合计算 */
(function (g) {
  "use strict";

  const NAV = [
    ["index.html", "总览"],
    ["portfolio.html", "持仓"],
    ["strategy.html", "策略"],
  ];

  const DSH = (g.DSH = {});

  DSH.quoteFreshness = function (q, now) {
    const source = Date.parse(q && q.ts), limit = Date.parse(q && q.fresh_until);
    now = now == null ? Date.now() : now;
    if (!Number.isFinite(source) || !Number.isFinite(limit) || limit <= source || !Number.isFinite(now)) return null;
    return now >= limit;
  };

  /* 展示层专用：横幅只看「采集是否真的停了」，不用数据源刷新周期当闹钟。
     fresh_until 是行情源 20 分钟的时效口径（业务判断、净值依赖校验都靠它，
     不能改），而生产采集是盘中 30 分钟一次。若直接拿 fresh_until 判横幅，
     每轮采集后的 14:53~15:03 这类窗口必然短暂弹出「行情更新超时」，
     数据其实完全正常。盘中快照额外给 45 分钟（30 分钟节奏 + 15 分钟
     GitHub 排队容错）；收盘版仍按 fresh_until 严格判定，不掩盖真停更。 */
  DSH.quoteStaleForDisplay = function (q, now) {
    const at = now == null ? Date.now() : now;
    if (DSH.quoteFreshness(q, at) !== true) return false;
    if (!q || q.intraday !== true) return true;
    const limit = Date.parse(q.fresh_until), grace = 45 * 60 * 1000;
    return !(Number.isFinite(limit) && Number.isFinite(at) && at < limit + grace);
  };

  DSH.sessionStamp = function (session, asOf, intraday) {
    const day = /^\d{4}-\d{2}-\d{2}$/.test(session || '') ? "美股 " + session.slice(5).replace('-', '/') : "";
    const time = Date.parse(asOf);
    if (!Number.isFinite(time)) return day;
    const parts = new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
    }).formatToParts(time);
    const fields = Object.fromEntries(parts.map(part => [part.type, part.value]));
    const phase = intraday === true ? '盘中' : intraday === false ? '收盘' : '';
    return [day, phase, `北京 ${fields.month}/${fields.day} ${fields.hour}:${fields.minute}`].filter(Boolean).join(' · ');
  };

  DSH.snapshotIntraday = function (preview, quotes) {
    if (typeof preview.intraday === 'boolean') return preview.intraday;
    if (quotes && preview.quote_version === quotes.version) return quotes.intraday;
    // Previously published previews on this release line were closing-only.
    return false;
  };

  DSH.previewVerified = function (preview) {
    return !!(preview && preview.quote_version && preview.reference_version &&
      /^\d{4}-\d{2}-\d{2}$/.test(preview.session || '') && Number.isFinite(Date.parse(preview.as_of_ts)) &&
      Date.parse(preview.as_of_ts) <= Date.now() && Array.isArray(preview.top) && preview.top.length === 3 &&
      preview.top.every(row => row && row.ticker && Number.isFinite(row.ytd)));
  };

  DSH.lastVerifiedDay = function (equity) {
    const day = equity && equity.today;
    const amount = +(localStorage.getItem("dsh.amt") || 100000);
    if (!equity || equity.runtime_verified !== true || !day || !equity.quote_version ||
        day.quote_version !== equity.quote_version || !/^\d{4}-\d{2}-\d{2}$/.test(day.session || '') ||
        !Number.isFinite(Date.parse(equity.source_as_of)) || Date.parse(equity.source_as_of) > Date.now() ||
        !Number.isFinite(day.pct) || day.pct <= -1 || !Number.isFinite(day.per_dollar) ||
        !Number.isFinite(amount) || amount <= 0) return null;
    return {amount: amount * day.per_dollar, pct: day.pct, session: day.session,
      sourceAsOf: equity.source_as_of, live: equity.quote_intraday === true};
  };

  DSH.previewCurrent = function (preview, quotes) {
    return !!(preview && quotes && preview.quote_version && quotes.version &&
      (preview.quote_version === quotes.version ||
        (Date.parse(preview.as_of_ts) <= Date.parse(quotes.ts) &&
         DSH.quoteFreshness({ts:preview.as_of_ts,fresh_until:preview.fresh_until}) === false)) &&
      preview.session === quotes.session &&
      preview.reference_version && Array.isArray(preview.top) && preview.top.length === 3 &&
      DSH.quoteFreshness(quotes) === false);
  };

  DSH.marketAdvice = function (equity, quotes) {
    const basis=equity && equity.market_advice, spy=quotes && quotes.quotes && quotes.quotes.SPY;
    const current = quotes && (quotes.intraday === false
      ? equity && equity.quote_version === quotes.version &&
        Number.isFinite(Date.parse(quotes.ts)) && !DSH.closingQuoteOverdue(quotes)
      : DSH.quoteFreshness(quotes) === false);
    if (!basis || !spy || !current || !Number.isFinite(spy.p) || spy.p<=0 ||
        !/^\d{4}-(0[1-9]|1[0-2])$/.test(basis.month) ||
        basis.month!==String(quotes.session || '').slice(0,7) || basis.window!==10 ||
        !basis.calendar_sha256 || basis.calendar_sha256!==quotes.calendar_sha256 ||
        !Array.isArray(basis.closes) || basis.closes.length!==9) return null;
    const date=new Date(basis.month+'-01T00:00:00Z');
    let sum=0;
    for (let i=8;i>=0;i--) {
      date.setUTCMonth(date.getUTCMonth()-1);
      const row=basis.closes[i];
      if (!row || String(row.date).slice(0,7)!==date.toISOString().slice(0,7) ||
          !Number.isFinite(row.price) || row.price<=0) return null;
      sum+=row.price;
    }
    const ma=(sum+spy.p)/10;
    return {spy:spy.p,ma:ma,trend:spy.p>=ma?'满仓':'半仓'};
  };

  DSH.todayUnavailable = function (quotes, equity) {
    if (!quotes || !quotes.quotes || !quotes.ts) return "行情读取失败";
    if (DSH.quoteFreshness(quotes) !== false) return "行情时效待核验";
    if (equity && equity.runtime_verified && equity.quote_version !== quotes.version) return "收益计算更新中";
    const settled = String(equity && equity.last_session || '').replace(/\D/g, '');
    const session = String(quotes.session || '').replace(/\D/g, '');
    const previous = String(quotes.previous_session || '').replace(/\D/g, '');
    if (!settled || (settled !== session && settled !== previous)) return "收益基准待更新";
    return "持仓报价或价格口径待核验";
  };

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

  /* ---------- 数据层：多在线端点（一主一备）+ 版本探测 ----------
     2026-11-08 改造：**不再使用任何本地文件形态的镜像**。页面按 cloud.json
     的 endpoints 有序数组访问在线数据库：
       [0] Supabase PostgREST（主，publishable key）
       [1] Turso libsql HTTP（备，只读 token，site_dataset 单表）
     每个端点独立熔断 10 分钟；成功后记住最优端点，后续请求优先走它；
     主库失败自动切备库，页面服务不中断。全部端点不可用时才退回
     localStorage 最近一次成功值（浏览器内存态，不是仓库里的静态文件）。

     按数据实际变更频率分三层（与 data_lifecycle.py 策略一致）：
       L0 STATIC    name_zh / sectors / curve / strategy_history / health  TTL 24h
       L1 DAILY     equity / signal / preview（含 B 口径）              TTL 30min
       L2 INTRADAY  quotes       交易时段 60s，非交易时段 30min

     版本探测每 60 秒（交易时段）/ 10 分钟（其余）只发 <1KB 请求，
     版本变化才精确失效并重绘；探测失败只记录、不熔断。
  */
  const cache = {};                 // name -> payload
  const cacheTs = {};               // name -> 本次成功取数的时间戳
  const inflight = {};              // 同名并发请求合并
  const degraded = {};              // 当前使用兜底数据的 key
  const degradedAt = {};            // key -> 最近一次退回兜底值的时间
  const LS_PREFIX = "dsh.data.v2.";
  const LS_ENDPOINT = "dsh.endpoint.v1.";
  const LS_CIRCUIT = "dsh.circuit.v1.";    // 跨刷新保留主库额度熔断，见 failEndpoint
  const CIRCUIT_MS = 10 * 60 * 1000;       // 单端点独立熔断
  const GLOBAL_COOLDOWN_MS = 60 * 1000;    // 全端点失败后的重试冷却
  const endpointHealth = {};        // id -> {circuitUntil, failures, lastOk}
  let circuitUntil = 0;
  let lastOnlineOkAt = 0;           // 最近一次端点成功返回的时间（判定链路是否健康）
  let preferredKey = "";
  let activeType = "supabase", activeUrl = "";
  let versionStamp = "";            // 最近一次探测到的数据库版本指纹
  let preferClosingDataset = false;
  DSH.useClosingDataset = function () { preferClosingDataset = true; };
  let exchangeCalendar = null;      // 公开 cloud.json 里的已复核 NYSE 日历（可选）

  const STATIC_KEYS = ["curve", "b/curve", "strategy_history", "b/strategy_history",
                       "name_zh", "sectors", "health"];
  const DAILY_KEYS = ["equity", "b/equity", "signal", "b/signal", "preview", "b/preview"];
  const TIER_STATIC = 0, TIER_DAILY = 1, TIER_INTRADAY = 2;

  function tierOf(name) {
    if (STATIC_KEYS.indexOf(name) >= 0) return TIER_STATIC;
    if (name === "quotes") return TIER_INTRADAY;
    if (DAILY_KEYS.indexOf(name) >= 0) return TIER_DAILY;
    return TIER_STATIC;            // 未知 key 用最长 TTL，避免打爆网关
  }

  /* 2026-11-08：与交易时段门禁共用同一份已复核 NYSE 日历。
     cloud.json 带 calendar 时按美东时间判定，能识别：
       全日休市（感恩节、Good Friday…）/ 半日 13:00 收盘 / DST 切换。
     联邦法定假日 ≠ NYSE 休市日，所以绝不在前端另算一套「节假日」。
     日历缺失、超出覆盖范围或 Intl 不可用时一律 fail-open 回退旧 UTC 逻辑，
     保证日历问题永远不会让页面停止刷新。 */
  function legacyTradingHours(d) {
    const w = d.getUTCDay(), h = d.getUTCHours();
    return w >= 1 && w <= 5 && h >= 13 && h < 22;
  }

  function easternParts(d) {
    const f = new Intl.DateTimeFormat("en-CA", {
      timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23"
    });
    const p = {};
    for (const x of f.formatToParts(d)) p[x.type] = x.value;
    return { date: p.year + "-" + p.month + "-" + p.day,
             minutes: Number(p.hour) * 60 + Number(p.minute) };
  }

  function isTradingHours(now) {
    const d = now || new Date();
    const c = exchangeCalendar;
    if (!c || !Array.isArray(c.closed) || !c.coverage_from || !c.coverage_to ||
        !c.regular_open || !c.regular_close) return legacyTradingHours(d);
    try {
      const et = easternParts(d);
      if (et.date < c.coverage_from || et.date > c.coverage_to)
        return legacyTradingHours(d);
      const weekday = new Date(et.date + "T12:00:00Z").getUTCDay();
      if (weekday === 0 || weekday === 6 || c.closed.indexOf(et.date) >= 0) return false;
      const oh = Number(c.regular_open.slice(0, 2)), om = Number(c.regular_open.slice(3));
      const closeText = (c.early_close && c.early_close[et.date]) || c.regular_close;
      const ch = Number(closeText.slice(0, 2)), cm = Number(closeText.slice(3));
      // 收盘后 20 分钟仍算交易窗口，与服务端 refresh_due 口径一致
      return et.minutes >= oh * 60 + om && et.minutes <= ch * 60 + cm + 20;
    } catch (e) { return legacyTradingHours(d); }
  }
  DSH.setExchangeCalendar = function (cal) { exchangeCalendar = cal; };

  function closeMinutes(day) {
    const weekday = new Date(day + "T12:00:00Z").getUTCDay();
    const c = exchangeCalendar;
    const covered = c && day >= c.coverage_from && day <= c.coverage_to;
    if (weekday === 0 || weekday === 6 || (covered && (c.closed || []).includes(day))) return null;
    const stamp = (covered && ((c.early_close || {})[day] || c.regular_close)) || "16:00";
    return Number(stamp.slice(0, 2)) * 60 + Number(stamp.slice(3));
  }

  function latestClose(now, grace) {
    const et = easternParts(now);
    const d = new Date(et.date + "T12:00:00Z");
    for (let days = 0; days < 14; days++, d.setUTCDate(d.getUTCDate() - 1)) {
      const date = d.toISOString().slice(0, 10);
      const close = closeMinutes(date);
      if (close != null && (days || et.minutes >= close + grace))
        return { date, elapsed: days * 1440 + et.minutes - close };
    }
    return null;
  }

  DSH.inClosingRefreshWindow = function (now) {
    const close = latestClose(now || new Date(), 45);
    return !!close && close.elapsed <= 8 * 60;
  };

  DSH.closingQuoteOverdue = function (quotes, now) {
    const close = latestClose(now || new Date(), 90);
    return !!close && (!quotes || quotes.intraday === true ||
      String(quotes.session || "") < close.date);
  };

  function ttlOf(name) {
    return (tierOf(name) === TIER_STATIC ? 24 * 60 : 15) * 60 * 1000;
  }
  DSH.ttlOf = ttlOf;
  DSH.tierOf = tierOf;
  DSH.isTradingHours = isTradingHours;

  function lsGet(name) {
    try {
      const raw = localStorage.getItem(LS_PREFIX + name);
      if (!raw) return null;
      const o = JSON.parse(raw);
      return o && o.payload != null ? o : null;
    } catch (e) { return null; }
  }
  function lsSet(name, payload) {
    try { localStorage.setItem(LS_PREFIX + name,
                               JSON.stringify({ ts: Date.now(), payload: payload })); }
    catch (e) { /* 隐私模式 / 配额满：降级为纯内存缓存，不影响主流程 */ }
  }
  function noteErr(url, e) {
    g.__dshErr = (g.__dshErr || []).concat(url + " -> " + e.message).slice(-20);
  }

  /* ---------- 端点注册与独立熔断 ----------
     cloud.json 既可以是新格式 {url,key,endpoints:[...]}，也可以只有
     {url,key}（老部署 / 单库场景），后者自动退化成一个 supabase 端点。 */
  function endpointId(ep) { return (ep.type || "supabase") + "|" + ep.url; }

  function endpointList(cfg) {
    if (cfg && Array.isArray(cfg.endpoints) && cfg.endpoints.length) return cfg.endpoints;
    return cfg && cfg.url && cfg.key
      ? [{ type: "supabase", url: cfg.url, key: cfg.key }] : [];
  }

  function healthOf(ep) {
    const id = endpointId(ep);
    if (!endpointHealth[id]) {
      /* 主库免费额度耗尽（402）时，熔断必须跨页面刷新保留；否则每次打开
         页面都会先撞一次 402 再降级，既浪费 egress 又拖慢首屏。 */
      let until = 0;
      try {
        until = Number(localStorage.getItem(LS_CIRCUIT + id)) || 0;
        if (until && until <= Date.now()) {
          localStorage.removeItem(LS_CIRCUIT + id);
          until = 0;
        } else if (until > Date.now() + CIRCUIT_MS) {
          until = 0;               // 拒绝被篡改的超长熔断
        }
      } catch (e) { until = 0; }
      endpointHealth[id] = { circuitUntil: until, failures: 0, lastOk: 0 };
    }
    return endpointHealth[id];
  }

  function orderedEndpoints(eps, closing = false) {
    const now = Date.now();
    const healthy = eps.filter(function (ep) {
      return ep && ep.url && ep.key && healthOf(ep).circuitUntil <= now;
    });
    if (!healthy.length) return [];
    const pref = (closing && healthy.find(ep => ep.type === "turso")) ||
      healthy.find(ep => endpointId(ep) === preferredKey);
    return pref ? [pref].concat(healthy.filter(function (ep) { return ep !== pref; })) : healthy;
  }

  function rememberEndpoint(ep) {
    preferredKey = endpointId(ep);
    activeType = ep.type || "supabase";
    activeUrl = ep.url;
    try { localStorage.setItem(LS_ENDPOINT, preferredKey); } catch (e) {}
  }

  function failEndpoint(ep, err) {
    const h = healthOf(ep);
    h.failures++;
    /* 401/403 属凭据轮换，清配置重载即可，不熔断数据库 */
    if (!(err && (err.status === 401 || err.status === 403))) {
      h.circuitUntil = Date.now() + CIRCUIT_MS;
      try { localStorage.setItem(LS_CIRCUIT + endpointId(ep), String(h.circuitUntil)); }
      catch (e) { /* 隐私模式：退化为本次会话内熔断 */ }
    }
  }

  async function loadCloud(fallback) {
    if (cache.cloud !== undefined) return cache.cloud;
    const url = "data/cloud.json?t=" + Date.now();
    try {
      const r = await fetch(url, { cache: "no-store" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const d = await r.json();
      const eps = endpointList(d);
      const valid = d && eps.length && eps.every(function (ep) {
        return ep && typeof ep.url === "string" && /^https:\/\//.test(ep.url) &&
               typeof ep.key === "string" && ep.key;
      });
      if (!valid) throw new Error("Invalid online configuration");
      cache.cloud = d;
      if (d.calendar && Array.isArray(d.calendar.closed)) exchangeCalendar = d.calendar;
      try {
        const saved = localStorage.getItem(LS_ENDPOINT);
        if (saved) preferredKey = saved;
      } catch (e) {}
    } catch (e) {
      noteErr(url, e);
      return fallback === undefined ? null : fallback;
    }
    return cache.cloud;
  }

  /* Supabase PostgREST 端点：静态层走 v_site_dataset，日内层走专用视图 */
  async function fetchSupabase(name, ep) {
    let url;
    const runtime = /^(b\/)?(equity|preview|signal)$/.exec(name);
    if (name === "quotes" || runtime) {
      if (runtime) {
        const variant = runtime[1] ? "B" : "A", kind = runtime[2];
        url = ep.url + (kind === "signal"
          ? "/rest/v1/live_strategy_anchor?variant=eq." + variant +
            "&select=payload&order=hold_year.desc,revision.desc&limit=1"
          : "/rest/v1/v_live_strategy_results?variant=eq." + variant +
            "&kind=eq." + kind + "&select=payload");
      } else url = ep.url + "/rest/v1/v_live_quote_display?select=payload";
    } else {
      url = ep.url + "/rest/v1/v_site_dataset?key=eq." +
            encodeURIComponent(name).replace(/%2F/gi, "/") + "&select=payload";
    }
    const r = await fetch(url, { cache: "no-store", headers: { apikey: ep.key } });
    if (!r.ok) {
      const e = new Error("HTTP " + r.status);
      e.status = r.status;
      throw e;
    }
    let d = await r.json();
    if (name === "quotes") {
      if (!Array.isArray(d) || d.length !== 1 || !d[0].payload ||
          !d[0].payload.ok || !d[0].payload.quotes || !d[0].payload.quotes.SPY)
        throw new Error("Online quote snapshot unavailable");
      d = d[0].payload;
    } else if (runtime) {
      if (!Array.isArray(d) || d.length !== 1 || !d[0].payload)
        throw new Error("Online strategy result unavailable");
      d = runtime[2] === "signal" ? d[0].payload.signal : d[0].payload;
      if (!d) throw new Error("Online strategy payload unavailable");
    } else {
      if (!Array.isArray(d) || d.length !== 1 || d[0].payload == null)
        throw new Error("Online dataset unavailable: " + name);
      d = d[0].payload;
    }
    return d;
  }

  /* Turso libsql HTTP 端点：14 个展示 key 统一落在 site_dataset(key,payload)。
     与前端消费模型完全对齐，备库不需要复刻任何 PostgREST 视图。 */
  async function fetchTurso(name, ep) {
    const body = { baton: null, requests: [
      { type: "execute", stmt: {
        sql: "select payload from site_dataset where key = ?",
        args: [{ type: "text", value: name }] } },
      { type: "close" }
    ] };
    const r = await fetch(ep.url + "/v2/pipeline", {
      method: "POST", cache: "no-store",
      headers: { Authorization: "Bearer " + ep.key, "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    if (!r.ok) { const e = new Error("HTTP " + r.status); e.status = r.status; throw e; }
    const raw = await r.json();
    const item = (raw && raw.results || [])[0];
    if (!item || item.type !== "ok") throw new Error("Backup query failed: " + name);
    const rows = ((item.response || {}).result || {}).rows || [];
    if (rows.length !== 1 || !rows[0][0] || typeof rows[0][0].value !== "string")
      throw new Error("Backup dataset unavailable: " + name);
    let d;
    try { d = JSON.parse(rows[0][0].value); }
    catch (e2) { throw new Error("Backup payload invalid: " + name); }
    if (name === "quotes" && (!d.ok || !d.quotes || !d.quotes.SPY))
      throw new Error("Backup quote snapshot unavailable");
    return d;
  }

  async function fetchFromEndpoint(name, ep) {
    const d = (ep.type === "turso") ? await fetchTurso(name, ep) : await fetchSupabase(name, ep);
    if (name === "quotes" && d && d.quotes) d.quotes.__session = d.session || "";
    return d;
  }

  function fallbackValue(name, fallback) {
    degraded[name] = true;
    degradedAt[name] = Date.now();
    const ls = lsGet(name);
    /* 立即过期：端点恢复后下一次读取就会重新尝试，不会长时间停留在旧值 */
    if (ls) { cache[name] = ls.payload; cacheTs[name] = Date.now() - ttlOf(name); return ls.payload; }
    return fallback === undefined ? null : fallback;
  }

  DSH.json = async function (name, fallback) {
    if (name === "cloud") return loadCloud(fallback);
    if (cache[name] !== undefined &&
        Date.now() - (cacheTs[name] || 0) < ttlOf(name)) return cache[name];
    if (inflight[name]) return inflight[name];

    inflight[name] = (async function () {
      try {
        const cfg = await DSH.json("cloud", null);
        const eps = cfg ? endpointList(cfg) : [];
        if (!eps.length) {
          const ce = new Error("Online dataset configuration unavailable");
          ce.configFailure = true;     // 本地引导配置问题，不应熔断数据库
          throw ce;
        }
        const order = orderedEndpoints(eps, preferClosingDataset && tierOf(name) !== TIER_STATIC);
        if (!order.length) return fallbackValue(name, fallback);  // 全部端点熔断中
        let lastErr = null;
        for (const ep of order) {
          try {
            const v = await fetchFromEndpoint(name, ep);
            const h = healthOf(ep);
            h.circuitUntil = 0; h.failures = 0; h.lastOk = Date.now();
            try { localStorage.removeItem(LS_CIRCUIT + endpointId(ep)); }
            catch (e) { /* 隐私模式下无持久化熔断可清 */ }
            rememberEndpoint(ep);
            cache[name] = v; cacheTs[name] = Date.now();
            lsSet(name, v);
            delete degraded[name];
            delete degradedAt[name];
            lastOnlineOkAt = Date.now();
            return v;
          } catch (e) {
            failEndpoint(ep, e);      // 主库 402/5xx 只熔断主库，继续尝试备库
            lastErr = e;
            if (e.status === 401 || e.status === 403) delete cache.cloud;
          }
        }
        circuitUntil = Date.now() + GLOBAL_COOLDOWN_MS;
        throw lastErr || new Error("All online endpoints unavailable");
      } catch (e) {
        noteErr(name, e);
        return fallbackValue(name, fallback);
      } finally { delete inflight[name]; }
    })();
    return inflight[name];
  };

  /* 兼容旧调用：只在「该层已到期且未熔断」时才真正重新取数 */
  DSH.jsonNoCache = async function (name, fallback) {
    if (Date.now() < circuitUntil)
      return cache[name] !== undefined ? cache[name] : (fallback === undefined ? null : fallback);
    if (Date.now() - (cacheTs[name] || 0) < ttlOf(name))
      return cache[name] !== undefined ? cache[name] : DSH.json(name, fallback);
    delete cache[name]; delete cacheTs[name];
    return DSH.json(name, fallback);
  };

  /* ---------- 版本探测：页面自动感知数据库变更 ----------
     三个极小请求（静态数据集时间戳 / 行情版本 / 计算结果场次），合计不到
     1KB；版本未变就不拉任何 payload。相比「每次全量重拉 9 个数据集」，
     常态流量下降约两个数量级，同时把「数据一更新页面就知道」从人工触发
     变成自动。探测失败只记录、不熔断，交给数据请求自身的容错路径。 */
  let probeInflight = null;
  const versions = { static: "", quote: "", result: "" };

  async function probeVersion(parts = ["static", "quote", "result"]) {
    const includeStatic = parts.includes("static");
    if (probeInflight) return probeInflight;
    probeInflight = (async function () {
      if (Date.now() < circuitUntil) return null;
      try {
        const cfg = await DSH.json("cloud", null);
        const order = orderedEndpoints(cfg ? endpointList(cfg) : [], preferClosingDataset);
        if (!order.length) return null;
        const ep = order[0];
        if (ep.type === "turso") {
          /* 备库一条 SQL 拿全部 key 的时间戳，按前端三层拆成同一组指纹，
             备库同步后依然能精确触发「哪一层变了就失效哪一层」。 */
          const body = { baton: null, requests: [
            { type: "execute", stmt: {
              sql: "select key, coalesce(nullif(payload_sha256, ''), updated_at) from site_dataset" +
                (includeStatic ? "" : " where key in ('quotes','equity','b/equity','signal','b/signal','preview','b/preview')"),
              args: [] } },
            { type: "close" }
          ] };
          const r = await fetch(ep.url + "/v2/pipeline", {
            method: "POST", cache: "no-store",
            headers: { Authorization: "Bearer " + ep.key, "Content-Type": "application/json" },
            body: JSON.stringify(body)
          });
          if (!r.ok) throw new Error("HTTP " + r.status);
          const raw = await r.json();
          const item = (raw && raw.results || [])[0];
          if (!item || item.type !== "ok") throw new Error("Backup version probe failed");
          const map = {};
          for (const row of ((item.response || {}).result || {}).rows || [])
            map[row[0].value] = (row[1] && row[1].value) || "";
          const fp = keys => keys.map(k => k + ":" + (map[k] || "")).sort().join("|");
          return { static: fp(STATIC_KEYS), quote: map.quotes || "", result: fp(DAILY_KEYS) };
        }
        const h = { apikey: ep.key }, opt = { cache: "no-store", headers: h };
        const [a, b, c] = await Promise.all([
          includeStatic ? fetch(ep.url + "/rest/v1/v_site_dataset?select=key,updated_at", opt) : null,
          fetch(ep.url + "/rest/v1/live_quote_snapshot?singleton=eq.true&select=evidence_sha256", opt),
          fetch(ep.url + "/rest/v1/v_live_strategy_results?select=variant,kind,quote_version,created_at", opt)
        ]);
        const failure = [a, b, c].find(response => response && !response.ok);
        if (failure) throw new Error("HTTP " + failure.status);
        const sa = a ? (await a.json()).map(r => r.key + ":" + r.updated_at).sort().join("|") : "";
        let qv = "", rv = "";
        if (b.ok) {
          const qb = await b.json();
          if (Array.isArray(qb) && qb.length) qv = qb[0].evidence_sha256 || "";
        }
        if (c.ok) rv = (await c.json())
          .map(r => r.variant + "/" + r.kind + ":" + r.quote_version + ":" + r.created_at).sort().join("|");
        return { static: sa, quote: qv, result: rv };
      } catch (e) {
        noteErr("version-probe", e);
        return null;
      } finally { probeInflight = null; }
    })();
    return probeInflight;
  }
  DSH.probeVersion = probeVersion;

  /* 返回本次变化导致失效的 key 列表；null = 探测不可用，[] = 无变化 */
  DSH.syncIfChanged = async function (parts = ["static", "quote", "result"]) {
    const v = await probeVersion(parts);
    if (!v) return null;
    const changed = [];
    for (const part of parts) {
      if (versions[part] && versions[part] !== v[part]) changed.push(part);
      versions[part] = v[part];
    }
    if (!changed.length) return [];
    const groups = { static: STATIC_KEYS, quote: ["quotes"], result: DAILY_KEYS };
    const expired = [];
    for (const part of changed)
      for (const k of groups[part]) {
        delete cache[k];
        delete cacheTs[k];
        expired.push(k);
      }
    return expired;
  };

  /* 页面可见期间持续感知；切回标签页立即探测一次（最典型的「及时」场景） */
  DSH.startVersionWatch = function (onChange) {
    let last = 0;
    async function tick(force) {
      if (document.hidden) return;
      if (!force && Date.now() - last < (isTradingHours() ? 60000 : 600000)) return;
      last = Date.now();
      try {
        const expired = await DSH.syncIfChanged();
        if (expired && expired.length && typeof onChange === "function") await onChange(expired);
      } catch (e) { noteErr("version-watch", e); }
    }
    setInterval(function () { tick(false); }, 30000);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) tick(true);
    });
    window.addEventListener("pageshow", function () { tick(true); });
  };
  DSH.versionStamp = function () { return Object.assign({}, versions); };

  /* ---------- 站点提示状态中心（2026-11-08） ----------
     端点类型只是路由结果，不是故障：主库免费额度限服（402）后浏览器自动
     读 Turso 实时备库，属计划内冗余，绝不能向访客弹主库故障告警。
     只有真实异常才登记提示；所有来源共用一个横幅，恢复后自动移除，
     避免多处各自建条、互相覆盖或故障恢复后残留。 */
  const notices = {};                    // id -> {level, text, ts}
  const NOTICE_TTL_MS = 60 * 60 * 1000;
  const NOTICE_RANK = { info: 1, warn: 2, error: 3 };

  DSH.reportNotice = function (id, level, text) {
    if (!text) delete notices[id];
    else notices[id] = { level: NOTICE_RANK[level] ? level : "warn",
                         text: String(text), ts: Date.now() };
    return DSH.renderNotices();
  };
  DSH.clearNotice = function (id) { delete notices[id]; return DSH.renderNotices(); };
  DSH.notices = function () { return Object.assign({}, notices); };
  DSH.renderNotices = function () {
    if (typeof document === "undefined" || !document.body) return null;
    const now = Date.now();
    const live = Object.keys(notices)
      .filter(function (k) { return now - notices[k].ts < NOTICE_TTL_MS; })
      .map(function (k) { return notices[k]; });
    let bar = document.getElementById("serviceBanner");
    if (!live.length) {
      if (!bar) return null;
      if (typeof bar.remove === "function") bar.remove();
      else if (bar.parentNode) bar.parentNode.removeChild(bar);
      return null;
    }
    live.sort(function (a, b) { return NOTICE_RANK[b.level] - NOTICE_RANK[a.level]; });
    const top = live[0];
    if (!bar) {
      bar = document.createElement("div");
      bar.id = "serviceBanner";
      document.body.insertBefore(bar, document.body.firstChild);
    }
    /* info 是「暂时用最近缓存」这类非故障告知，用中性色而非红色告警 */
    bar.className = top.level === "info" ? "data-notice" : "data-alert";
    bar.textContent = (top.level === "info" ? "" : "⚠ ") + top.text;
    bar.style.display = "block";
    return bar;
  };

  /* mode 是诊断用的路由快照（live/backup/stale），不代表故障；
     level 才是访客提示的唯一依据：healthy / degraded / offline。 */
  DSH.dataStatus = function () {
    const now = Date.now();
    /* 一次瞬时失败若已在后续请求中恢复，degraded 会被删除；这里再对
       长期无人重新请求的辅助 key 做时间衰减，避免告警永久常驻。 */
    const activeDegraded = Object.keys(degraded).filter(function (k) {
      /* health 只是辅助诊断数据，取不到应由 health notice / 看门狗负责，
         不能让整个页面被判定为「离线」而弹出访客提示。 */
      return k !== "health" && k !== "cloud" &&
             now - (degradedAt[k] || 0) < NOTICE_TTL_MS;
    });
    const online = lastOnlineOkAt > 0 && now - lastOnlineOkAt < NOTICE_TTL_MS;
    const allDown = now < circuitUntil;
    return {
      mode: activeDegraded.length ? "stale" : (activeType === "turso" ? "backup" : "live"),
      level: !activeDegraded.length ? "healthy"
           : (allDown || !online ? "offline" : "degraded"),
      online: online,
      allDown: allDown,
      lastOnlineOkAt: lastOnlineOkAt,
      degradedKeys: activeDegraded,
      activeEndpoint: activeType,
      activeUrl: activeUrl,
      endpoints: Object.keys(endpointHealth).map(function (k) {
        return { id: k, circuitOpen: endpointHealth[k].circuitUntil > Date.now(),
                 failures: endpointHealth[k].failures, lastOk: endpointHealth[k].lastOk };
      }),
      circuitOpen: allDown
    };
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
        if (!cfg) return false;
        /* 持仓写入只能走 Supabase（备库 token 只读）；老格式缺顶层
           url/key 时从 endpoints 里取主库端点，保持云同步可用。 */
        if (!cfg.url || !cfg.key) {
          const primary = (cfg.endpoints || []).find(e => (e.type || "supabase") === "supabase");
          if (!primary) return false;
          cfg = Object.assign({}, cfg, { url: primary.url, key: primary.key });
        }
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
    const stale = DSH.quoteFreshness(q);
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
      (stale ? "⚠ 数据已 " + ageTxt : (stale === null ? "时效待核验 ｜ " : "") + ageTxt) + " ｜ " + DSH.esc(when) + "</span>";
  };
  /* ---------- 页面打开期间的自动刷新 ----------
     数据由 GitHub Actions 定期生成；页面这边每 5 分钟重新拉一次，
     有新数据就重绘，没有就只更新时间，避免用户看到过期数字。 */
  DSH._refreshers = [];
  DSH.onRefresh = function (fn) { if (typeof fn === 'function') DSH._refreshers.push(fn); };
  DSH.autoRefresh = function (minutes) {
    const ms = (minutes || 5) * 60000;
    setInterval(async function () {
      if (document.hidden) return;                 // 后台标签页不产生任何流量
      try {
        /* 只让「已到本层 TTL」的数据过期；静态层不会被无谓重拉 */
        for (const k of Object.keys(cache)) {
          if (k === "cloud") continue;
          if (Date.now() - (cacheTs[k] || 0) >= ttlOf(k)) {
            delete cache[k]; delete cacheTs[k];
          }
        }
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

  /* ---------- 当日涨跌（三页共用的唯一口径） ----------
     按正式持仓权重合成最新报价的日内涨跌；行情够新显示「盘中」，否则「收盘」。 */
  DSH.today = function (signal, quotes, equity) {
    if (equity && equity.runtime_verified === true) {
      const day = equity.today, amount = +(localStorage.getItem("dsh.amt") || 100000);
      const lagging = !!(quotes && equity.quote_version !== quotes.version);
      const ownFresh = DSH.quoteFreshness({ts:equity.source_as_of,fresh_until:equity.fresh_until});
      if (!day || !quotes || DSH.quoteFreshness(quotes) !== false ||
          (lagging && (ownFresh !== false || Date.parse(equity.source_as_of) > Date.parse(quotes.ts))) ||
          day.quote_version !== equity.quote_version ||
          day.session !== quotes.session || !Number.isFinite(day.pct) || day.pct <= -1 ||
          !Number.isFinite(day.per_dollar) || !Number.isFinite(amount) || amount <= 0) return null;
      return {amount:amount*day.per_dollar,pct:day.pct,session:day.session,
        asOf:equity.updated,sourceAsOf:equity.source_as_of || quotes.ts,live:typeof equity.quote_intraday==='boolean' ? equity.quote_intraday : quotes.intraday === true,
        label:"今日",lagging:lagging};
    }
    // 口径：day = Σ 股数×(最新价−昨收)，基数 = 当前市值（不是目标权重）
    const qs = (quotes && quotes.quotes) || {};
    const pos = (equity && equity.position) || [];
    const amt = +(localStorage.getItem("dsh.amt") || 100000);
    // Strategy performance stays fully invested; exposure is advice only.
    const exp = 1;
    if (!Array.isArray(pos) || !pos.length || !Number.isFinite(amt) || amt <= 0) return null;
    const settled = String(equity.last_session || '').replace(/-/g, '');
    const session = String(quotes && quotes.session || '').replace(/-/g, '');
    if (!/^\d{8}$/.test(settled) || !/^\d{8}$/.test(session) || settled > session) return null;
    const previousDay = String(quotes && quotes.previous_session || '').replace(/-/g,'');
    if (settled !== session && settled !== previousDay) return null;
    if (pos.some(function (p) {
      if (!p || typeof p !== 'object') return true;
      const q = qs[p.ticker];
      return !Number.isFinite(p.entry) || p.entry <= 0 ||
        !Number.isFinite(p.last) || p.last <= 0 || !Number.isFinite(p.last_raw) || p.last_raw <= 0 ||
        !Number.isFinite(p.weight) || p.weight <= 0 || !q ||
        !Number.isFinite(q.p) || q.p <= 0 || !Number.isFinite(q.pc) || q.pc <= 0 ||
        String(p.last_date || '').replace(/-/g,'') !== settled ||
        (settled !== session && Math.abs(p.last_raw-q.pc) > Math.max(.00011,p.last_raw*1e-8));
    })) return null;
    let day = 0, mv = 0, covered = 0;
    pos.forEach(function (p) {
      const q = qs[p.ticker];
      const base = p.entry > 0 ? (amt * p.weight * exp) / p.entry : 0;   // 股数
      // Net-value entries and last prices share an adjusted unit. Convert
      // current raw quotes to that unit before multiplying by strategy shares.
      const unit = p.last / p.last_raw;
      const price = q.p * unit;
      const prev = q.pc * unit;
      if (!base || !price) return;
      mv += base * price;
      if (prev) { day += base * (price - prev); covered++; }
    });
    const cash = amt * (1 - exp);
    const totalMv = mv + cash;
    if (covered !== pos.length || !Number.isFinite(day) || !Number.isFinite(totalMv) || !totalMv) return null;
    const age = DSH.ageHours(quotes && quotes.ts);
    // 优先用行情自带的 intraday 标志；缺失时退回「45 分钟内」
    const live = (quotes && typeof quotes.intraday === 'boolean')
      ? quotes.intraday
      : (age != null && age <= 0.75);
    return {
      amount: day,
      pct: totalMv - day > 0 ? day / (totalMv - day) : null,
      session: (quotes && quotes.session) || "",
      asOf: (quotes && quotes.ts_local) || "",
      sourceAsOf: quotes && quotes.ts,
      live: live,
      label: "今日",
    };
  };

  DSH.yearResult = function (signal, quotes, equity) {
    const last = String(equity && equity.last_session || '').replace(/-/g,'');
    const pct = equity && equity.stats && equity.stats.strategy;
    const bench = equity && equity.stats && equity.stats.benchmark;
    const result = { pct: Number.isFinite(pct) ? pct : null,
      benchmark: Number.isFinite(bench) ? bench : null,
      asOf: /^\d{8}$/.test(last) ? last.slice(0,4)+'-'+last.slice(4,6)+'-'+last.slice(6,8) : '', bridged: false };
    const day = DSH.today(signal,quotes,equity);
    const session = quotes && quotes.session;
    if (!day || !Number.isFinite(day.pct) || result.pct == null || !result.asOf || session <= result.asOf) return result;
    result.pct = (1+result.pct)*(1+day.pct)-1;
    const spy = quotes.quotes && quotes.quotes.SPY;
    result.benchmark = result.benchmark != null && spy && Number.isFinite(spy.p) && spy.p>0 && Number.isFinite(spy.pc) && spy.pc>0
      ? (1+result.benchmark)*spy.p/spy.pc-1 : null;
    if (equity && equity.runtime_verified) {
      const factor = equity.today && equity.today.benchmark_factor;
      result.benchmark = Number.isFinite(bench) && Number.isFinite(factor) && factor > 0
        ? (1+bench)*factor-1 : null;
    }
    result.asOf = session;
    result.bridged = true;
    return result;
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
    const w = opts.w || 900, h = opts.h || 320, FS = opts.fs || 11, L = opts.l || 54, R = opts.r || 16, T = opts.t || 16, B = opts.b || 28, SW = opts.sw || 2;
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
    let svg = '<svg viewBox="0 0 ' + w + " " + h + '" shape-rendering="geometricPrecision" style="width:100%;height:auto;display:block">';
    // 网格 + Y 轴
    for (let k = 0; k <= 4; k++) {
      const rawV = logScale ? Math.exp(min + (max - min) * k / 4) : (min + (max - min) * k / 4);
      const y = (T + (1 - ((logScale ? Math.log(rawV) : rawV) - min) / (max - min || 1)) * (h - T - B)).toFixed(1);
      svg += '<line x1="' + L + '" y1="' + y + '" x2="' + (w - R) + '" y2="' + y +
        '" stroke="rgba(255,255,255,.06)" stroke-width="1"/>';
      svg += '<text x="' + (L - 9) + '" y="' + (+y + 4) + '" fill="#6F7D92" font-size="' + FS + '" text-anchor="end">' +
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
    const xOnly = !!opts.xOnly;
    // 只在有标签的点打刻度（如每年 1 月），并控制密度避免重叠
    let idxs = [];
    for (let i = 0; i < pts.length; i++) {
      if (!xOnly || pts[i].label) idxs.push(i);
    }
    if (!xOnly) idxs = idxs.filter((i) => i >= step);
    const stride = Math.max(1, Math.ceil(idxs.length / 6));
    idxs.forEach((i, k) => {
      if (k % stride) return;
      const d0 = pts[i].d;
      const lab = pts[i].label || (d0.slice(4, 6) + "/" + d0.slice(6, 8));
      const first = k === 0, last = k + stride >= idxs.length;
      const anchor = first ? "start" : (last ? "end" : "middle");
      svg += '<text x="' + X(i).toFixed(1) + '" y="' + (h - 9) + '" fill="#6F7D92" font-size="' + FS +
        '" text-anchor="' + anchor + '">' + lab + "</text>";
    });
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
    console.error('页面显示异常', msg);
    if (!document.body) return;
    var d = document.getElementById('appError');
    if (!d) {
      d = document.createElement('div'); d.id = 'appError';
      d.setAttribute('role', 'alert');
      d.style.cssText = 'position:fixed;bottom:20px;left:20px;right:20px;background:#fff6ee;color:#784b32;padding:14px 20px;border:1px solid #eacdb8;border-radius:12px;font:14px/1.5 sans-serif;z-index:9999';
      document.body.appendChild(d);
    }
    d.textContent = '页面显示暂时出了问题，请刷新重试。';
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
        parts.push("行情时间(北京) <b style=\"color:var(--txt-2)\">" +
          DSH.esc(d.toLocaleString('zh-CN', {timeZone:'Asia/Shanghai', hour12:false})) + "</b>");
      }
      if (DSH.quoteStaleForDisplay(q) === true) {
        parts.push("<b style=\"color:#FFAB6B\">⚠ 行情更新超时</b>");
      }
    }
    if (eq && eq.updated) {
      parts.push("净值截至(北京) <b style=\"color:var(--txt-2)\">" + DSH.esc(eq.updated) + "</b>");
    }
    if (DSH.quoteFreshness(q) === null) parts.push("行情时效待核验");
    if (!parts.length) return;

    // 行情过旧时，顶部通栏提醒（不能只缩在页脚）
    if (DSH.quoteStaleForDisplay(q) === true && !document.getElementById("staleBar")) {
      const bar = document.createElement("div");
      bar.id = "staleBar";
      bar.style.cssText = "position:sticky;top:0;z-index:60;background:#7A4A12;color:#FFE7C2;" +
        "font-size:13px;padding:9px 16px;text-align:center;letter-spacing:.01em";
      bar.textContent = "⚠ 行情更新超时，最近行情所属交易日为 " + (q.session || "未知");
      document.body.insertBefore(bar, document.body.firstChild);
    }
    if (DSH.quoteStaleForDisplay(q) !== true) {
      const old = document.getElementById("staleBar");
      if (old) old.remove();
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

  /* ---------- 云端健康数据 ----------
     真实数据异常照样告警，但不再自建横幅：统一交给状态中心，
     恢复后能自动移除，也不会和页面自身的提示叠成两条。 */
  (function () {
    if (location.pathname.indexOf("report.html") >= 0) return;
    /* 显式取 window.DSH：不依赖「window 属性自动成为全局变量」的隐式行为，
       在严格模式、模块化和测试沙箱里都一致。 */
    var api = window.DSH;
    if (!api) return;
    api.json("health", null)
      .then(function (h) {
        if (!h || h.status === "ok") { api.clearNotice("health"); return; }
        api.reportNotice("health", "warn",
          "数据异常：" + (h.issues || []).join("；") +
          (h.quotes_ts ? "（行情 " + h.quotes_ts + " UTC）" : ""));
      });
  })();
