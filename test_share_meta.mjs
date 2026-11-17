/* 微信/社交转发分享卡片契约：每个页面都必须带完整 og meta，
   og:image 指向方形 og-icon.png（微信把缩略图裁成正方形，横图会被裁掉两侧、看不清）。
   缺任何一项，微信转发就会退化成纯文字空白卡片。 */
import { readFileSync, existsSync } from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';

const ROOT = new URL('./site_src/', import.meta.url);
const PAGES = ['index.html', 'portfolio.html', 'strategy.html', 'signals.html', 'report.html'];
const ABS_IMAGE = 'https://dear202606-star.github.io/trade/assets/og-icon.png';

for (const page of PAGES) {
  test(`${page} carries complete Open Graph share metadata`, () => {
    const html = readFileSync(new URL(page, ROOT), 'utf8');
    for (const prop of ['og:type', 'og:site_name', 'og:title', 'og:description',
                        'og:image', 'og:image:type', 'og:image:width', 'og:image:height',
                        'og:image:alt', 'og:url']) {
      assert.match(html, new RegExp(`property="${prop}"`),
        `${page} missing ${prop}`);
    }
    assert.match(html, /name="twitter:card" content="summary_large_image"/,
      `${page} missing large twitter card`);
    assert.match(html, /name="description"/, `${page} missing description`);
  });

  test(`${page} og:image points to the square 600x600 icon`, () => {
    const html = readFileSync(new URL(page, ROOT), 'utf8');
    assert.ok(html.includes(ABS_IMAGE), `${page} og:image must be the square icon URL`);
    assert.ok(html.includes('content="600"'), `${page} missing 600 dimension`);
    assert.ok(!html.includes('og-card.png'), `${page} still references the old wide card`);
  });
}

function pngSize(rel) {
  const p = new URL(rel, ROOT);
  assert.ok(existsSync(p), `${rel} missing`);
  const buf = readFileSync(p);
  assert.deepEqual([...buf.slice(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10], `${rel} not a PNG`);
  return { buf, w: buf.readUInt32BE(16), h: buf.readUInt32BE(20) };
}

test('og-icon.png is a valid 600x600 square PNG within size limits', () => {
  const { buf, w, h } = pngSize('assets/og-icon.png');
  assert.equal(w, 600, 'square icon width');
  assert.equal(h, 600, 'square icon height');
  assert.ok(buf.length > 0 && buf.length <= 2 * 1024 * 1024, 'icon too large or empty');
});

test('og-card.png remains a valid 1200x630 wide PNG for large-image previews', () => {
  const { buf, w, h } = pngSize('assets/og-card.png');
  assert.equal(w, 1200, 'wide card width');
  assert.equal(h, 630, 'wide card height');
  assert.ok(buf.length <= 2 * 1024 * 1024, 'wide card too large');
});
