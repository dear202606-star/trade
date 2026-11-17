/* 微信/社交转发分享卡片契约：每个页面都必须带完整 og meta，
   og:image 指向的本地图片必须真实存在且尺寸合规。
   缺任何一项，微信转发就会退化成纯文字空白卡片。 */
import { readFileSync, existsSync } from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';

const ROOT = new URL('./site_src/', import.meta.url);
const PAGES = ['index.html', 'portfolio.html', 'strategy.html', 'signals.html', 'report.html'];
const ABS_IMAGE = 'https://dear202606-star.github.io/trade/assets/og-card.png';

for (const page of PAGES) {
  test(`${page} carries complete Open Graph share metadata`, () => {
    const html = readFileSync(new URL(page, ROOT), 'utf8');
    for (const prop of ['og:type', 'og:site_name', 'og:title', 'og:description',
                        'og:image', 'og:image:type', 'og:image:width', 'og:image:height',
                        'og:url']) {
      assert.match(html, new RegExp(`property="${prop.replace(/:/g, ':')}"`),
        `${page} missing ${prop}`);
    }
    assert.match(html, /name="twitter:card" content="summary_large_image"/,
      `${page} missing large twitter card`);
    assert.match(html, /name="description"/, `${page} missing description`);
  });

  test(`${page} og:image is the absolute HTTPS share card URL`, () => {
    const html = readFileSync(new URL(page, ROOT), 'utf8');
    assert.ok(html.includes(ABS_IMAGE), `${page} og:image must be absolute HTTPS URL`);
    assert.ok(html.includes('content="1200"') && html.includes('content="630"'),
      `${page} missing og image dimensions`);
  });
}

test('og-card.png exists and is a valid 1200x630 PNG within WeChat size limits', () => {
  const p = new URL('assets/og-card.png', ROOT);
  assert.ok(existsSync(p), 'assets/og-card.png missing');
  const buf = readFileSync(p);
  assert.ok(buf.length > 0 && buf.length <= 2 * 1024 * 1024, 'og image too large or empty');
  // PNG signature
  assert.deepEqual([...buf.slice(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10], 'not a PNG');
  // IHDR width/height at bytes 16..24
  assert.equal(buf.readUInt32BE(16), 1200, 'wrong width');
  assert.equal(buf.readUInt32BE(20), 630, 'wrong height');
});
