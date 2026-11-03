import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';

function calculator(amount = '100000') {
  const context = vm.createContext({
    window: { addEventListener() {} }, location: { pathname: '/report.html' },
    localStorage: { getItem: () => amount },
  });
  vm.runInContext(readFileSync(new URL('./site_src/assets/app.js', import.meta.url), 'utf8'), context);
  return context.window.DSH.today;
}

const equity = { exposure: 1, position: [
  { ticker: 'A', weight: 0.6, entry: 100, last: 999 },
  { ticker: 'B', weight: 0.4, entry: 200, last: 999 },
] };
const quotes = { quotes: { A: { p: 110, pc: 100 }, B: { p: 190, pc: 200 } } };

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
    const input = { quotes: { ...quotes.quotes, B: bad } };
    assert.equal(calculator()(null, input, equity), null);
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
