# -*- coding: utf-8 -*-
"""dashboard.py — 把信号历史渲染成自包含的可交互 HTML（离线可用，无 CDN 依赖）"""
import json


def build(history, path="output/dashboard.html"):
    data = json.dumps(history, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>美股排名动量 · 信号看板</title>
<style>
:root{{--bg:#0d1117;--card:#161d2c;--line:#2a3346;--txt:#e6ecf5;--dim:#93a4bd;--gold:#ffc857;
--green:#2ecc71;--red:#ff5a5f;--blue:#4da3ff}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--txt);font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;
line-height:1.7;font-size:15px}}
.wrap{{max-width:1080px;margin:0 auto;padding:40px 24px 90px}}
h1{{font-size:26px;margin:0 0 4px;letter-spacing:-.4px}}
.sub{{color:var(--dim);font-size:13px;margin-bottom:22px}}
.tabs{{display:flex;gap:8px;margin:20px 0 22px;border-bottom:1px solid var(--line)}}
.tab{{padding:9px 16px;cursor:pointer;color:var(--dim);font-size:14px;border-bottom:2px solid transparent;user-select:none}}
.tab.on{{color:var(--gold);border-bottom-color:var(--gold)}}
.panel{{display:none}} .panel.on{{display:block}}
.hero{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:6px 0 18px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px}}
.card .v{{font-size:24px;font-weight:800;color:var(--gold);letter-spacing:-.6px}}
.card .l{{font-size:12px;color:var(--dim);margin-top:2px}}
.card .s{{font-size:11.5px;color:#6f8199;margin-top:6px}}
.pill{{display:inline-block;padding:3px 10px;border-radius:20px;font-size:12.5px;font-weight:700}}
.on-pill{{background:#12351f;color:#5fe08a}} .off-pill{{background:#3a2018;color:#ffab6b}}
table{{width:100%;border-collapse:collapse;font-size:13.5px;margin:10px 0}}
th{{background:#1b2434;color:#aab9cf;font-weight:600;text-align:left;padding:9px 8px;
border-bottom:1px solid var(--line);font-size:12.5px}}
td{{padding:9px 8px;border-bottom:1px solid #1e2637}}
tr:hover td{{background:#182033}}
.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.tk{{font-weight:700;color:var(--gold);font-family:Consolas,monospace}}
.bar{{height:6px;border-radius:3px;background:var(--blue);min-width:2px}}
.note{{background:#141b28;border-left:3px solid var(--blue);border-radius:8px;padding:12px 16px;
font-size:13.5px;color:#c3cfe0;margin:14px 0}}
.act{{border-left-color:var(--gold);background:#1a1a12;color:#ffe6a8}}
.sel{{background:var(--card);color:var(--txt);border:1px solid var(--line);border-radius:8px;
padding:7px 10px;font-size:13.5px;font-family:inherit}}
.hist-row{{display:flex;gap:14px;align-items:center;padding:11px 0;border-bottom:1px solid #1e2637}}
.hist-m{{color:var(--dim);font-family:Consolas,monospace;width:74px}}
.tag{{font-size:11px;padding:1px 7px;border-radius:4px;background:#1b2434;color:#9fb2cb}}
.foot{{color:#6f8199;font-size:11.5px;margin-top:36px;border-top:1px solid var(--line);padding-top:14px}}
h3{{font-size:15px;color:var(--gold);margin:24px 0 6px}}
</style></head><body><div class="wrap">

<h1>美股排名动量 · 信号看板</h1>
<div class="sub">策略：每年 12 月末按过去 12 个月涨幅取前 3 名等权，全年持有；标普 500 跌破 10 月均线时仓位减半</div>

<div class="tabs">
  <div class="tab on" data-p="now">本期信号</div>
  <div class="tab" data-p="hist">历史记录</div>
  <div class="tab" data-p="how">策略说明</div>
</div>

<div class="panel on" id="now">
  <div id="action"></div>
  <div class="hero" id="hero"></div>
  <div id="official"></div>
  <div id="picks"></div>
</div>

<div class="panel" id="hist">
  <div style="margin:12px 0">
    <label style="color:var(--dim);font-size:13px">选择月份：</label>
    <select class="sel" id="monthSel"></select>
  </div>
  <div id="histDetail"></div>
  <h3>全部记录</h3>
  <div id="histList"></div>
</div>

<div class="panel" id="how">
  <div class="note"><b>选股</b>：股票池为当前标普 500 成分股；剔除股价 &lt; 5 美元、过去 12 个月日均成交额 &lt; 2000 万美元的标的；按过去 12 个自然月涨幅排序。</div>
  <div class="note"><b>持仓</b>：前 3 名；同一 GICS 行业最多 2 只，若出现 2 只同行业，该行业权重上限 60%，剩余给第三只。</div>
  <div class="note"><b>换仓</b>：<b>每年 12 月最后一个交易日</b>按当时排名换仓，之后全年不变。其余月份只检查大盘开关，不动持仓——这是回测验证过的关键纪律（改成月度再平衡会让年化掉约 8 个百分点）。</div>
  <div class="note"><b>开关</b>：标普 500 上月收盘低于 10 个月均线时，整体仓位降至 50%；否则满仓。</div>
  <div class="note"><b>仓位</b>：策略总仓位 ≤ 股票资产的 30%（上表"占总资产"已按此折算），单只 ≤ 总资产 10%，不设个股止损。</div>
  <div class="note" style="border-left-color:var(--red)"><b>风险</b>：回测存在生存者偏差（退市股缺失），真实收益预计打 7–8 折；行业集中度高，单赛道崩塌会同步回撤。本页面仅供研究，不构成投资建议。</div>
</div>

<div class="foot">数据来源：新浪财经日线 · 标普500 成分。页面由 GitHub Actions 每月自动生成。</div>
</div>

<script>
const DATA = {data};
const fmt = (x,d=1)=> (x*100>=0?'+':'') + (x*100).toFixed(d) + '%';
const esc = s => String(s==null?'':s).replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));

document.querySelectorAll('.tab').forEach(t=>{{
  t.onclick=()=>{{
    document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));
    document.querySelectorAll('.panel').forEach(x=>x.classList.remove('on'));
    t.classList.add('on'); document.getElementById(t.dataset.p).classList.add('on');
  }};
}});

function renderNow(rec){{
  if(!rec){{document.getElementById('now').innerHTML='<div class="note">暂无数据</div>';return;}}
  const on = rec.trend === '满仓';
  const off = rec.official || rec.picks || [];

  document.getElementById('action').innerHTML =
    '<div class="note act"><b>本月动作：</b>' + esc(rec.action || '—') + '</div>';

  document.getElementById('hero').innerHTML = `
    <div class="card"><div class="v">${{esc(rec.month)}}</div><div class="l">最新信号月份</div>
      <div class="s">生成于 ${{esc(rec.date)}}</div></div>
    <div class="card"><div class="v"><span class="pill ${{on?'on-pill':'off-pill'}}">${{esc(rec.trend)}}</span></div>
      <div class="l">大盘开关</div><div class="s">目标仓位 ${{(rec.exposure*100).toFixed(0)}}%</div></div>
    <div class="card"><div class="v">${{rec.spy.toFixed(0)}}</div><div class="l">SPY / 10月均线 ${{rec.ma.toFixed(0)}}</div>
      <div class="s">${{on?'在均线上方':'跌破均线'}}</div></div>`;

  document.getElementById('official').innerHTML = `
    <h3>正式持仓（${{esc(rec.official_month||'—')}} 锁定，本年度有效）</h3>
    <table><thead><tr><th>#</th><th>代码</th><th>公司</th><th>行业</th>
    <th class="num">基准涨幅</th><th class="num">策略内权重</th><th class="num">占总资产</th></tr></thead><tbody>
    ${{off.map(p=>`<tr><td>${{p.rank}}</td><td class="tk">${{esc(p.ticker)}}</td><td>${{esc(p.name)}}</td>
      <td>${{esc(p.sector)}}</td><td class="num" style="color:var(--green)">${{fmt(p.ret12,0)}}</td>
      <td class="num">${{(p.weight*100).toFixed(0)}}%</td>
      <td class="num"><b>${{(p.weight*rec.exposure*0.30*100).toFixed(1)}}%</b></td></tr>`).join('')}}
    </tbody></table>`;

  const bench = (rec.bench||[]).slice(0,7);
  document.getElementById('picks').innerHTML = bench.length ? `
    <h3>当月排名（若在此换仓会选谁 · 仅供参考）</h3>
    <table><thead><tr><th>#</th><th>代码</th><th>行业</th><th class="num">12月涨幅</th></tr></thead><tbody>
    ${{bench.map(p=>`<tr><td>${{p.rank}}</td><td class="tk">${{esc(p.ticker)}}</td>
      <td>${{esc(p.sector)}}</td><td class="num">${{fmt(p.ret12,0)}}</td></tr>`).join('')}}
    </tbody></table>` : '';
}}

function renderHist(rec){{
  if(!rec) return;
  const on = rec.trend==='满仓';
  const off = rec.official || rec.picks || [];
  document.getElementById('histDetail').innerHTML = `
    <div class="hero" style="grid-template-columns:repeat(3,1fr)">
      <div class="card"><div class="v">${{esc(rec.month)}}</div><div class="l">月份</div></div>
      <div class="card"><div class="v"><span class="pill ${{on?'on-pill':'off-pill'}}">${{esc(rec.trend)}}</span></div>
        <div class="l">大盘开关</div><div class="s">仓位 ${{(rec.exposure*100).toFixed(0)}}%</div></div>
      <div class="card"><div class="v">${{off.map(p=>esc(p.ticker)).join(' / ')}}</div><div class="l">正式持仓</div></div>
    </div>
    <div class="note act"><b>动作：</b>${{esc(rec.action||'—')}}</div>`;
}}

function initHist(){{
  const sel = document.getElementById('monthSel');
  sel.innerHTML = DATA.map((r,i)=>`<option value="${{i}}">${{esc(r.month)}}（${{esc(r.trend)}}）</option>`).join('');
  sel.onchange = ()=> renderHist(DATA[+sel.value]);
  document.getElementById('histList').innerHTML = DATA.map(r=>{{
    const on = r.trend==='满仓';
    const off = r.official || r.picks || [];
    return `<div class="hist-row">
      <div class="hist-m">${{esc(r.month)}}</div>
      <span class="pill ${{on?'on-pill':'off-pill'}}" style="font-size:11px;padding:1px 8px">${{esc(r.trend)}}</span>
      <div style="flex:1">${{off.map(p=>`<span class="tag">${{esc(p.ticker)}} ${{(p.weight*100).toFixed(0)}}%</span>`).join(' ')}}</div>
      <div style="color:var(--dim);font-size:12px">${{esc(r.date)}}</div>
    </div>`;
  }}).join('');
  if(DATA.length) renderHist(DATA[0]);
}}

renderNow(DATA[0]);
initHist();
</script>
</body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path
