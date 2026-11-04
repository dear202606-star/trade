import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';

function calculator(amount = '100000', method = 'today') {
  const context = vm.createContext({
    window: { addEventListener() {} }, location: { pathname: '/report.html' },
    localStorage: { getItem: () => amount },
  });
  vm.runInContext(readFileSync(new URL('./site_src/assets/app.js', import.meta.url), 'utf8'), context);
  return context.window.DSH[method];
}

const equity = { exposure: 1, last_session: '20261103', position: [
  { ticker: 'A', weight: 0.6, entry: 100, last: 999, last_raw: 999, last_date: '20261103' },
  { ticker: 'B', weight: 0.4, entry: 200, last: 999, last_raw: 999, last_date: '20261103' },
] };
const quotes = { session: '2026-11-03', quotes: { A: { p: 110, pc: 100 }, B: { p: 190, pc: 200 } } };

test('all holdings contribute shares times actual price change', () => {
  const result = calculator()(null, quotes, equity);
  assert.equal(result.amount, 4000);
  assert.equal(result.pct, 0.04);
});

test('exposure advice never scales strategy shares, amount or daily return', () => {
  const today = calculator();
  const expected = today(null, quotes, equity);
  for (const exposure of [0.5, 0, undefined]) {
    const result = today(null, quotes, { ...equity, exposure });
    assert.equal(result.amount, expected.amount);
    assert.equal(result.pct, expected.pct);
  }
});

test('missing price or previous close cannot produce a partial portfolio return', () => {
  for (const bad of [undefined, { p: 190 }, { p: 190, pc: 0 },
    { p: NaN, pc: 200 }, { p: 190, pc: Infinity }, { p: '190', pc: 200 }]) {
    const input = { ...quotes, quotes: { ...quotes.quotes, B: bad } };
    assert.equal(calculator()(null, input, equity), null);
  }
});

const adjusted = { last_session: '20261102', stats: { strategy: .06, benchmark: .02 }, position: [
  { ticker:'A',weight:.6,entry:75,last:82.5,last_raw:110,last_date:'20261102' },
  { ticker:'B',weight:.4,entry:200,last:200,last_raw:200,last_date:'20261102' },
] };
const nextQuotes = { session:'2026-11-03',intraday:false,
  quotes:{ A:{p:121,pc:110},B:{p:190,pc:200},SPY:{p:101,pc:100} } };

test('raw quotes are converted to the net-value share unit before calculating dollars', () => {
  const result = calculator()(null,nextQuotes,adjusted);
  assert.ok(Math.abs(result.amount-4600)<1e-8);
  assert.ok(Math.abs(result.pct-4600/106000)<1e-10);
});

test('a verified previous-close bridge updates year return once, including after closing', () => {
  const calculate = calculator('100000','yearResult');
  const result = calculate(null,nextQuotes,adjusted);
  assert.ok(Math.abs(result.pct-.106)<1e-10);
  assert.ok(Math.abs(result.benchmark-.0302)<1e-10);
  assert.equal(result.asOf,'2026-11-03');
  assert.equal(result.bridged,true);
  const sameDay = calculate(null,nextQuotes,{...adjusted,last_session:'20261103',stats:{strategy:.106,benchmark:.0302}});
  assert.equal(sameDay.pct,.106);
  assert.equal(sameDay.bridged,false);
});

test('unverified units, a multi-session gap or mismatched previous close retain the settled year date', () => {
  for (const eq of [
    {...adjusted,last_session:'20261030'},
    {...adjusted,position:[{...adjusted.position[0],last_raw:109},adjusted.position[1]]},
    {...adjusted,position:[{...adjusted.position[0],last_raw:null},adjusted.position[1]]},
  ]) {
    assert.equal(calculator()(null,nextQuotes,eq),null);
    const result = calculator('100000','yearResult')(null,nextQuotes,eq);
    assert.equal(result.pct,.06);
    assert.equal(result.bridged,false);
  }
});

test('invalid position or capital cannot silently omit a holding', () => {
  for (const change of [{ entry: 0 }, { entry: Infinity }, { weight: NaN }, { weight: 0 }]) {
    const input = { ...equity, position: [equity.position[0], { ...equity.position[1], ...change }] };
    assert.equal(calculator()(null, quotes, input), null);
  }
  assert.equal(calculator('NaN')(null, quotes, equity), null);
  assert.equal(calculator()(null, quotes, { position: [] }), null);
  assert.equal(calculator()(null, quotes, { position: {} }), null);
  assert.equal(calculator()(null, quotes, { position: [null] }), null);
});
