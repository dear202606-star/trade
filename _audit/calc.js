const fs=require('fs');
const D='C:/Users/Administrator/Desktop/量化/_audit/data/';
const sig=JSON.parse(fs.readFileSync(D+'signal.json','utf8'));
const eq=JSON.parse(fs.readFileSync(D+'equity.json','utf8'));
const q=JSON.parse(fs.readFileSync(D+'quotes.json','utf8'));
const cv=JSON.parse(fs.readFileSync(D+'curve.json','utf8'));
const pct=(v,d=2)=>v==null||isNaN(v)?'—':(v>=0?'+':'')+(v*100).toFixed(d)+'%';
const money=(v,d)=>{if(v==null||isNaN(v))return '—';const a=Math.abs(v);const s=a>=1e4?Math.round(a).toLocaleString('en-US'):a.toLocaleString('en-US',{maximumFractionDigits:d==null?2:d});return (v<0?'-':'')+'$'+s;};
const pts=eq.points, p0=pts[0], pn=pts[pts.length-1];
const r=pn.s/p0.s-1, br=pn.b/p0.b-1;
console.log('=== 首页 策略卡 ===');
console.log('策略累计收益', pct(r), '| 同期标普', pct(br), '| 跑赢', pct(r-br), '| 期间', p0.d, '起 ·', pts.length, '日');
// DSH.today
let w=0,chg=0;
sigs=sig.official;
for(const p of sig.official){const qq=q.quotes[p.ticker]; if(qq&&isFinite(qq.chg)&&p.weight){chg+=p.weight*qq.chg; w+=p.weight;}}
const tp=chg/w;
console.log('今日 pct', pct(tp), '| 首页金额(按本金10万)', money(100000*tp), '| 报价 session', q.session, 'ts', q.ts, 'ts_local', q.ts_local);
// portfolio build
const amt=100000, exp=eq.exposure!=null?eq.exposure:1;
let mv=0,cost=0,pnl=0;
for(const p of eq.position){
  const budget=amt*(p.weight||0)*exp; const shares=p.entry>0?budget/p.entry:null;
  const mvNow=shares!=null&&p.last>0?shares*p.last:null;
  const pnlNow=mvNow!=null?mvNow-budget:null;
  mv+=mvNow; cost+=budget; pnl+=pnlNow;
  console.log(`  ${p.ticker} 权重${(p.weight*100).toFixed(0)}% 建仓${p.entry} 现价${p.last} 市值${money(mvNow)} 盈亏${money(pnlNow)} 盈亏%${pct(pnlNow/budget)} 现权重${pct(mvNow/mv)}`);
}
const cash=amt*(1-exp); const mvAll=mv+cash;
console.log('总市值', money(mvAll), '| 建仓成本', money(amt*exp), '| 累计盈亏', money(mvAll-amt), '| 收益率', pct((mvAll-amt)/amt), '| 仓位', Math.round(exp*100)+'%');
const dayPnl=eq.position.reduce((a,p)=>{const qq=q.quotes[p.ticker]; const budget=amt*p.weight*exp; const sh=budget/p.entry; const m=sh*p.last; return a+(qq&&qq.chg!=null?m*(qq.chg/(1+qq.chg)):0);},0);
console.log('账户页今日金额', money(dayPnl), '| 今日%', pct(dayPnl/(mvAll-dayPnl)));
// strategy
let peak=0,mdd=0; pts.forEach(p=>{peak=Math.max(peak,p.s);mdd=Math.min(mdd,p.s/peak-1);});
console.log('=== 策略页 当前持仓 ===');
console.log('策略收益', pct(r), '标普同期', pct(br), '跑赢', pct(r-br), '最大回撤', pct(mdd,1));
console.log('=== 全周期 ===');
console.log('累计收益', pct(cv.stats.total,0), '年化', pct(cv.stats.cagr), '标普', pct(cv.stats.bench_total,0), '最大回撤', pct(cv.stats.mdd,1));
console.log('月份数', cv.points.length, '首末', cv.points[0].d, cv.points[cv.points.length-1].d);
// DSH.ret for total
const ret=(v)=>{const p=v*100;const abs=Math.abs(p);const num=abs>=1000?abs.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g,','):abs.toFixed(1);return (p>=0?'+':'-')+num+'%';};
console.log('DSH.ret(total)', ret(cv.stats.total), '| DSH.ret(bench)', ret(cv.stats.bench_total));
console.log('=== SPY/quotes sanity ===');
console.log('SPY', q.quotes.SPY.p, q.quotes.SPY.pc, pct(q.quotes.SPY.chg), '| count', q.count, 'coverage', q.coverage, 'stale', JSON.stringify(q.stale));