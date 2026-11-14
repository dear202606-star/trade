import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';

function reader(reply,cloudReply,opts={}) {
  const calls=[];
  const store=opts.store || {getItem(){return null;},setItem(){},removeItem(){}};
  const sandbox={window:{addEventListener(){}},
    location:{pathname:opts.pathname || '/report.html'},
    localStorage:store,console,
    fetch:async(url,options)=>{
      calls.push({url,options});
      if (url.startsWith('data/cloud.json')) return cloudReply ? cloudReply() : {ok:true,json:async()=>({url:'https://example.supabase.co',key:'public-test-key'})};
      return reply(url, options);
    }};
  if (opts.document) sandbox.document=opts.document;
  const context=vm.createContext(sandbox);
  vm.runInContext(readFileSync(new URL('./site_src/assets/app.js',import.meta.url),'utf8'),context);
  return {dsh:context.window.DSH,calls,store};
}

test('static datasets read the online database in normal mode, never the mirror',async()=>{
  // 2026-11-07 二次审查：在线库是唯一常规数据源。镜像只在故障时兜底，
  // 不参与常态读取，避免「文件 + 数据库」双套体系的一致性风险。
  let hits=0;
  const {dsh,calls}=reader(async(url)=>{
    if (url.includes('v_site_dataset?key=eq.curve')) {
      hits++;return {ok:true,json:async()=>[{payload:{points:[1,2,3]}}]};
    }
    throw Error('unexpected call: '+url);
  });
  assert.deepEqual(await dsh.json('curve',null),{points:[1,2,3]});
  assert.equal(calls.some(c=>c.url==='data/snapshot.json'),false);
  // 24h TTL 内重复读取命中内存缓存，不产生任何新请求
  const n=calls.length;
  assert.deepEqual(await dsh.json('curve',null),{points:[1,2,3]});
  assert.equal(calls.length,n);
  assert.equal(hits,1);
  assert.equal(dsh.snapshot,undefined,'文件镜像接口必须彻底退场');
});

test('a 402 on the primary endpoint fails over to the Turso backup, no file mirror',async()=>{
  const equity={position:[{ticker:'X'}],stats:{strategy:0.4355}};
  const quotes={ok:true,ts:'2026-11-08T00:00:00Z',session:'2026-11-07',quotes:{SPY:{p:100}}};
  const payloads={equity,quotes};
  const cfg={url:'https://example.supabase.co',key:'public-test-key',endpoints:[
    {type:'supabase',url:'https://example.supabase.co',key:'public-test-key'},
    {type:'turso',url:'https://backup.example',key:'readonly-token'}]};
  let primaryHits=0,backupHits=0;
  const {dsh,calls}=reader(async(url,options)=>{
    if (url.startsWith('https://example.supabase.co/rest/v1/')) {primaryHits++;return {ok:false,status:402};}
    if (url==='https://backup.example/v2/pipeline') {
      backupHits++;
      const key=JSON.parse(options.body).requests[0].stmt.args[0].value;
      return {ok:true,json:async()=>({results:[
        {type:'ok',response:{type:'execute',result:{rows:[[{type:'text',value:JSON.stringify(payloads[key])}]]}}},
        {type:'ok',response:{type:'close'}}]})};
    }
    throw Error('unexpected call: '+url);
  },async()=>({ok:true,json:async()=>cfg}));
  const got=await dsh.json('equity',null);
  // payload 经 vm 沙箱内 JSON.parse 生成，原型与宿主对象不同，逐字段比对
  assert.equal(got.position[0].ticker,'X');
  assert.equal(got.stats.strategy,0.4355);
  assert.equal(primaryHits,1);
  assert.equal(backupHits,1);
  assert.equal(dsh.dataStatus().mode,'backup');
  assert.equal(dsh.dataStatus().activeEndpoint,'turso');
  // 2026-11-08：主库 402（免费额度限服）→ Turso 正常供数是计划内路由，
  // 绝不能被判定为故障，更不能向访客弹「主数据库暂不可用」。
  assert.equal(dsh.dataStatus().level,'healthy');
  assert.equal(Object.keys(dsh.notices()).length,0,'备库健康时不得登记任何访客提示');
  assert.equal(calls.some(c=>c.url.includes('snapshot.json')),false,'不得读取任何本地镜像文件');
  // 主库被独立熔断 10 分钟：另一个 key 直接走备库，主库请求数不再增加
  assert.equal((await dsh.json('quotes',null)).ts,quotes.ts);
  assert.equal(primaryHits,1);
  assert.equal(backupHits,2);
  const primary=dsh.dataStatus().endpoints.find(e=>e.id.startsWith('supabase|'));
  assert.equal(primary.circuitOpen,true);
});

test('refresh cadence is tiered instead of a single 60-second full reload',()=>{
  const {dsh}=reader(async()=>({ok:false}));
  assert.equal(dsh.ttlOf('curve'),24*3600*1000);
  assert.equal(dsh.tierOf('curve'),0);
  assert.equal(dsh.ttlOf('equity'),15*60*1000);
  assert.equal(dsh.tierOf('equity'),1);
  assert.equal(dsh.tierOf('quotes'),2);
  assert.equal(dsh.ttlOf('quotes'),15*60*1000);
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

test('intraday banner follows the 30-minute collection cadence, closing banner stays strict',()=>{
  const {dsh}=reader(async()=>({ok:false}));
  const intraday={ts:'2026-11-10T14:33:00Z',fresh_until:'2026-11-10T14:53:00Z',intraday:true};
  // 数据源 20 分钟时效刚过，但下一轮 30 分钟采集还在路上：业务口径已判过期，
  // 展示层不应弹「行情更新超时」，否则每次采集后都会短暂误报。
  assert.equal(dsh.quoteFreshness(intraday,Date.parse('2026-11-10T14:58:00Z')),true);
  assert.equal(dsh.quoteStaleForDisplay(intraday,Date.parse('2026-11-10T14:58:00Z')),false);
  // 宽限 45 分钟后仍无新快照：真停更，必须告警。
  assert.equal(dsh.quoteStaleForDisplay(intraday,Date.parse('2026-11-10T15:40:00Z')),true);
  // 收盘版不享受宽限，fresh_until 一到即报；周末/夜间由日历口径兜住。
  const closing={ts:'2026-11-10T21:17:00Z',fresh_until:'2026-11-11T14:50:00Z',intraday:false};
  assert.equal(dsh.quoteStaleForDisplay(closing,Date.parse('2026-11-11T14:49:00Z')),false);
  assert.equal(dsh.quoteStaleForDisplay(closing,Date.parse('2026-11-11T14:51:00Z')),true);
  // 无法核验时效时不误报
  assert.equal(dsh.quoteStaleForDisplay({ts:intraday.ts},Date.now()),false);
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

test('version probe expires only the layer whose data actually changed',async()=>{
  const state={staticTs:'2026-11-07T00:00:00Z',quoteVer:'aaa',resultTs:'2026-11-06T21:00:00Z'};
  const {dsh,calls}=reader(async(url)=>{
    if (url.includes('v_site_dataset?select=key,updated_at'))
      return {ok:true,json:async()=>[{key:'curve',updated_at:state.staticTs}]};
    if (url.includes('live_quote_snapshot?singleton'))
      return {ok:true,json:async()=>[{evidence_sha256:state.quoteVer}]};
    if (url.includes('v_live_strategy_results?select=variant'))
      return {ok:true,json:async()=>[{variant:'A',kind:'equity',quote_version:"fixed",created_at:state.resultTs}]};
    if (url.includes('v_site_dataset?key=eq.curve'))
      return {ok:true,json:async()=>[{payload:{points:[1,2,3]}}]};
    if (url.includes('/rest/v1/'))
      return {ok:true,json:async()=>[{payload:{ok:true,quotes:{SPY:{p:1}}}}]};
    throw Error('unexpected call: '+url);
  });
  // 首次探测只建立基线；版本未变则完全不拉 payload
  assert.equal((await dsh.syncIfChanged()).length,0);
  const baseline=calls.length;
  assert.equal((await dsh.syncIfChanged()).length,0);
  assert.equal(calls.length,baseline+3);   // 每次仅 3 个极小探测请求
  // 填充缓存后，只改静态数据集时间戳
  await dsh.json('curve',null);
  await dsh.json('quotes',{});
  await dsh.json('equity',null);
  state.staticTs='2026-11-07T01:00:00Z';
  const expired=await dsh.syncIfChanged();
  assert.ok(expired.includes('curve'),'静态层应失效');
  assert.ok(!expired.includes('quotes'),'行情版本未变，不应被连带失效');
  assert.ok(!expired.includes('equity'),'计算结果未变，不应被连带失效');
  state.resultTs='2026-11-07T01:05:00Z';
  assert.deepEqual(Array.from(await dsh.syncIfChanged()), ['equity','b/equity','signal','b/signal','preview','b/preview']);
});

test('all endpoints down falls back to the caller value, never to a file mirror',async()=>{
  const cfg={url:'https://example.supabase.co',key:'public-test-key',endpoints:[
    {type:'supabase',url:'https://example.supabase.co',key:'public-test-key'},
    {type:'turso',url:'https://backup.example',key:'readonly-token'}]};
  const {dsh,calls}=reader(async()=>({ok:false,status:503}),async()=>({ok:true,json:async()=>cfg}));
  const fallback={position:[{ticker:'cached'}]};
  assert.deepEqual(await dsh.json('equity',fallback),fallback);
  assert.equal(dsh.dataStatus().mode,'stale');
  assert.equal(dsh.dataStatus().level,'offline','全部端点不可用时才是访客可见的异常');
  assert.equal(dsh.dataStatus().circuitOpen,true);
  assert.equal(calls.some(c=>c.url.includes('snapshot.json')),false);
  const n=calls.length;
  assert.deepEqual(await dsh.json('equity',fallback),fallback);
  assert.equal(calls.length,n,'全端点熔断期内不再产生在线请求');
});

test('backup endpoint version probe stays lightweight and expires only the changed layer',async()=>{
  const cfg={url:'https://example.supabase.co',key:'public-test-key',
    endpoints:[{type:'turso',url:'https://backup.example',key:'readonly-token'}]};
  const state={staticTs:'2026-11-07T00:00:00Z',quoteTs:'2026-11-07T21:00:00Z',
    resultTs:'2026-11-07T21:30:00Z'};
  const payloads={curve:{points:[1,2,3]},
    quotes:{ok:true,ts:'2026-11-07T21:00:00Z',session:'2026-11-07',quotes:{SPY:{p:100}}}};
  const {dsh,calls}=reader(async(url,options)=>{
    if (url!=='https://backup.example/v2/pipeline') throw Error('unexpected call: '+url);
    const req=JSON.parse(options.body).requests[0];
    const rows=[];
    if (req.stmt.sql.includes('select key, coalesce')) {
      const map={curve:state.staticTs,'b/curve':state.staticTs,
        strategy_history:state.staticTs,'b/strategy_history':state.staticTs,
        name_zh:state.staticTs,sectors:state.staticTs,health:state.staticTs,
        quotes:state.quoteTs,equity:state.resultTs,'b/equity':state.resultTs,
        signal:state.resultTs,'b/signal':state.resultTs,
        preview:state.resultTs,'b/preview':state.resultTs};
      for (const k of Object.keys(map))
        rows.push([{type:'text',value:k},{type:'text',value:map[k]}]);
    } else {
      const key=req.stmt.args[0].value;
      rows.push([{type:'text',value:JSON.stringify(payloads[key])}]);
    }
    return {ok:true,json:async()=>({results:[
      {type:'ok',response:{type:'execute',result:{rows}}},
      {type:'ok',response:{type:'close'}}]})};
  },async()=>({ok:true,json:async()=>cfg}));
  assert.equal((await dsh.syncIfChanged()).length,0);
  const baseline=calls.length;
  assert.equal((await dsh.syncIfChanged()).length,0);
  assert.equal(calls.length,baseline+1,'备库版本探测每次只需 1 个请求');
  await dsh.json('curve',null);
  await dsh.json('quotes',{});
  state.staticTs='2026-11-08T00:00:00Z';
  const expired=await dsh.syncIfChanged();
  assert.ok(expired.includes('curve'),'静态层应失效');
  assert.ok(!expired.includes('quotes'),'行情时间戳未变，不应被连带失效');
});

/* ---------- 2026-11-08：访客提示 = 数据健康度，而不是「用了哪个端点」 ---------- */
function noticeDom(){
  const nodes={};
  const body={firstChild:null,
    insertBefore(node){nodes[node.id]=node;body.firstChild=node;},
    removeChild(node){delete nodes[node.id];body.firstChild=null;}};
  const document={body,
    createElement(){return {id:'',className:'',textContent:'',style:{},parentNode:body,
      remove(){body.removeChild(this);}};},
    getElementById(id){return nodes[id]||null;}};
  return {document,nodes};
}

test('primary 402 circuit survives a reload so the free egress quota is not wasted',async()=>{
  const saved={};
  const store={getItem:k=>saved[k]||null,setItem:(k,v)=>{saved[k]=String(v);},
               removeItem:k=>{delete saved[k];}};
  const cfg={url:'https://example.supabase.co',key:'public-test-key',endpoints:[
    {type:'supabase',url:'https://example.supabase.co',key:'public-test-key'},
    {type:'turso',url:'https://backup.example',key:'readonly-token'}]};
  let primaryHits=0;
  const handler=async(url,options)=>{
    if (url.startsWith('https://example.supabase.co/rest/v1/')) {
      primaryHits++;return {ok:false,status:402};
    }
    if (url==='https://backup.example/v2/pipeline') {
      const key=JSON.parse(options.body).requests[0].stmt.args[0].value;
      return {ok:true,json:async()=>({results:[
        {type:'ok',response:{type:'execute',result:{rows:[[{type:'text',value:JSON.stringify({ok:true,key}) }]]}}},
        {type:'ok',response:{type:'close'}}]})};
    }
    throw Error('unexpected call: '+url);
  };
  const first=reader(handler,async()=>({ok:true,json:async()=>cfg}),{store});
  await first.dsh.json('equity',null);
  assert.equal(primaryHits,1);
  const circuitKey=Object.keys(saved).find(k=>k.startsWith('dsh.circuit.v1.')&&k.includes('supabase|'));
  assert.ok(circuitKey,'402 熔断必须跨刷新持久化');
  assert.ok(Number(saved[circuitKey])>Date.now(),'熔断必须带过期时间，不能永久锁死主库');
  delete saved['dsh.endpoint.v1.'];   // 模拟不记得上次成功端点的最坏情况
  const second=reader(handler,async()=>({ok:true,json:async()=>cfg}),{store});
  await second.dsh.json('equity',null);
  assert.equal(primaryHits,1,'刷新后不得再撞一次 402');
  assert.equal(second.dsh.dataStatus().level,'healthy');
});

test('notice center renders one neutral bar and removes it after recovery',()=>{
  const {document,nodes}=noticeDom();
  const {dsh}=reader(async()=>({ok:false,status:503}),null,{document});
  dsh.reportNotice('link','info','最近缓存');
  assert.equal(nodes.serviceBanner.className,'data-notice','非故障告知不用红色告警色');
  assert.equal(nodes.serviceBanner.textContent,'最近缓存');
  dsh.reportNotice('quotes','warn','行情更新超时');
  assert.equal(nodes.serviceBanner.className,'data-alert','真实异常优先级更高');
  assert.equal(nodes.serviceBanner.textContent,'⚠ 行情更新超时');
  assert.equal(Object.keys(nodes).length,1,'所有来源共用一个横幅，不叠加');
  dsh.clearNotice('quotes');
  assert.equal(nodes.serviceBanner.className,'data-notice');
  dsh.clearNotice('link');
  assert.equal(nodes.serviceBanner,undefined,'恢复后横幅必须自动移除');
});

test('genuine data-health issues still surface a warning instead of being silenced',async()=>{
  const {document,nodes}=noticeDom();
  const cfg={url:'https://example.supabase.co',key:'public-test-key',endpoints:[
    {type:'supabase',url:'https://example.supabase.co',key:'public-test-key'}]};
  reader(async(url)=>{
    if (url.includes('v_site_dataset?key=eq.health'))
      return {ok:true,json:async()=>[{payload:{status:'degraded',
        issues:['行情 30 小时未更新'],quotes_ts:'2026-11-06T21:00:00Z'}}]};
    throw Error('unexpected call: '+url);
  },async()=>({ok:true,json:async()=>cfg}),{document,pathname:'/index.html'});
  await new Promise(r=>setTimeout(r,10));
  assert.ok(nodes.serviceBanner,'真实数据异常必须保留可见告警');
  assert.match(nodes.serviceBanner.textContent,/数据异常/);
});

test('a missing auxiliary health key never marks the whole page offline',async()=>{
  const {dsh}=reader(async()=>({ok:false,status:503}),null,{pathname:'/index.html'});
  await new Promise(r=>setTimeout(r,10));
  assert.equal(dsh.dataStatus().level,'healthy');
  assert.equal(Object.keys(dsh.notices()).length,0);
});

/* ---------- 2026-11-08：前端刷新节奏共用已复核 NYSE 日历 ---------- */
test('published calendar stops polling on NYSE holidays but keeps federal-only trading days',async()=>{
  const cal={market:'XNYS',timezone:'America/New_York',
    coverage_from:'2025-12-29',coverage_to:'2028-12-31',
    regular_open:'09:30',regular_close:'16:00',
    closed:['2026-11-26','2026-04-03','2026-12-25'],
    early_close:{'2026-11-27':'13:00'}};
  const {dsh}=reader(async()=>({ok:false,status:503}),
    async()=>({ok:true,json:async()=>({url:'https://example.supabase.co',
      key:'public-test-key',calendar:cal})}));
  await dsh.json('cloud',null);
  assert.equal(dsh.isTradingHours(new Date('2026-11-26T16:00:00Z')),false,'感恩节休市');
  assert.equal(dsh.isTradingHours(new Date('2026-04-03T15:00:00Z')),false,'Good Friday 休市');
  assert.equal(dsh.isTradingHours(new Date('2026-10-12T15:00:00Z')),true,'Columbus Day 照常开市');
  assert.equal(dsh.isTradingHours(new Date('2026-11-11T15:00:00Z')),true,'Veterans Day 照常开市');
  assert.equal(dsh.isTradingHours(new Date('2026-11-27T17:00:00Z')),true,'半日盘中');
  assert.equal(dsh.isTradingHours(new Date('2026-11-27T18:30:00Z')),false,'半日已收盘');
  assert.equal(dsh.isTradingHours(new Date('2026-11-10T02:00:00Z')),false,'北京 10:00');
  const beyond=new Date('2029-01-02T15:00:00Z');
  const legacy=beyond.getUTCDay()>=1&&beyond.getUTCDay()<=5&&
               beyond.getUTCHours()>=13&&beyond.getUTCHours()<22;
  assert.equal(dsh.isTradingHours(beyond),legacy,'超出覆盖范围应 fail-open 回退旧逻辑');
});

test('missing published calendar keeps the legacy UTC window instead of freezing the page',async()=>{
  const {dsh}=reader(async()=>({ok:false,status:503}));
  await dsh.json('cloud',null);
  assert.equal(dsh.isTradingHours(new Date('2026-11-09T15:00:00Z')),true);
  assert.equal(dsh.isTradingHours(new Date('2026-11-08T15:00:00Z')),false,'周日');
});

test('closing refresh and overdue warnings follow the exchange calendar',()=>{
  const {dsh}=reader(async()=>({ok:false}));
  const at=stamp=>new Date(stamp);
  dsh.setExchangeCalendar({coverage_from:'2026-01-01',coverage_to:'2026-12-31',
    closed:['2026-11-26'],early_close:{'2026-11-27':'13:00'},regular_close:'16:00'});
  assert.equal(dsh.inClosingRefreshWindow(at('2026-11-27T18:44:00Z')),false);
  assert.equal(dsh.inClosingRefreshWindow(at('2026-11-27T18:45:00Z')),true);
  assert.equal(dsh.inClosingRefreshWindow(at('2026-11-26T22:00:00Z')),false);
  const q={intraday:false,session:'2026-11-25'};
  assert.equal(dsh.closingQuoteOverdue(q,at('2026-11-27T19:29:00Z')),false);
  assert.equal(dsh.closingQuoteOverdue(q,at('2026-11-27T19:30:00Z')),true);
});
