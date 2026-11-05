import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';

function reader(reply,cloudReply) {
  const calls=[];
  const context=vm.createContext({window:{addEventListener(){}},location:{pathname:'/report.html'},
    localStorage:{getItem(){return null;}},fetch:async(url,options)=>{
      calls.push({url,options});
      if (url.startsWith('data/cloud.json')) return cloudReply ? cloudReply() : {ok:true,json:async()=>({url:'https://example.supabase.co',key:'public-test-key'})};
      return reply(url);
    }});
  vm.runInContext(readFileSync(new URL('./site_src/assets/app.js',import.meta.url),'utf8'),context);
  return {dsh:context.window.DSH,calls};
}

test('a transient static-file failure retries, while a successful frozen file stays cached',async()=>{
  let attempts=0;
  const {dsh}=reader(async()=>++attempts===1?{ok:false,status:503}:{ok:true,json:async()=>({points:[1]})});
  assert.equal(await dsh.json('curve',null),null);
  assert.equal((await dsh.json('curve',null)).points.length,1);
  await dsh.json('curve',null);assert.equal(attempts,2);
});

test('failed or malformed cloud configuration cannot poison future refreshes',async()=>{
  let attempts=0;
  const payload={ok:true,ts:'2026-11-04T21:00:00Z',quotes:{SPY:{p:100}}};
  const {dsh}=reader(async()=>({ok:true,json:async()=>[{payload}]}),async()=>{
    attempts++;
    if(attempts===1)return {ok:false,status:503};
    return {ok:true,json:async()=>attempts===2?{}:{url:'https://example.supabase.co',key:'public-test-key'}};
  });
  assert.equal(await dsh.json('quotes',null),null);
  assert.equal(await dsh.json('quotes',null),null);
  assert.equal((await dsh.json('quotes',null)).ts,payload.ts);
  assert.equal(attempts,3);
});

test('authorization failure reloads published configuration on the next attempt',async()=>{
  let reads=0,configs=0;
  const payload={ok:true,ts:'2026-11-04T21:00:00Z',quotes:{SPY:{p:100}}};
  const {dsh,calls}=reader(async()=>++reads===1?{ok:false,status:401}:{ok:true,json:async()=>[{payload}]},
    async()=>({ok:true,json:async()=>({url:'https://example.supabase.co',key:'public-'+(++configs)})}));
  assert.equal(await dsh.json('quotes',null),null);
  assert.equal((await dsh.json('quotes',null)).ts,payload.ts);
  assert.equal(configs,2);
  assert.equal(calls.at(-1).options.headers.apikey,'public-2');
});

test('preview requires the same quote version and session, proof and three selections',()=>{
  const {dsh}=reader(async()=>({ok:false}));
  const quotes={version:'current',session:'2026-11-04',ts:new Date(Date.now()-1000).toISOString(),
    fresh_until:new Date(Date.now()+60000).toISOString()};
  const preview={quote_version:'current',session:quotes.session,reference_version:'reviewed',top:[{},{},{}]};
  assert.equal(dsh.previewCurrent(preview,quotes),true);
  for (const patch of [{quote_version:undefined},{quote_version:'old'},{session:'2026-11-02'},
      {reference_version:null},{top:[]}]) assert.equal(dsh.previewCurrent({...preview,...patch},quotes),false);
  assert.equal(dsh.previewCurrent(preview,{...quotes,fresh_until:new Date(Date.now()-100).toISOString()}),false);
  assert.equal(dsh.previewCurrent(null,quotes),false);
});

test('a timestamped prior preview remains usable only inside its own reviewed validity window',()=>{
  const {dsh}=reader(async()=>({ok:false}));const now=Date.now();
  const q={version:'new',session:'2026-11-04',ts:new Date(now).toISOString(),fresh_until:new Date(now+1200000).toISOString()};
  const pv={quote_version:'old',session:q.session,reference_version:'ref',top:[{},{},{}],
    as_of_ts:new Date(now-300000).toISOString(),fresh_until:new Date(now+900000).toISOString()};
  assert.equal(dsh.previewCurrent(pv,q),true);
  assert.equal(dsh.previewCurrent({...pv,fresh_until:new Date(now-1).toISOString()},q),false);
});

test('market advice uses nine verified month ends and current SPY, never a frozen trend',()=>{
  const {dsh}=reader(async()=>({ok:false}));const now=Date.now();
  const q={session:'2026-11-04',calendar_sha256:'calendar',ts:new Date(now).toISOString(),
    fresh_until:new Date(now+1200000).toISOString(),quotes:{SPY:{p:100}}};
  const dates=['2026-02-27','2026-03-31','2026-04-30','2026-05-29','2026-06-30','2026-07-31','2026-08-31','2026-09-30','2026-10-30'];
  const e={market_advice:{month:'2026-11',calendar_sha256:'calendar',window:10,closes:dates.map(date=>({date,price:100}))}};
  assert.equal(dsh.marketAdvice(e,q).trend,'满仓');q.quotes.SPY.p=90;
  assert.equal(dsh.marketAdvice(e,q).trend,'半仓');assert.equal(dsh.marketAdvice(e,q).ma,99);
  assert.equal(dsh.marketAdvice(e,{...q,session:'2026-12-01'}),null);
});

test('daily failure explains a stalled equity anchor separately from quote failure',()=>{
  const {dsh}=reader(async()=>({ok:false}));
  const quotes={session:'2026-11-04',previous_session:'2026-11-03',quotes:{},
    ts:new Date(Date.now()-1000).toISOString(),fresh_until:new Date(Date.now()+60000).toISOString()};
  assert.equal(dsh.todayUnavailable({},{}),'行情读取失败');
  assert.equal(dsh.todayUnavailable(quotes,{last_session:'20261102'}),'收益基准待更新');
  assert.equal(dsh.todayUnavailable(quotes,{last_session:'20261103'}),'持仓报价或价格口径待核验');
});

test('freshness follows the reviewed deadline across weekends and recovers after refresh',()=>{
  const {dsh}=reader(async()=>({ok:false}));
  const friday={ts:'2026-11-06T21:00:00Z',fresh_until:'2026-11-09T14:50:00Z'};
  assert.equal(dsh.quoteFreshness(friday,Date.parse('2026-11-08T21:00:00Z')),false);
  assert.equal(dsh.quoteFreshness(friday,Date.parse('2026-11-09T14:50:00Z')),true);
  const refreshed={ts:'2026-11-09T14:45:00Z',fresh_until:'2026-11-09T15:05:00Z'};
  assert.equal(dsh.quoteFreshness(refreshed,Date.parse('2026-11-09T14:50:00Z')),false);
  assert.equal(dsh.quoteFreshness({ts:friday.ts},Date.now()),null);
  assert.equal(dsh.quoteFreshness({...friday,fresh_until:'invalid'},Date.now()),null);
  assert.equal(dsh.quoteFreshness({...friday,fresh_until:friday.ts},Date.now()),null);
});

test('quotes read the online database instead of a deployment snapshot',async()=>{
  const payload={ok:true,ts:'2026-11-03T21:00:00Z',session:'2026-11-03',quotes:{SPY:{p:100}}};
  const {dsh,calls}=reader(async()=>({ok:true,json:async()=>[{payload}]}));
  const result=await dsh.json('quotes',{});
  assert.equal(result.ts,payload.ts);
  assert.ok(calls[1].url.includes('/rest/v1/v_live_quote_display?'));
  assert.equal(calls[1].options.headers.apikey,'public-test-key');
  assert.equal(calls.some(c=>c.url.includes('data/quotes.json')),false);
});

test('both strategy variants read live equity, ranking and official anchors online',async()=>{
  for (const prefix of ['', 'b/']) {
    const {dsh,calls}=reader(async(url)=>({ok:true,json:async()=>[
      {payload:url.includes('live_strategy_anchor')?{signal:{official:[{ticker:'X'}]}}:{quote_version:'current'}}]}));
    for (const name of ['equity','preview','signal']) assert.ok(await dsh.json(prefix+name,null));
    assert.ok(calls.some(c=>c.url.includes('variant=eq.'+(prefix?'B':'A'))));
    assert.equal(calls.some(c=>/data\/(b\/)?(equity|preview|signal)\.json/.test(c.url)),false);
  }
});

test('database failure and malformed snapshots never fall back to old quote files',async()=>{
  for (const reply of [async()=>({ok:false,status:503}),async()=>({ok:true,json:async()=>[]}),
      async()=>({ok:true,json:async()=>[{payload:{ok:true,quotes:{}}}]}),async()=>{throw Error('offline');}]) {
    const {dsh,calls}=reader(reply);
    const result=await dsh.json('quotes',null);
    assert.equal(result,null);
    assert.equal(calls.some(c=>c.url.includes('data/quotes.json')),false);
  }
});
