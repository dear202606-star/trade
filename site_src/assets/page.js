/* 单页渲染：一次加载，全部可见，无 Tab、无隐藏内容 */
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var EQ = null, SIG = null, Q = null, CV = null, SH = null, PV = null, ZH = {}, SEC = {};
  /* GICS 行业中文名：持仓、次年预览共用一套口径 */
  var SECTOR_ZH = {
    "Information Technology": "信息技术", "Communication Services": "通信服务",
    "Consumer Discretionary": "可选消费", "Consumer Staples": "必需消费",
    "Health Care": "医疗保健", "Financials": "金融", "Industrials": "工业",
    "Energy": "能源", "Utilities": "公用事业", "Materials": "原材料",
    "Real Estate": "房地产", "Unknown": "未分类"
  };
  function sectorOf(ticker, inline) {
    var raw = inline || (SEC && SEC[ticker]) || "Unknown";
    return { raw: raw, zh: SECTOR_ZH[raw] || raw };
  }
  var amt = 100000;
  localStorage.setItem("dsh.amt", String(amt));

  function yuan(v) {
    if (v == null || !isFinite(v)) return "—";
    return (v < 0 ? "-$" : "$") + Math.abs(Math.round(v)).toLocaleString("en-US");
  }
  function pct2(v, d) {
    if (v == null || !isFinite(v)) return "—";
    if (Math.abs(v) < 1e-9) return (0).toFixed(d == null ? 2 : d) + "%";
    return (v > 0 ? "+" : "") + (v * 100).toFixed(d == null ? 2 : d) + "%";
  }
  function cls(v) {
    if (v == null || !isFinite(v)) return "";
    if (Math.abs(v) < 1e-9) return "flat";   // 0 用中性色
    return v > 0 ? "up" : "down";
  }

  /* ---------- ① 单日表现 ---------- */
  function renderNow() {
    var advice = DSH.marketAdvice(EQ, Q);
    var trend = advice ? advice.trend : "—";
    var hold = trend === "满仓";

    $("verdict").textContent = advice ? (hold ? "持有不动" : "建议减仓") : "建议待更新";
    $("verdictSub").textContent = !advice ? "大盘建议暂不可用" : hold
      ? "下次换仓 " + ((SIG && SIG.rebalance_hint) || "12 月末")
      : "大盘跌破均线 · 建议减仓";

    var current = DSH.quoteFreshness(Q) === false ? DSH.today(SIG, Q, EQ) : null;
    var t = current || DSH.lastVerifiedDay(EQ);
    $("todayLabel").textContent = "单日策略表现";
    $("todayPct").textContent = t ? pct2(t.pct) : "—";
    $("todayPct").className = "num num-lg " + (t ? cls(t.pct) : "");
    $("todayAmt").textContent = t ? yuan(t.amount) : "";
    $("todayAmt").className = "sub " + (t ? cls(t.amount) : "");
    $("todayStamp").textContent = t ? DSH.sessionStamp(t.session, t.sourceAsOf, t.live) +
      (current && !current.lagging ? "" : " · 最近核验") : DSH.todayUnavailable(Q, EQ);

    $("switchTxt").textContent = trend;
    var _spy = advice ? advice.spy.toFixed(0) : "—", _ma = advice ? advice.ma.toFixed(0) : "—";
    $("switchSub").textContent = "SPY " + _spy + " " +
      (advice ? (hold ? "高于均线 " : "低于均线 ") : "均线 ") + _ma;
  }

  /* ---------- ② 当期账户 ---------- */
  function account(bridge) {
    var pos = (EQ && (bridge && EQ.runtime_verified ? EQ.live_position : EQ.position)) || [];
    // Match the full-holding equity curve; advice does not trade this account.
    var exposure = 1;
    var rows = [], mv = 0, cost = 0;
    pos.forEach(function (p) {
      var shares = p.entry > 0 ? (amt * p.weight * exposure) / p.entry : 0;
      var quote = Q && Q.quotes && Q.quotes[p.ticker];
      var last = bridge && !EQ.runtime_verified ? quote.p * p.last / p.last_raw : p.last;
      var c = shares * p.entry, v = shares * last;
      mv += v; cost += c;
      rows.push({ ticker: p.ticker, weight: p.weight, entry: p.entry, last: last,
        entry_raw: p.entry_raw, last_raw: bridge && !EQ.runtime_verified ? quote.p : p.last_raw,
        shares: shares, mv: v, cost: c });
    });
    var cash = amt * (1 - exposure);
    return { rows: rows, mv: mv + cash, cost: cost, pnl: mv - cost,
      pnlPct: cost > 0 ? (mv - cost) / cost : null, exposure: exposure };
  }

  function renderYear() {
    var y = EQ && EQ.base_date ? (+String(EQ.base_date).slice(0, 4) + 1) : "";
    var yearResult = DSH.yearResult(SIG, DSH.quoteFreshness(Q) === false ? Q : {}, EQ);
    $("yearTitle").textContent = (y ? y + " 年" : "") + "策略收益" +
      (yearResult.asOf ? " · 截至 " + yearResult.asOf.slice(5).replace('-', '/') : "");
    var c = account(yearResult.bridged);

    // 收盘口径（曲线末点）
    var closePct = (EQ && EQ.stats) ? EQ.stats.strategy : c.pnlPct;
    // 盘中口径：只有盘中才叠加今日涨跌（收盘后净值已含当日，叠加会重复计算）
    var td = DSH.today(SIG, Q, EQ);
    var dayPct = (td && td.pct != null) ? td.pct : 0;
    var isLive = yearResult.bridged;
    var livePct = yearResult.pct;

    $("yearRet").textContent = pct2(livePct);
    $("yearRet").className = "num num-xl " + cls(livePct);

    var bench = yearResult.benchmark;
    var extra = "";
    if (isLive && closePct != null) {
      extra = " ｜ 含 " + yearResult.asOf.slice(5).replace('-', '/') + " <span class='" + cls(dayPct) + "'>" + pct2(dayPct) +
        "</span>（前收 " + pct2(closePct) + "）";
    }
    $("yearVs").innerHTML = "同期标普 <span class='" + cls(bench) + "'>" + pct2(bench) + "</span>" +
      (livePct != null && bench != null
        ? " ｜ 跑赢 <span class='" + cls(livePct - bench) + "'>" + pct2(livePct - bench) + "</span>" : "") + extra;

    // 金额同步为盘中口径
    if (livePct != null) {
      c.pnlPct = livePct;
      c.pnl = amt * livePct;
      c.mv = amt + c.pnl;
    }

    $("tMv").textContent = yuan(c.mv);
    $("tPnl").textContent = yuan(c.pnl);
    $("tPnl").className = "num num-md " + cls(c.pnl);
    $("tCost").textContent = yuan(c.cost);
    $("tExp").textContent = Math.round(c.exposure * 100) + "%";

    var bdTxt = (EQ && EQ.base_date)
      ? EQ.base_date.slice(0, 4) + "-" + EQ.base_date.slice(4, 6) + "-" + EQ.base_date.slice(6, 8) : "—";
    var pm = $("posMeta");
    if (pm) {
      var quoteTime = yearResult.bridged ? (EQ.runtime_verified ? EQ.source_as_of : Q.ts) :
        (EQ && EQ.quote_intraday !== true ? EQ.source_as_of : null);
      pm.textContent = (y ? (y - 1) + "-12 选出 · " : "") + "建仓 " + bdTxt +
        (yearResult.asOf ? " · 估值 " + DSH.sessionStamp(yearResult.asOf, quoteTime, yearResult.bridged ? (EQ.runtime_verified ? EQ.quote_intraday : Q.intraday) : false) : "");
    }
    var posRows = $("posRows");
    posRows.innerHTML = c.rows.map(function (r) {
      var eraw = r.entry_raw != null ? r.entry_raw : r.entry;
      var lraw = r.last_raw != null ? r.last_raw : r.last;
      var ret = r.entry > 0 ? r.last / r.entry - 1 : null;
      var pnl = r.mv - r.cost;
      return '<tr class="pr-row">' +
        '<th scope="row"><span class="tk-cell"><span class="tk">' + DSH.esc(r.ticker) + '</span>' +
          '<span class="sec-tag">' + DSH.esc(sectorOf(r.ticker).zh) + '</span></span></th>' +
        '<td class="r pr-return ' + cls(ret) + '">' + pct2(ret) + '</td>' +
        '<td class="r ' + cls(pnl) + '">' + yuan(pnl) + '</td>' +
        '<td class="r pr-detail"><span class="mobile-field" aria-hidden="true">权重</span>' + Math.round(r.weight * 100) + '%</td>' +
        '<td class="r pr-detail"><span class="mobile-field" aria-hidden="true">建仓价</span>' + eraw.toFixed(2) + '</td>' +
        '<td class="r pr-detail"><span class="mobile-field" aria-hidden="true">市场价</span>' + lraw.toFixed(2) + '</td>' +
        '<td class="r pr-detail"><span class="mobile-field" aria-hidden="true">市值</span>' + yuan(r.mv) + '</td></tr>';
    }).join("");
  }

  function drawYearSpark() {
    var box = $("yearSpark");
    if (!box) return;
    if (!EQ || !EQ.points || EQ.points.length < 3) { box.innerHTML = ""; return; }
    try {
      box.innerHTML = DSH.spark(EQ.points.map(function (p) { return { y: p.s }; }),
        { h: box.clientHeight || 176, w: Math.max(240, Math.round(box.clientWidth)), color: "#009c80" });
    } catch (e) { box.innerHTML = ""; }
  }

  /* 顶部提示的唯一入口：按数据健康度决定显示什么，正常态不显示任何东西。
     原来这里会先看「是否切到备库」，把计划内冗余当成故障弹红条，已废除。 */
  function checkFreshness() { renderServiceNotice(); }

  /* 中文名优先（行情源带中文），否则回退英文 */
  function zhName(ticker, fallback) {
    var q = (Q && Q.quotes && Q.quotes[ticker]) || null;
    var nm = q && q.name ? String(q.name).trim() : "";
    if (/[\u4e00-\u9fff]/.test(nm)) return nm;             // 行情源自带中文
    if (ZH && ZH[ticker]) return ZH[ticker];                // 本地中文映射
    var fb = fallback ? String(fallback).trim() : "";
    if (fb && fb.toUpperCase() !== String(ticker).toUpperCase()) return fb;
    return nm && nm.toUpperCase() !== String(ticker).toUpperCase() ? nm : "";
  }

  /* 统一日期为 YYYYMMDD（月频数据补 01） */
  function ymd(d) {
    var t = String(d || "").replace(/[^0-9]/g, "");
    if (t.length === 6) t += "01";
    return t || "20200101";
  }

  /* 年度刻度：只在每年 1 月打点，显示 4 位年份 */
  function yearLab(d) {
    var t = String(d || "").replace(/[^0-9]/g, "");
    if (t.length < 6) return "";
    return t.slice(4, 6) === "01" ? t.slice(0, 4) : "";
  }

  function drawAllChart() {
    var box = $("allChart");
    if (!box) return;
    var note = $("curveNote");
    if (note) {
      if (CV && CV.degraded) {
        note.textContent = "曲线月内点含 " + (CV.synthetic_months || "部分") +
          " 个线性插值点，仅年末点对应真实复利；回撤与波动率不宜据此解读";
        note.hidden = false;
      } else {
        note.hidden = true;
      }
    }
    if (!CV || !CV.points || CV.points.length < 4) { box.innerHTML = ""; return; }
    try {
      var wpx = Math.max(300, Math.round(box.clientWidth));
      var _cv = (typeof CV_VIEW !== "undefined" && CV_VIEW && CV_VIEW.length) ? CV_VIEW : CV.points;
      box.innerHTML = DSH.lineChart([
        { name: "策略", color: "#009c80", pts: _cv.map(function (p) { return { d: ymd(p.d), y: p.s, label: yearLab(p.d) }; }) },
        { name: "标普500", color: "#82958d", pts: _cv.map(function (p) { return { d: ymd(p.d), y: p.b || 1, label: yearLab(p.d) }; }) }
      ], { h: 200, w: wpx, fs: 11, l: 42, b: 30, sw: 2, log: true, xOnly: true,
        fmt: function (v) { return v >= 10 ? Math.round(v) + "×" : v.toFixed(1) + "×"; } });
    } catch (e) { box.innerHTML = ""; }
  }

  /* ---------- 明年预览 ---------- */
  function renderPreview() {
    var box = $("previewRows");
    if (!box) return;
    var status = $("previewStatus");
    var current = DSH.previewCurrent(PV, Q);
    var verified = current || DSH.previewVerified(PV);
    status.hidden = !verified;
    if (!verified) {
      box.textContent = "暂无已核验排名";
      $("previewNote").textContent = "候选及收益核验通过后更新";
      return;
    }
    var intraday = DSH.snapshotIntraday(PV, Q);
    status.textContent = PV.final ? "已定稿" : current ? (intraday ? "盘中快照" : "收盘快照") : "最近核验";
    $("previewNote").textContent = "年初至今收益 · " + DSH.sessionStamp(PV.session, PV.as_of_ts, intraday);
    box.innerHTML = (PV.top || []).map(function (r) {
      var sec = sectorOf(r.ticker, r.sector).zh;
      return '<div class="pv-row">' +
        '<div class="pv-main"><div class="pv-id"><span class="pv-rank">' + (r.rank || "") + '</span>' +
          '<span class="tk">' + DSH.esc(r.ticker) + '</span></div>' +
          '<span class="sec-tag">' + DSH.esc(sec) + '</span></div>' +
        '<div class="pv-val ' + cls(r.ytd) + '">' + pct2(r.ytd, 1) + '</div>' +
        '</div>';
    }).join("");
  }

  /* ---------- ⑤ 全周期 ---------- */
  /* 历史回测展示起点：2013 年（此前的行情/行业覆盖不足，见 KNOWN_ISSUES G5/G6） */
  var SHOW_FROM = 2013;
  var CV_VIEW = null;

  function histView() {
    var years = ((SH && SH.years) || []).filter(function (r) { return r.year >= SHOW_FROM; });
    var allPts = (CV && CV.points) || [];
    // 基准点取「上一年 12 月」（2012-12），否则会漏掉 2013 年 1 月的涨幅
    var base = null;
    for (var i = 0; i < allPts.length; i++) {
      if ((+String(allPts[i].d).slice(0, 4)) < SHOW_FROM) base = allPts[i];
    }
    var pts = allPts.filter(function (p) {
      return (+String(p.d).slice(0, 4)) >= SHOW_FROM;
    });
    var s0 = base ? base.s : (pts.length ? pts[0].s : 1);
    var b0 = base ? (base.b || 1) : (pts.length ? (pts[0].b || 1) : 1);
    var norm = pts.map(function (p) {
      return { d: p.d, s: p.s / s0, b: (p.b ? p.b / b0 : null), label: p.label };
    });
    var ok = norm.length > 1 && years.length > 0;
    var total = ok ? norm[norm.length - 1].s - 1 : null;
    var bench = ok ? norm[norm.length - 1].b - 1 : null;
    var peak = 1, mdd = 0;
    norm.forEach(function (p) {
      if (p.s > peak) peak = p.s;
      var dd = p.s / peak - 1;
      if (dd < mdd) mdd = dd;
    });
    var n = years.length;
    var cagr = (ok && total > -1) ? Math.pow(1 + total, 12 / Math.max(1, n * 12)) - 1 : null;
    var wins = years.filter(function (r) { return (r.ret || 0) > 0; }).length;
    return {
      years: years, norm: norm, total: total, bench: bench, ok: ok,
      mdd: ok ? mdd : null, cagr: cagr, wins: wins, n: n, from: SHOW_FROM,
      to: years.length ? years[years.length - 1].year : SHOW_FROM
    };
  }

  function renderAll() {
    var v = histView();
    if (!v.ok) {
      ["bTotal", "bCagr", "bMdd", "bWin", "bBench"].forEach(function (id) {
        var e = $(id); if (e) { e.textContent = "—"; e.className = "num"; }
      });
      var rg0 = $("allRange");
      if (rg0) rg0.textContent = "数据缺失";
      CV_VIEW = [];
      var box = $("allChart"); if (box) box.innerHTML = "";
      return;
    }
    $("bTotal").textContent = pct2(v.total, 0);
    $("bTotal").className = "num num-xl " + cls(v.total);
    $("bCagr").textContent = pct2(v.cagr, 1);
    $("bCagr").className = "num " + cls(v.cagr);
    $("bMdd").textContent = "-" + (Math.abs(v.mdd) * 100).toFixed(1) + "%";
    $("bMdd").className = "num " + (v.mdd < 0 ? "down" : "");
    $("bWin").textContent = v.wins + "/" + v.n;
    $("bBench").textContent = pct2(v.bench, 0);
    $("bBench").className = "num " + cls(v.bench);
    var rg = $("allRange");
    if (rg) rg.textContent = v.from + "–" + v.to + " · " + v.n + " 年";
    CV_VIEW = v.norm;
  }

  function renderYears() {
    var years = ((SH && SH.years) || []).filter(function (x) { return x.year >= SHOW_FROM; });
    $("yearRows").innerHTML = years.slice().sort(function (a, b) { return b.year - a.year; }).map(function (r) {
      var holds = (r.holdings || []).map(function (h) {
        return '<span class="chip"><b>' + DSH.esc(h.ticker) + '</b><i>' +
          Math.round((h.weight || 0) * 100) + '%</i></span>';
      }).join('');
      return '<tr class="yr-row">' +
        '<th scope="row">' + DSH.esc(r.year) + '</th>' +
        '<td class="r yr-return ' + cls(r.ret) + '">' + pct2(r.ret, 1) + '</td>' +
        '<td class="r yr-drawdown ' + cls(r.dd == null ? null : -Math.abs(r.dd)) + '">' +
          pct2(r.dd == null ? null : -Math.abs(r.dd), 1) + '</td>' +
        '<td><div class="h">' + holds + '</div></td></tr>';
    }).join("");
  }

  /* ---------- 本金切换 ---------- */
  function initPills() {
    document.querySelectorAll(".pill-btn").forEach(function (b) {
      b.classList.toggle("on", +b.dataset.amt === amt);
      b.setAttribute("aria-pressed", String(+b.dataset.amt === amt));
      b.addEventListener("click", function () {
        amt = +b.dataset.amt;
        localStorage.setItem("dsh.amt", String(amt));
        document.querySelectorAll(".pill-btn").forEach(function (x) {
          x.classList.toggle("on", +x.dataset.amt === amt);
          x.setAttribute("aria-pressed", String(+x.dataset.amt === amt));
        });
        renderNow();
        renderYear();
      });
    });
  }

  function renderTick() {
    var q = Q && Q.quotes ? Q.quotes.SPY : null;
    if (!q) {
      $("tickPx").textContent = "SPY —";
      $("tickChg").textContent = "—";
      var tick = $("tickUpdated");
      if (tick) tick.textContent = "行情读取失败 · 正在重试";
      return;
    }
    $("tickPx").textContent = "SPY " + q.p.toFixed(2);
    $("tickChg").textContent = pct2(q.chg);
    $("tickChg").className = "chg " + cls(q.chg);
    var updated = $("tickUpdated");
    if (updated) {
      var stamp = Q.ts_local;
      if (!stamp && Q.ts && !isNaN(Date.parse(Q.ts))) {
        stamp = new Date(Q.ts).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
      }
      updated.textContent = stamp ? "行情时点 " + stamp + "（北京）" : "行情更新时间暂无";
    }
  }

  /* 分层自动刷新：不同卡片按各自数据变更频率更新，不再一刀切 60 秒全量重拉。
     L2 行情：仅交易时段每 60 秒，且只拉 quotes，重绘行情条
     L1 净值/持仓/预览：每 30 分钟重取一次并重绘卡片
     L0 曲线/历史/映射：TTL 24 小时，由数据层自行判定，不设独立定时器
     另有版本探测（app.js startVersionWatch）：数据库版本一变就精确失效并自动重绘，
     不需人工触发；切回标签页时立即探测。
     全部定时器在页面隐藏时静默，避免后台标签页持续消耗出口流量。 */
  function startAutoRefresh() {
    var busy = false;
    setInterval(async function () {
      if (busy || document.hidden || (DSH.isTradingHours && !DSH.isTradingHours())) return;
      busy = true;
      try {
        var q = await DSH.jsonNoCache("quotes", {});
        if (q && (q.ts || q.ts_local)) {
          Q = q; renderTick(); renderStamp(); checkFreshness();
          if (dataReady()) { renderNow(); renderYear(); renderPreview(); }
        }
      } finally { busy = false; }
    }, 60000);

    setInterval(async function () {
      if (busy || document.hidden) return;
      busy = true;
      try {
        await loadVariant(true);
        if (!dataReady()) { renderUnavailable("计算数据读取失败 · 正在重试"); return; }
        checkFreshness();
        renderAllCards();
      } finally { busy = false; }
    }, 30 * 60 * 1000);
  }

  /* ---------- 口径切换 ---------- */
  var VARIANT = localStorage.getItem("dsh.variant.v2") === "A" ? "A" : "B";  // 默认全年成分

  function vpath(name) {
    if (VARIANT === "B" && name !== "quotes" && name !== "name_zh") {
      return "b/" + name;
    }
    return name;
  }

  async function loadVariant(refresh) {
    var read = refresh ? DSH.jsonNoCache : DSH.json;
    var variant = VARIANT;
    var arr = await Promise.all([
      read(vpath("equity"), null),
      read(vpath("signal"), null),
      read("quotes", {}),
      DSH.json(vpath("curve"), null),
      DSH.json(vpath("strategy_history"), null),
      read(vpath("preview"), null),
      DSH.json("name_zh", {}),
      variant === "B" ? read("signal", null) : Promise.resolve(null),
      DSH.json("sectors", {})
    ]);
    if (variant !== VARIANT) return;
    EQ = arr[0]; SIG = arr[1]; Q = arr[2]; CV = arr[3]; SH = arr[4]; PV = arr[5]; ZH = arr[6] || {};
    SEC = arr[8] || {};

    // B 口径的 signal 不含大盘开关，用 A 的补齐（开关与选股口径无关）
    if (variant === "B" && SIG) {
      var sigA = arr[7];
      SIG.trend = sigA ? sigA.trend : null;
      SIG.spy = sigA ? sigA.spy : null;
      SIG.ma = sigA ? sigA.ma : null;
      SIG.rebalance_hint = sigA ? sigA.rebalance_hint : null;
    }
  }

  function applyVariantUI() {
    document.querySelectorAll(".vtab").forEach(function (b) {
      b.classList.toggle("on", b.dataset.variant === VARIANT);
      b.setAttribute("aria-pressed", String(b.dataset.variant === VARIANT));
    });
  }

  function renderAllCards() {
    renderTick();
    renderNow();
    renderYear();
    renderPreview();
    renderAll();
    renderYears();
    drawYearSpark();
    drawAllChart();
    renderStamp();
  }

  function renderStamp() {
    var s = DSH.dataStatus ? DSH.dataStatus() : null;
    /* 计划内的备库路由不在页脚标注「备用数据库」，避免被误读为故障；
       只有真的退回最近缓存时才说明数据出处。 */
    var tag = s && s.level && s.level !== "healthy" ? " · 最近缓存" : "";
    var foot = $("footStamp");
    if (foot) foot.textContent = "行情 " + (Q && Q.ts_local ? Q.ts_local.slice(5,16) : "暂无") +
      " · 收益 " + (EQ && EQ.updated ? EQ.updated.slice(5,16) : "暂无") + "（北京）" + tag;
  }

  /* ---------- 访客提示状态机（2026-11-08） ----------
     架构原则：多数据库冗余对访客应当「无感」。主库免费额度限服（402）后
     自动改读 Turso 实时备库是设计内的正常路径，不是故障，不得弹任何告警。
     只有以下真实异常才提示：
       ① 核心数据缺失、无法给出操作建议（error）
       ② 行情更新超时 / 云端 health 判定数据异常（warn）
       ③ 全部端点不可用且只能展示最近一次成功读取的数据（info，中性色）
     所有来源登记到 app.js 的统一状态中心，任一来源恢复都会重算并自动
     移除横幅，杜绝多处自建横幅互相覆盖、或故障恢复后红条残留。 */
  function setNotice(id, level, text) {
    if (DSH.reportNotice) { DSH.reportNotice(id, level, text); return; }
    /* 无状态中心的运行环境（如单测沙箱）：退化为本地横幅且不抛错 */
    if (typeof document === "undefined" || !document.body) return;
    var b = document.getElementById("freshBanner");
    if (!text) { if (b) { b.textContent = ""; b.style.display = "none"; } return; }
    if (!b) {
      b = document.createElement("div");
      b.id = "freshBanner";
      document.body.insertBefore(b, document.body.firstChild);
    }
    b.className = level === "info" ? "data-notice" : "data-alert";
    b.textContent = text;
    b.style.display = "block";
  }

  function linkNotice() {
    var s = DSH.dataStatus ? DSH.dataStatus() : null;
    /* level 才是唯一依据；mode=backup 只是路由快照，绝不触发提示 */
    if (!s || !s.level || s.level === "healthy") {
      setNotice("link", "info", "");
      return;
    }
    setNotice("link", "info", s.level === "offline"
      ? "在线连接暂时中断，正展示最近一次成功读取的数据；连接恢复后自动更新"
      : "部分数据暂由最近缓存补全，在线连接恢复后自动更新");
  }

  function renderServiceNotice() {
    if (!dataReady()) {
      var lastErr = (window.__dshErr || []).slice(-1)[0] || "";
      setNotice("page", "error", "在线数据源暂不可用，已隐藏操作建议" +
        (lastErr ? "（" + lastErr.split(" -> ").slice(-1)[0] + "）" : ""));
      linkNotice();
      return;
    }
    setNotice("page", "error", "");
    if (DSH.quoteStaleForDisplay ? DSH.quoteStaleForDisplay(Q) : DSH.quoteFreshness(Q) === true)
      setNotice("quotes", "warn", "行情更新超时");
    else setNotice("quotes", "warn", "");
    linkNotice();
  }

  function renderUnavailable(reason) {
    renderTick(); renderPreview(); renderAll(); renderYears(); drawAllChart(); renderStamp();
    showDegraded(reason);
  }

  function initVariants() {
    document.querySelectorAll(".vtab").forEach(function (b) {
      b.addEventListener("click", async function () {
        var v = b.dataset.variant;
        if (v === VARIANT) return;
        VARIANT = v;
        localStorage.setItem("dsh.variant.v2", v);
        applyVariantUI();
        await loadVariant();
        if (!dataReady()) { renderUnavailable("该口径的数据缺失 · 正在重试"); return; }
        renderAllCards();
      });
    });
    applyVariantUI();
  }

  /* 数据是否可用：核心文件缺失或持仓为空时，不能给出任何操作建议 */
  function dataReady() {
    return !!(EQ && EQ.position && EQ.position.length &&
              EQ.stats && isFinite(EQ.stats.strategy) &&
              SIG && (SIG.official || []).length);
  }

  function showDegraded(reason) {
    var v = $("verdict");
    if (v) { v.textContent = "数据未就绪"; v.className = "verdict"; }
    var vs = $("verdictSub");
    if (vs) vs.textContent = reason || "数据加载失败，请稍后刷新";
    ["tMv", "tPnl", "tCost"].forEach(function (id) {
      var e = $(id); if (e) { e.textContent = "—"; e.className = "num num-lg"; }
    });
    var exp = $("tExp"); if (exp) exp.textContent = "—";
    var ret = $("yearRet");
    if (ret) { ret.textContent = "—"; ret.className = "num num-xl"; }
    var vs2 = $("yearVs"); if (vs2) vs2.textContent = "";
    ["todayPct", "todayAmt", "switchTxt"].forEach(function (id) {
      var el = $(id); if (el) el.textContent = "—";
    });
    $("todayLabel").textContent = "单日策略表现";
    $("todayStamp").textContent = "收益数据待更新";
    $("switchSub").textContent = "大盘建议暂不可用";
    $("posRows").innerHTML = "";
    $("yearSpark").innerHTML = "";
    $("posMeta").textContent = "账户数据暂不可用";
    $("yearTitle").textContent = "当期策略收益";
    renderServiceNotice();   // 统一登记，避免与行情/健康提示叠成多条横幅
  }

  async function boot() {
    initPills();
    /* 常态只读在线库；镜像由故障路径按需懒加载，不预先拉取（单一数据源） */
    await loadVariant();
    initVariants();
    startAutoRefresh();
    /* 数据库版本变化 → 精确失效缓存 → 自动重绘，不依赖人工触发/手工调校 */
    if (DSH.startVersionWatch) DSH.startVersionWatch(async function () {
      await loadVariant(false);
      if (!dataReady()) { renderUnavailable("计算数据读取失败 · 正在重试"); return; }
      checkFreshness();
      renderAllCards();
    });

    if (dataReady()) { checkFreshness(); renderAllCards(); }
    else renderUnavailable("在线数据源暂不可用 · 正在重试");

    var _rt = null;
    window.addEventListener("resize", function () {
      clearTimeout(_rt);
      _rt = setTimeout(function () { drawYearSpark(); drawAllChart(); }, 180);
    });

  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();

/* Section navigation reflects the reading position and keyboard activation. */
(function () {
  var links = Array.prototype.slice.call(document.querySelectorAll('.section-nav a'));
  function activate(id) {
    links.forEach(function (link) {
      var selected = link.getAttribute('href') === '#' + id;
      link.classList.toggle('active', selected);
      if (selected) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    });
  }
  links.forEach(function (link) {
    link.addEventListener('click', function () {
      activate(link.hash.slice(1));
      if (link.hash === '#annual') document.querySelector('#annual details').open = true;
    });
  });
  if ('IntersectionObserver' in window) {
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) { if (entry.isIntersecting) activate(entry.target.id); });
    }, { rootMargin: '-90px 0px -55% 0px', threshold: 0 });
    links.forEach(function (link) {
      var target = document.querySelector(link.getAttribute('href'));
      if (target) observer.observe(target);
    });
  }
})();
