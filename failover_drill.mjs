/* failover_drill.mjs — 真实故障切换演练（HANDOFF 第 7 节 A 层验收）
 *
 * 与 test_online_quotes.mjs 的区别：那边全链路 mock，用于 CI 逻辑回归；
 * 这里只把「主库」模拟成 402（Supabase egress 超额限服，真实发生过的故障），
 * 备库则打真实 Turso HTTPS 端点、读真实数据，验证端到端确实能切过去。
 *
 * 用法：node failover_drill.mjs              # 用仓库里的 site_src/cloud.json
 *       node failover_drill.mjs --online      # 用线上 Pages 的 data/cloud.json
 * 依赖：公开只读 token（无需任何私密凭据），需要能访问公网。
 *       发布后用 --online 复验线上配置是否真的带上了备库端点。
 *
 * 断言覆盖：备库返回真实行情 / 自动切换 / 主库独立熔断 /
 *           端点记忆 / 无 snapshot.json 本地文件兜底。
 */
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const BASE = path.dirname(fileURLToPath(import.meta.url));
const ONLINE_URL = 'https://dear202606-star.github.io/trade/data/cloud.json';
const useOnline = process.argv.includes('--online');
const cfg = useOnline
  ? await (await realConfig()).json()
  : JSON.parse(readFileSync(path.join(BASE, 'site_src', 'cloud.json'), 'utf8'));

async function realConfig() {
  const r = await fetch(ONLINE_URL + '?drill=' + Date.now(), { cache: 'no-store' });
  if (!r.ok) throw new Error('线上 cloud.json HTTP ' + r.status);
  return r;
}

const eps = cfg.endpoints || [];
const primary = eps.find((e) => e.type === 'supabase');
const turso = eps.find((e) => e.type === 'turso');
assert.ok(primary && primary.url.startsWith('https://'), '缺少 Supabase 主端点');
assert.ok(turso && turso.url.startsWith('https://') && turso.key,
  '缺少真实 Turso 只读端点');

const calls = [];
const store = new Map();
const realFetch = globalThis.fetch;
const context = vm.createContext({
  window: { addEventListener() {} },
  location: { pathname: '/report.html' },
  localStorage: {
    getItem(k) { return store.has(k) ? store.get(k) : null; },
    setItem(k, v) { store.set(k, String(v)); },
    removeItem(k) { store.delete(k); }
  },
  console, setTimeout, clearTimeout, setInterval, clearInterval,
  fetch: async (url, options) => {
    calls.push({ url: String(url), options });
    if (String(url).startsWith('data/cloud.json')) {
      return { ok: true, status: 200, json: async () => cfg };
    }
    if (String(url).startsWith(primary.url)) {
      // 主库 402：egress 超额时 PostgREST 被限服，正是需要自动切换的场景
      return { ok: false, status: 402 };
    }
    return realFetch(url, options);   // 备库走真实 HTTPS 网络
  }
});
vm.runInContext(readFileSync(path.join(BASE, 'site_src', 'assets', 'app.js'), 'utf8'), context);
const dsh = context.window.DSH;

const primaryCalls = () => calls.filter((c) => c.url.startsWith(primary.url)).length;
const backupCalls = () => calls.filter((c) => c.url.includes('/v2/pipeline')).length;

const quotes = await dsh.json('quotes', null);
assert.ok(quotes && typeof quotes === 'object', '备库未返回 quotes');
assert.ok(quotes.quotes && quotes.quotes.SPY, '备库 quotes 缺少真实 SPY 行情');
// 行情快照在非交易时段会产出 display_only 精简版（仅展示标的、count 仍是全量覆盖数），
// 因此不能把标的数写死；按数据契约校验可用性、覆盖数与场次即可。
assert.ok(Object.keys(quotes.quotes).length >= 1, '备库行情标的为空');
assert.equal(quotes.ok, true, '备库 quotes 标记为不可用');
assert.ok(quotes.count > 0, '备库 quotes 覆盖数为 0');
assert.ok(quotes.ts && quotes.session, '备库 quotes 缺少时间戳或场次');
assert.equal(dsh.dataStatus().mode, 'backup', '未切换到备库模式');
assert.equal(dsh.dataStatus().activeEndpoint, 'turso');
assert.equal(primaryCalls(), 1, '主库应只请求一次后即被独立熔断');
assert.equal(backupCalls(), 1, '备库应命中一次');
assert.equal(calls.some((c) => c.url.includes('snapshot.json')), false,
  '不得读取任何本地文件镜像');
assert.equal(dsh.snapshot, undefined, '文件镜像接口必须彻底退场');

// 熔断期内读另一个 key：主库不再被打，直接由备库服务
const equity = await dsh.json('equity', null);
assert.ok(equity && typeof equity === 'object' && Object.keys(equity).length > 0,
  '备库未返回 equity');
assert.equal(primaryCalls(), 1, '熔断期内不得再打主库');
assert.equal(backupCalls(), 2, '第二个 key 应由备库服务');
assert.equal(dsh.dataStatus().mode, 'backup');

const saved = store.get('dsh.endpoint.v1.');
assert.ok(saved && saved.startsWith('turso|'), '未把最优端点写入 localStorage');

console.log('FAILOVER_DRILL_OK ' + JSON.stringify({
  configSource: useOnline ? ONLINE_URL : 'site_src/cloud.json',
  mode: dsh.dataStatus().mode,
  activeEndpoint: dsh.dataStatus().activeEndpoint,
  primaryCalls: primaryCalls(),
  backupCalls: backupCalls(),
  quotesDisplayEntries: Object.keys(quotes.quotes).length,
  quotesCoverageCount: quotes.count,
  quotesTs: quotes.ts,
  quotesSession: quotes.session,
  errors: 0,
  fileMirrorRequests: calls.filter((c) => c.url.includes('snapshot.json')).length
}));
