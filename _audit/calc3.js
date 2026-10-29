const fs=require('fs');
const D='C:/Users/Administrator/Desktop/量化/_audit/data/';
const q=JSON.parse(fs.readFileSync(D+'quotes.json','utf8')).quotes;
const eq=JSON.parse(fs.readFileSync(D+'equity.json','utf8'));
const amt=1e5, exp=1;
let mvPrev=0, mvNow=0, day=0;
for(const p of eq.position){
  const sh=amt*p.weight*exp/p.entry; const qq=q[p.ticker];
  mvPrev+=sh*qq.pc; mvNow+=sh*qq.p; day+=sh*(qq.p-qq.pc);
  console.log(p.ticker, 'shares',sh.toFixed(3),'pc',qq.pc,'p',qq.p,'mvPrev',(sh*qq.pc).toFixed(0),'mvNow',(sh*qq.p).toFixed(0));
}
console.log('mvPrev',mvPrev.toFixed(0),'mvNow',mvNow.toFixed(0),'day$',day.toFixed(0),'day%',(day/mvPrev*100).toFixed(3));
console.log('page 今日: pct', (0.3*q.LITE.chg+0.3*q.WDC.chg+0.4*q.HOOD.chg).toFixed(6));
console.log('weights now', (eq.position.map(p=>{const sh=amt*p.weight/p.entry;return [p.ticker,(sh*q[p.ticker].p/mvNow*100).toFixed(1)];})));