# -*- coding: utf-8 -*-
"""stealth_fetch.py — 反爬绕过工具箱（用于公开市场数据）

手段（按成本从低到高）
  1. curl_cffi TLS 指纹伪装（chrome120/114/edge101…）
  2. 真实浏览器（Playwright + Chrome）拿 Cookie/Session，再用会话复用请求
  3. 反检测脚本：抹掉 navigator.webdriver、伪造 plugins/languages/chrome 对象
  4. 请求头完整化：Referer / Origin / Accept-Language / sec-ch-ua 系列
  5. 备用入口：移动端 API、CSV 导出、RSS、站点 sitemap 中的 JSON

用法
  python stealth_fetch.py --url "https://..." [--browser] [--dump 500]
"""
import argparse
import io
import json
import sys
import time
import urllib.parse
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

UA_CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

HDRS_FULL = {
    "User-Agent": UA_CHROME,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7,ko;q=0.6",
    "Accept-Encoding": "gzip, deflate, br",
    "sec-ch-ua": '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh','en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
window.chrome = window.chrome || {runtime: {}, loadTimes: function(){}, csi: function(){}};
const origQuery = window.navigator.permissions && window.navigator.permissions.query;
if (origQuery) {
  window.navigator.permissions.query = (p) => (
    p && p.name === 'notifications'
      ? Promise.resolve({state: Notification.permission})
      : origQuery(p)
  );
}
"""


def via_curl_cffi(url, referer=None, timeout=30):
    from curl_cffi import requests as cr
    h = dict(HDRS_FULL)
    if referer:
        h["Referer"] = referer
    r = cr.get(url, headers=h, impersonate="chrome", timeout=timeout)
    return r.status_code, r.text


def via_browser(url, wait_ms=6000, referer=None, headless=True):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch(channel="chrome", headless=headless,
                               args=["--disable-blink-features=AutomationControlled",
                                     "--no-sandbox"])
        ctx = br.new_context(user_agent=UA_CHROME, locale="zh-CN",
                             viewport={"width": 1440, "height": 900},
                             extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
        ctx.add_init_script(STEALTH_JS)
        pg = ctx.new_page()
        if referer:
            pg.set_extra_http_headers({"Referer": referer})
        resp = pg.goto(url, timeout=45000, wait_until="domcontentloaded")
        pg.wait_for_timeout(wait_ms)
        body = pg.content()
        status = resp.status if resp else None
        cookies = {c["name"]: c["value"] for c in ctx.cookies()}
        br.close()
        return status, body, cookies


def via_browser_api(url, wait_ms=3000):
    """在浏览器里直接 fetch（带 Cookie 与同源策略），返回 JSON 文本"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch(channel="chrome", headless=True,
                               args=["--disable-blink-features=AutomationControlled"])
        ctx = br.new_context(user_agent=UA_CHROME, locale="zh-CN")
        ctx.add_init_script(STEALTH_JS)
        pg = ctx.new_page()
        host = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(url))
        try:
            pg.goto(host, timeout=30000, wait_until="domcontentloaded")
            pg.wait_for_timeout(1500)
        except Exception:
            pass
        out = pg.evaluate("""async (u) => {
            try {
                const r = await fetch(u, {credentials: 'include'});
                return [r.status, (await r.text()).slice(0, 5000)];
            } catch (e) { return ['ERR', String(e)]; }
        }""", url)
        br.close()
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--browser", action="store_true", help="用真实浏览器打开")
    ap.add_argument("--api", action="store_true", help="在浏览器内 fetch（带 Cookie）")
    ap.add_argument("--referer", default=None)
    ap.add_argument("--dump", type=int, default=300)
    a = ap.parse_args()

    t0 = time.time()
    if a.api:
        st, body = via_browser_api(a.url)
        print("browser-fetch:", st, "%.1fs" % (time.time() - t0))
        print(body[:a.dump])
        return
    if a.browser:
        st, body, cookies = via_browser(a.url, referer=a.referer)
        print("browser:", st, "len=%d" % len(body), "cookies=%s" % list(cookies)[:5],
              "%.1fs" % (time.time() - t0))
        print(body[:a.dump].replace("\n", " "))
        return
    try:
        st, body = via_curl_cffi(a.url, referer=a.referer)
        print("curl_cffi:", st, "len=%d" % len(body), "%.1fs" % (time.time() - t0))
        print(body[:a.dump].replace("\n", " "))
    except Exception as e:
        print("curl_cffi ERR:", str(e)[:100])


if __name__ == "__main__":
    main()
