/* 持仓表与次年预览的行业标签对齐契约。
   两张卡片的 .sec-tag 必须共用同一套竖排规则、相同 gap、无缩进，
   否则在不同视口下会出现一边缩进、一边贴左的错位（2026-11-17 线上反馈）。 */
import { readFileSync } from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';

const css = readFileSync(new URL('./site_src/assets/dashboard.css', import.meta.url), 'utf8');
const js = readFileSync(new URL('./site_src/assets/page.js', import.meta.url), 'utf8');

test('both cards render industry labels with the shared .sec-tag class', () => {
  // 两处都用 .sec-tag，共用字体/底色/圆角，样式天然一致。
  const tagUses = (js.match(/class="sec-tag"/g) || []).length;
  assert.ok(tagUses >= 2, `expected sec-tag in both cards, found ${tagUses}`);
});

test('ticker cells and preview cards share one identical vertical layout rule', () => {
  assert.match(css, /\.tk-cell,\.pv-main\{display:flex;flex-direction:column;align-items:flex-start;gap:2px\}/,
    'both containers must share the same flex-column layout and gap');
});

test('industry labels have no indent inside either card, at any breakpoint', () => {
  assert.match(css, /\.tk-cell \.sec-tag,\.pv-main \.sec-tag\{margin-left:0;margin-top:0\}/,
    'labels in both cards must be flush-left with zero indent');
  // 不允许残留给预览标签单独加缩进/不同 gap 的断点覆盖。
  assert.doesNotMatch(css, /\.pv-main \.sec-tag\{[^}]*margin-left:[1-9]/,
    'preview label must not get a standalone indent override');
  assert.doesNotMatch(css, /\.pr \.tk-cell\{[^}]*gap:3px/,
    'holdings cell must not override the shared gap at any breakpoint');
});

test('ticker typography matches across both cards', () => {
  const prTk = css.match(/\.pr \.tk\{([^}]*)\}/);
  const pvTk = css.match(/\.pv-main \.tk\{([^}]*)\}/);
  assert.ok(prTk && pvTk, 'both ticker rules must exist');
  for (const prop of ['font-size:15px', 'font-weight:700', 'letter-spacing:-.4px']) {
    assert.ok(prTk[1].includes(prop), `holdings ticker missing ${prop}`);
    assert.ok(pvTk[1].includes(prop), `preview ticker missing ${prop}`);
  }
});
