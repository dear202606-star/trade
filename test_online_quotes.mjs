import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';

function reader(reply) {
  const calls=[];
  const context=vm.createContext({window:{addEventListener(){}},location:{pathname:'/report.html'},
    localStorage:{getItem(){return null;}},fetch:async(url,options)=>{
      calls.push({url,options});
      if (url.startsWith('data/cloud.json')) return {ok:true,json:async()=>({url:'https://example.supabase.co',key:'public-test-key'})};
      return reply(url);
    }});
  vm.runInContext(readFileSync(new URL('./site_src/assets/app.js',import.meta.url),'utf8'),context);
  return {dsh:context.window.DSH,calls};
}

test('quotes read the online database instead of a deployment snapshot',async()=>{
  const payload={ok:true,ts:'2026-11-03T21:00:00Z',session:'2026-11-03',quotes:{SPY:{p:100}}};
  const {dsh,calls}=reader(async()=>({ok:true,json:async()=>[{payload}]}));
  const result=await dsh.json('quotes',{});
  assert.equal(result.ts,payload.ts);
  assert.ok(calls[1].url.includes('/rest/v1/live_quote_snapshot?'));
  assert.equal(calls[1].options.headers.apikey,'public-test-key');
  assert.equal(calls.some(c=>c.url.includes('data/quotes.json')),false);
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
