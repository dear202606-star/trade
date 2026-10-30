/* 单页渲染：一次加载，全部可见，无 Tab、无隐藏内容 */
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var EQ = null, SIG = null, Q = null, CV = null, SH = null, PV = null, ZH = {};
  var amt = +(localStorage.getItem("dsh.amt") || 100000);

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

  /* ---------- ① 今日 ---------- */
  function renderNow() {
    var off = (SIG && SIG.official) || [];
    var trend = (SIG && SIG.trend) || "—";
    var hold = trend === "满仓";

    $("verdict").textContent = hold ? "持有不动" : "仓位减半";
    $("verdictSub").textContent = hold
      ? "下次换仓 " + ((SIG && SIG.rebalance_hint) || "12 月末")
      : "大盘跌破均线 · 建议减仓";

    var t = DSH.today(SIG, Q, EQ);
    if (t) {
      $("todayPct").textContent = pct2(t.pct);
      $("todayPct").className = "num num-lg " + cls(t.pct);
      $("todayAmt").textContent = yuan(t.amount);
      $("todayAmt").className = "sub " + cls(t.amount);
      $("todayLabel").textContent = "今日 · " + (t.live ? "盘中" : "收盘");
    }

    $("switchTxt").textContent = trend;
    $("switchSub").textContent = "SPY " + spyNow() + " / " +
      (SIG && SIG.ma ? SIG.ma.toFixed(0) : "—");
  }

  /* ---------- ② 当期账户 ---------- */
  function account() {
    var pos = (EQ && EQ.position) || [];
    var exposure = (EQ && EQ.exposure != null) ? EQ.exposure : 1;
    var rows = [], mv = 0, cost = 0;
    pos.forEach(function (p) {
      var shares = p.entry > 0 ? (amt * p.weight * exposure) / p.entry : 0;
      var c = shares * p.entry, v = shares * p.last;
      mv += v; cost += c;
      rows.push({ ticker: p.ticker, weight: p.weight, entry: p.entry, last: p.last,
        entry_raw: p.entry_raw, last_raw: p.last_raw,
        shares: shares, mv: v, cost: c });
    });
    var cash = amt * (1 - exposure);
    return { rows: rows, mv: mv + cash, cost: cost, pnl: mv - cost,
      pnlPct: cost > 0 ? (mv - cost) / cost : null, exposure: exposure };
  }

  function renderYear() {
    var y = EQ && EQ.base_date ? (+String(EQ.base_date).slice(0, 4) + 1) : "";
    $("yearTitle").textContent = (y ? y + " 年" : "") + "策略收益";
    var c = account();
    $("yearRet").textContent = pct2(c.pnlPct);
    $("yearRet").className = "num num-xl " + cls(c.pnlPct);

    var bench = EQ && EQ.stats ? EQ.stats.benchmark : null;
    $("yearVs").innerHTML = "同期标普 <span class='" + cls(bench) + "'>" + pct2(bench) + "</span>" +
      (c.pnlPct != null && bench != null
        ? " ｜ 跑赢 <span class='" + cls(c.pnlPct - bench) + "'>" + pct2(c.pnlPct - bench) + "</span>" : "");

    $("tMv").textContent = yuan(c.mv);
    $("tPnl").textContent = yuan(c.pnl);
    $("tPnl").className = "num num-md " + cls(c.pnl);
    $("tCost").textContent = yuan(c.cost);
    $("tExp").textContent = Math.round(c.exposure * 100) + "%";

    var q = (Q && Q.quotes) || {};
    var bdTxt = (EQ && EQ.base_date)
      ? EQ.base_date.slice(0, 4) + "-" + EQ.base_date.slice(4, 6) + "-" + EQ.base_date.slice(6, 8) : "—";
    $("posTitle").textContent = "当前持仓明细";
    var pm = $("posMeta");
    if (pm) pm.textContent = (y ? (y - 1) + "-12 选出 · " : "") + (bdTxt ? "建仓 " + bdTxt : "");
    $("posRows").innerHTML = c.rows.map(function (r, i) {
      var qq = q[r.ticker] || {};
      var eraw = (r.entry_raw != null) ? r.entry_raw : r.entry;
      var lraw = (r.last_raw != null) ? r.last_raw : r.last;
      // 实时价（原始口径）→ 换算成复权价用于市值/盈亏，拆股也连续
      var liveRaw = (isFinite(qq.p) && qq.p > 0) ? qq.p : lraw;
      var liveAdj = (eraw && r.entry) ? (liveRaw / eraw) * r.entry : r.last;
      var mvAdj = r.shares * liveAdj;
      var pnlAdj = mvAdj - r.cost;
      return '<div class="pr-row">' +
        '<div><span class="tk">' + DSH.esc(r.ticker) + '</span>' +
          '<div class="nm">建仓 ' + eraw.toFixed(2) + '</div>' +
          '<div class="nm">现价 ' + liveRaw.toFixed(2) + '</div></div>' +
        '<div class="r">' + Math.round(r.weight * 100) + '%</div>' +
        '<div class="r">' + eraw.toFixed(2) + '</div>' +
        '<div class="r">' + liveRaw.toFixed(2) + '</div>' +
        '<div class="r">' + yuan(mvAdj) + '</div>' +
        '<div class="r ' + cls(liveAdj - r.entry) + '">' + pct2(liveAdj / r.entry - 1) + '</div>' +
        '<div class="r ' + cls(pnlAdj) + '">' + yuan(pnlAdj) + '</div>' +
        '</div>';
    }).join("");
  }

  function drawYearSpark() {
    var box = $("yearSpark");
    if (!box || !EQ || !EQ.points || EQ.points.length < 3) return;
    try {
      box.innerHTML = DSH.spark(EQ.points.map(function (p) { return { y: p.s }; }),
        { h: 118, color: "#F2C46B" });
    } catch (e) { box.innerHTML = ""; }
  }

  /* 数据停更检测：按用户当前时间算，不依赖构建时的健康快照 */
  function checkFreshness() {
    var ts = Q && Q.ts;
    if (!ts) return;
    var ageH = (Date.now() - Date.parse(ts)) / 36e5;
    if (!isFinite(ageH) || ageH <= 26) return;
    var bar = document.getElementById("freshBanner");
    if (!bar) {
      bar = document.createElement("div");
      bar.id = "freshBanner";
      bar.style.cssText = "position:sticky;top:0;z-index:99;background:rgba(255,90,95,.16);" +
        "border-bottom:1px solid rgba(255,90,95,.4);color:#FFB3B6;font-size:12.5px;" +
        "padding:8px 16px;text-align:center";
      document.body.insertBefore(bar, document.body.firstChild);
    }
    bar.textContent = "⚠ 行情已 " + Math.round(ageH) + " 小时未更新";
  }

  /* 全站统一 SPY 价格：优先实时行情，其次用信号里的收盘价 */
  function spyNow() {
    var q = (Q && Q.quotes && Q.quotes.SPY) || null;
    if (q && isFinite(q.p) && q.p > 0) return q.p.toFixed(0);
    if (SIG && isFinite(SIG.spy)) return Number(SIG.spy).toFixed(0);
    return "—";
  }

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
    if (!box || !CV || !CV.points || CV.points.length < 4) return;
    try {
      box.innerHTML = DSH.lineChart([
        { name: "策略", color: "#FF6B72", pts: CV.points.map(function (p) { return { d: ymd(p.d), y: p.s, label: yearLab(p.d) }; }) },
        { name: "标普500", color: "#6F7D92", pts: CV.points.map(function (p) { return { d: ymd(p.d), y: p.b, label: yearLab(p.d) }; }) }
      ], { h: 210, w: 420, fs: 14, l: 46, b: 34, sw: 2.4, log: true, xOnly: true,
        fmt: function (v) { return v >= 10 ? Math.round(v) + "×" : v.toFixed(1) + "×"; } });
    } catch (e) { box.innerHTML = ""; }
  }

  /* ---------- 明年预览 ---------- */
  function renderPreview() {
    var box = $("previewRows");
    if (!box || !PV) return;
    var y = PV.hold_year || "";
    $("nextYearTitle").textContent = y ? (y + " 年可能持仓") : "明年可能持仓";
    $("previewNote").textContent = PV.final ? "已定稿 · 与正式持仓一致" : "年初至今涨幅 · 年末定稿";
    var SEC = {
      "Information Technology": "信息技术",
      "Communication Services": "通信服务",
      "Consumer Discretionary": "可选消费",
      "Consumer Staples": "必需消费",
      "Health Care": "医疗保健",
      "Financials": "金融",
      "Industrials": "工业",
      "Energy": "能源",
      "Utilities": "公用事业",
      "Materials": "原材料",
      "Real Estate": "房地产"
    };
    box.innerHTML = (PV.top || []).map(function (r) {
      var sec = SEC[r.sector] || r.sector || "";
      return '<div class="pv-row">' +
        '<div class="pv-rank">' + (r.rank || "") + '</div>' +
        '<div class="pv-main"><div class="tk">' + DSH.esc(r.ticker) + '</div>' +
          '</div>' +
        '<div class="pv-sec">' + DSH.esc(sec) + '</div>' +
        '<div class="pv-val ' + cls(r.ytd) + '">' + pct2(r.ytd, 1) + '</div>' +
        '</div>';
    }).join("");
  }

  /* ---------- ⑤ 全周期 ---------- */
  function renderAll() {
    if (!SH || !SH.summary) return;
    var s = SH.summary;
    $("bTotal").textContent = pct2(s.total, 0);
    $("bTotal").className = "num num-xl " + cls(s.total);
    $("bCagr").textContent = pct2(s.cagr, 1);
    $("bMdd").textContent = "-" + (Math.abs(s.worst_intra || 0) * 100).toFixed(1) + "%";
    $("bMdd").className = "num num-md down";
    $("bWin").textContent = s.wins + "/" + s.years;
    var rg = $("allRange");
    if (rg) {
      rg.textContent = (s.start && s.end)
        ? (s.start + "–" + s.end + " · " + s.years + " 年") : "";
    }
    var bench = (SH.years || []).reduce(function (a, r) { return a * (1 + (r.bench || 0)); }, 1) - 1;
    $("bBench").textContent = pct2(bench, 0);
  }

  /* ---------- ⑤ 年度 ---------- */
  function renderYears() {
    var years = (SH && SH.years) || [];
    $("yearRows").innerHTML = years.slice().reverse().map(function (r) {
      var holds = (r.holdings || []).map(function (h) {
        return '<span class="chip"><b>' + DSH.esc(h.ticker) + '</b><i>' +
          Math.round((h.weight || 0) * 100) + '%</i></span>';
      }).join('');
      return '<div class="yr-row">' +
        '<div>' + r.year + '</div>' +
        '<div class="h">' + holds + '</div>' +
        '<div class="r ' + cls(r.ret) + '">' + pct2(r.ret, 1) + '</div>' +
        '<div class="r down">-' + (Math.abs(r.dd || 0) * 100).toFixed(1) + '%</div>' +
        '</div>';
    }).join("");
  }

  /* ---------- 本金切换 ---------- */
  function initPills() {
    document.querySelectorAll(".pill-btn").forEach(function (b) {
      if (+b.dataset.amt === amt) b.classList.add("on");
      b.addEventListener("click", function () {
        amt = +b.dataset.amt;
        localStorage.setItem("dsh.amt", String(amt));
        document.querySelectorAll(".pill-btn").forEach(function (x) {
          x.classList.toggle("on", +x.dataset.amt === amt);
        });
        renderYear();
      });
    });
  }

  function renderTick() {
    var q = Q && Q.quotes ? Q.quotes.SPY : null;
    if (!q) return;
    $("tickPx").textContent = "SPY " + q.p.toFixed(2);
    $("tickChg").textContent = pct2(q.chg);
    $("tickChg").className = "chg " + cls(q.chg);
  }

  async function boot() {
    initPills();
    try {
      var arr = await Promise.all([
        DSH.json("equity", null), DSH.json("signal", null), DSH.json("quotes", {}),
        DSH.json("curve", null), DSH.json("strategy_history", null),
        DSH.json("preview", null), DSH.json("name_zh", {})
      ]);
      EQ = arr[0]; SIG = arr[1]; Q = arr[2]; CV = arr[3]; SH = arr[4]; PV = arr[5]; ZH = arr[6] || {};
    } catch (e) {}

    checkFreshness();
    renderTick();
    renderNow();
    renderYear();
    renderPreview();
    renderPreview();
    renderAll();
    renderYears();
    drawYearSpark();
    drawAllChart();

    var when = (Q && Q.ts_local) || (EQ && EQ.updated) || "";
    if (when) $("footStamp").textContent = "数据更新 " + when.slice(5, 16) + "（北京）";
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
