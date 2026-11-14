import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';

function page(read, opts={}) {
  const calls=[], paints=[], elements=new Map(), notices=[];
  const document={readyState:'loading',hidden:false,addEventListener(){},querySelectorAll(){return [];},
    getElementById(id){
      if (!elements.has(id)) elements.set(id,{textContent:'',className:'',innerHTML:'',style:{}});
      return elements.get(id);
    }};
  let tick;
  const context=vm.createContext({document,window:{},localStorage:{getItem(){return null;},setItem(){}},
    DSH:{jsonNoCache:async(name,fallback)=>{calls.push(name);return read(name,fallback);},
      json:async(name,fallback)=>read(name,fallback),previewCurrent:()=>false,previewVerified:()=>false,
      reportNotice:(id,level,text)=>notices.push({id,level,text}),
      dataStatus:()=>opts.status||{level:'healthy',mode:'backup'},
      closingQuoteOverdue:()=>opts.staleQuotes===true,
      inClosingRefreshWindow:()=>opts.windowOpen!==false,
      syncIfChanged:async()=>opts.changed||['quotes','b/preview']},
    setInterval(fn){tick=fn;},console});
  const source=readFileSync(new URL('./site_src/assets/page.js',import.meta.url),'utf8');
  vm.runInContext(source.replace('  if (document.readyState === "loading")',`
    window.testPage = {startAutoRefresh, loadVariant, renderYear, renderServiceNotice,
      state:()=>({EQ,PV,Q}), switchTo:v=>{VARIANT=v;},
      seed:(eq,sig,q)=>{EQ=eq;SIG=sig;Q=q;}};
    renderAllCards=()=>window.paint({EQ,PV,Q});
    checkFreshness=()=>window.checked++;
    renderQuality=()=>{};
    if (document.readyState === "loading")`),context);
  context.window.paint=s=>paints.push(s);
  context.window.checked=0;
  return {api:context.window.testPage,context,calls,paints,elements,notices,tick:()=>tick()};
}

test('unchanged quotes still refresh dependent artifacts and recheck deadlines',async()=>{
  let revision=0;
  const p=page(name=>name==='quotes'?{ts:'same',version:'same'}:
    name.endsWith('equity')?{position:[{}],stats:{strategy:revision},revision}:
    name.endsWith('signal')?{official:[{}]}:{revision});
  p.api.startAutoRefresh();
  await new Promise(resolve=>setImmediate(resolve));
  revision=1;await p.tick();
  assert.equal(p.paints.length,2);
  assert.equal(p.paints[1].EQ.revision,1);
  assert.equal(p.paints[1].PV.revision,1);
  assert.equal(p.context.window.checked,2);
  for(const name of ['b/equity','b/signal','b/preview','quotes'])
    assert.equal(p.calls.filter(n=>n===name).length,2);
  assert.equal(p.calls.some(n=>['b/curve','b/strategy_history','name_zh'].includes(n)),false);
});

test('an open dashboard does not fetch outside the closing window',async()=>{
  const p=page(()=>null,{windowOpen:false});
  p.api.startAutoRefresh();
  await p.tick();
  assert.deepEqual(p.calls,[]);
});

test('an old variant response cannot overwrite a newly selected variant',async()=>{
  let release;
  const gate=new Promise(resolve=>{release=resolve;});
  const p=page(async()=>{await gate;return {old:true};});
  const pending=p.api.loadVariant(true);
  p.api.switchTo('A');release();await pending;
  assert.equal(p.api.state().EQ,null);
});

test('a failed account refresh clears the prior variant positions instead of leaving ghost values',async()=>{
  const opts={windowOpen:false};
  const p=page(()=>null,opts);p.api.startAutoRefresh();
  opts.windowOpen=true;
  const positions=p.context.document.getElementById('posRows');positions.innerHTML='old variant holdings';
  const daily=p.context.document.getElementById('todayPct');daily.textContent='+9%';
  await p.tick();
  assert.equal(positions.innerHTML,'');assert.equal(daily.textContent,'—');
  assert.equal(p.context.document.getElementById('verdict').textContent,'数据未就绪');
});

/* ---------- 2026-11-08：顶部提示按数据健康度分级，备库路由不再误报 ---------- */
const readyData=()=>[{position:[{ticker:'X'}],stats:{strategy:0.1}},{official:[{}]},{}];

test('healthy backup routing shows no visitor-facing banner at all',()=>{
  const p=page(()=>null,{status:{level:'healthy',mode:'backup',activeEndpoint:'turso'}});
  p.api.seed(...readyData());
  p.api.renderServiceNotice();
  assert.deepEqual(p.notices.filter(n=>n.text),[],'备库正常供数必须零提示');
});

test('quote staleness still warns while the healthy link stays quiet',()=>{
  const p=page(()=>null,{status:{level:'healthy',mode:'backup'},staleQuotes:true});
  p.api.seed(...readyData());
  p.api.renderServiceNotice();
  assert.equal(p.notices.find(n=>n.id==='quotes').level,'warn');
  assert.equal(p.notices.find(n=>n.id==='quotes').text,'行情更新超时');
  assert.equal(p.notices.find(n=>n.id==='link').text,'');
});

test('offline link gets a neutral notice, never a primary-outage alarm',()=>{
  const p=page(()=>null,{status:{level:'offline',mode:'stale'}});
  p.api.seed(...readyData());
  p.api.renderServiceNotice();
  const link=p.notices.find(n=>n.id==='link');
  assert.equal(link.level,'info');
  assert.match(link.text,/最近缓存/);
  assert.doesNotMatch(link.text,/主数据库|故障/);
});

test('missing core data raises an error instead of silently showing advice',()=>{
  const p=page(()=>null,{status:{level:'healthy'}});
  p.api.renderServiceNotice();
  const errNotice=p.notices.find(n=>n.id==='page');
  assert.equal(errNotice.level,'error');
  assert.match(errNotice.text,/在线数据源暂不可用/);
});

test('recovery clears every registered notice',()=>{
  const opts={status:{level:'offline',mode:'stale'},staleQuotes:true};
  const p=page(()=>null,opts);
  p.api.seed(...readyData());
  p.api.renderServiceNotice();
  assert.ok(p.notices.some(n=>n.text),'异常时应有提示');
  p.notices.length=0;
  opts.status.level='healthy';opts.staleQuotes=false;
  p.api.renderServiceNotice();
  assert.deepEqual(p.notices.filter(n=>n.text),[],'恢复后所有提示必须被清除');
});

test('the obsolete primary-outage copy is gone from the page source',()=>{
  const src=readFileSync(new URL('./site_src/assets/page.js',import.meta.url),'utf8');
  assert.doesNotMatch(src,/主数据库暂不可用|自动切换到备用数据库/);
  assert.doesNotMatch(src,/showSourceNotice/);
  assert.doesNotMatch(src,/mode === "backup"/,'不得再把备库路由当成故障条件');
});
