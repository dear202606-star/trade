import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';

function page(read) {
  const calls=[], paints=[], elements=new Map();
  const document={readyState:'loading',hidden:false,addEventListener(){},querySelectorAll(){return [];},
    getElementById(id){
      if (!elements.has(id)) elements.set(id,{textContent:'',className:'',innerHTML:'',style:{}});
      return elements.get(id);
    }};
  let tick;
  const context=vm.createContext({document,window:{},localStorage:{getItem(){return null;},setItem(){}},
    DSH:{jsonNoCache:async(name,fallback)=>{calls.push(name);return read(name,fallback);},
      json:async(name,fallback)=>read(name,fallback),previewCurrent:()=>false},
    setInterval(fn){tick=fn;},console});
  const source=readFileSync(new URL('./site_src/assets/page.js',import.meta.url),'utf8');
  vm.runInContext(source.replace('  if (document.readyState === "loading")',`
    window.testPage = {startAutoRefresh, loadVariant, renderYear,
      state:()=>({EQ,PV,Q}), switchTo:v=>{VARIANT=v;}};
    renderAllCards=()=>window.paint({EQ,PV,Q});
    checkFreshness=()=>window.checked++;
    renderQuality=()=>{};
    if (document.readyState === "loading")`),context);
  context.window.paint=s=>paints.push(s);
  context.window.checked=0;
  return {api:context.window.testPage,context,calls,paints,elements,tick:()=>tick()};
}

test('unchanged quotes still refresh dependent artifacts and recheck deadlines',async()=>{
  let revision=0;
  const p=page(name=>name==='quotes'?{ts:'same',version:'same'}:
    name.endsWith('equity')?{position:[{}],stats:{strategy:revision},revision}:
    name.endsWith('signal')?{official:[{}]}:{revision});
  p.api.startAutoRefresh();
  await p.tick();revision=1;await p.tick();
  assert.equal(p.paints.length,2);
  assert.equal(p.paints[1].EQ.revision,1);
  assert.equal(p.paints[1].PV.revision,1);
  assert.equal(p.context.window.checked,2);
  for(const name of ['b/equity','b/signal','b/preview','quotes'])
    assert.equal(p.calls.filter(n=>n===name).length,2);
  assert.equal(p.calls.some(n=>['b/curve','b/strategy_history','name_zh'].includes(n)),false);
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
  const p=page(()=>null);p.api.startAutoRefresh();
  const positions=p.context.document.getElementById('posRows');positions.innerHTML='old variant holdings';
  const daily=p.context.document.getElementById('todayPct');daily.textContent='+9%';
  await p.tick();
  assert.equal(positions.innerHTML,'');assert.equal(daily.textContent,'—');
  assert.equal(p.context.document.getElementById('verdict').textContent,'数据未就绪');
});
