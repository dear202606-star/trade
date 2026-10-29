const fs=require('fs');const D='C:/Users/Administrator/Desktop/量化/_audit/data/';
const cv=JSON.parse(fs.readFileSync(D+'curve.json','utf8'));
const sh=JSON.parse(fs.readFileSync(D+'strategy_history.json','utf8'));
const pct=v=>v==null?'-':(v*100).toFixed(1)+'%';
console.log('CV years vs SH years');
cv.years.forEach(y=>{const s=sh.years.find(x=>x.year===y.year);
 console.log(y.year,'cv ret',pct(y.ret),'bench',pct(y.bench),'dd',pct(y.dd),'| sh ret',pct(s.ret),'bench',pct(s.bench),'dd',pct(s.dd),'| diff',pct((y.ret||0)-(s.ret||0)));});
console.log('\nstocks count',sh.stocks.length);
sh.stocks.slice(0,8).forEach(s=>console.log(s.ticker,s.name.slice(0,20),s.years.join('/'),s.n,pct(s.total_ret),pct(s.avg_ret)));
console.log('\ncurve points first/last',cv.points[0],cv.points[cv.points.length-1]);
console.log('curve years sample 2024',cv.years.find(y=>y.year===2024));
// DSH.combo axis: total curve max
const eqs=cv.points.map(p=>p.y===undefined?p.s-1:p.s-1);
console.log('max eq',Math.max(...eqs));