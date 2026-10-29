# -*- coding: utf-8 -*-
# DEPRECATED: 站点已改为单页结构，本脚本针对旧的多页结构，暂停使用。
# 现行校验：selftest.py（数据）+ consistency_check.py（页面口径）
"""线上真实性测试：对已部署站点做真实交互，验证每个功能真的生效（不是摆设）。

只读为主，唯一写操作是云同步（测试后清理）。
"""
import json, os, re, sys, time
import requests
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "https://dear202606-star.github.io/trade/"
OK, BAD = [], []
CFG = json.load(open("output/_supabase.json", encoding="utf-8"))


def ck(cond, name, detail=""):
    (OK if cond else BAD).append(name if cond else "%s %s" % (name, detail))
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else "  " + detail))


def supabase_rows():
    H = {"apikey": CFG["publishable_key"], "Authorization": "Bearer " + CFG["publishable_key"]}
    r = requests.get(CFG["url"] + "/rest/v1/holdings?select=id", headers=H, timeout=30)
    return r.json() if r.ok else []


def main():
    console_errors = []
    with sync_playwright() as p:
        br = p.chromium.launch(channel="chrome", headless=True)
        ctx = br.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True)
        pg = ctx.new_page()
        bad_resources = []
        pg.on("response", lambda r: bad_resources.append((r.status, r.url)) if r.status >= 400 else None)
        pg.on("pageerror", lambda e: console_errors.append("pageerror: " + str(e)))
        pg.on("console", lambda m: console_errors.append("console: " + m.text) if m.type == "error" else None)

        # ---------- 总览 ----------
        print("\n[总览]")
        pg.goto(BASE, wait_until="networkidle"); pg.wait_for_timeout(2000)
        body = pg.inner_text("body")
        ck("今天该做什么" in body, "页面正常渲染")
        eq = pg.locator("#eqRet").inner_text() if pg.locator("#eqRet").count() else ""
        ck(bool(re.search(r"[+\-]?\d", eq)), "策略累计收益有真实数值", "值=%r" % eq)
        hero = pg.locator("#verdict").inner_text() if pg.locator("#verdict").count() else ""
        ck(len(hero) >= 2, "今日结论有内容（%s）" % hero, "结论为空")
        ck("看账户" in body, "首屏有主行动按钮")
        line = pg.locator("#holdingLine").inner_text() if pg.locator("#holdingLine").count() else ""
        ck(all(t in line for t in ["WDC", "MU", "HOOD"]),
           "持仓摘要：%s" % line.strip()[:40], "持仓摘要缺失：%s" % line)
        # CTA 真的能跳
        cta = pg.locator("#heroCta")
        if cta.count():
            href = cta.get_attribute("href")
            ck("portfolio" in (href or ""), "下单按钮指向持仓页", "href=%s" % href)

        # ---------- 策略账户 ----------
        print("\n[策略账户]")
        pg.goto(BASE + "portfolio.html", wait_until="networkidle")
        pg.wait_for_timeout(2500)
        body = pg.inner_text("body")
        ck("当期策略表现" in body or "年当期策略表现" in body, "页面为「当期策略表现」")
        ck(all(k not in body for k in ["计划投入", "一键分配", "添加", "同步码", "成本价"]),
           "无个人录入控件")
        nrow = pg.locator(".pr-row").count()
        ck(nrow == 4, "明细 3 只 + 表头（%d）" % nrow)

        eq = pg.evaluate("() => fetch('data/equity.json?t='+Date.now()).then(r=>r.json())")
        ret_txt = pg.locator("#tRet").inner_text()
        got = float(ret_txt.replace("%", "").replace("+", ""))
        want = eq["stats"]["strategy"] * 100
        ck(abs(got - round(want, 2)) <= 0.05,
           "账户收益率 = 净值曲线（%s）" % ret_txt,
           "不一致：页面 %s / 净值 %+.2f%%" % (ret_txt, want))

        aud = pg.evaluate("() => window.__audit ? window.__audit() : {ok:true,fails:[]}")
        ck(aud.get("ok"), "账户自检通过", ";".join(aud.get("fails", [])[:2]))

        mv100 = pg.locator("#tMv").inner_text()
        pg.locator('[data-amt="10000"]').click(); pg.wait_for_timeout(900)
        mv10 = pg.locator("#tMv").inner_text()
        f = lambda t: float(t.replace("$", "").replace(",", "") or 0)
        ck(abs(f(mv10) * 10 - f(mv100)) / max(1, f(mv100)) < 0.02,
           "本金 100万→10万 线性缩放（%s → %s）" % (mv100, mv10))
        ret10 = pg.locator("#tRet").inner_text()
        ck(ret10 == ret_txt, "切换本金不改变收益率")
        pg.locator('[data-amt="100000"]').click(); pg.wait_for_timeout(600)

        # 两个价格来源都缺时，必须显示「—」而不是编一个数字
        def _strip_last(route):
            d = json.loads(route.fetch().text())
            if d.get("position"):
                d["position"][0]["last"] = None
            route.fulfill(status=200, body=json.dumps(d), headers={"Content-Type": "application/json"})
        pg.route("**/data/equity.json*", _strip_last)
        pg.route("**/data/quotes.json*", lambda r: r.fulfill(status=200,
                 body=json.dumps({"quotes": {}, "session": "", "ts": ""}),
                 headers={"Content-Type": "application/json"}))
        pg.reload(wait_until="networkidle"); pg.wait_for_timeout(2200)
        row = pg.locator(".pr-row").nth(1).inner_text()
        ck("—" in row, "缺价格显示「—」（该行：%s）" % row.replace(chr(10), " ")[:50],
           "缺价格仍显示数值：%s" % row.replace(chr(10), " ")[:60])
        pg.unroute("**/data/equity.json*")
        pg.unroute("**/data/quotes.json*")

        # ---------- 策略页 ----------
        print("\n[策略]")
        pg.goto(BASE + "strategy.html", wait_until="networkidle"); pg.wait_for_timeout(2500)
        txt = pg.inner_text("body")
        ck("回测：全周期" in txt, "策略页正常渲染")
        btotal = pg.locator("#bTotal").inner_text()
        ck("%" in btotal or "倍" in btotal, "全周期累计收益有值", btotal)
        n_year = pg.evaluate(
            "() => { const e = document.getElementById('years'); return e ? e.children.length : 0; }")
        ck(n_year >= 15, "分年记录 %d 条" % n_year)
        ck(pg.locator("#btChart svg").count() > 0, "回测图已绘制")
        # 图表在首屏之外，必须先滚动进视口再悬停，否则鼠标事件落在空白处
        pg.locator("#ch2").scroll_into_view_if_needed()
        pg.wait_for_timeout(600)
        box = pg.locator("#ch2").bounding_box()
        pg.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.45)
        pg.wait_for_timeout(400)
        pg.mouse.move(box["x"] + box["width"] * 0.58, box["y"] + box["height"] * 0.45)
        pg.wait_for_timeout(800)
        tip = pg.evaluate("""() => { const w = document.getElementById('ch2');
          const v = [...w.querySelectorAll('div')].filter(x => getComputedStyle(x).display === 'block');
          return v.map(x => x.innerText).filter(t => t && (t.includes('%') || t.includes('倍'))).pop() || ''; }""")
        ck("%" in tip or "倍" in tip, "图表十字线显示数值", tip.replace("\n", " ")[:40])

        # ---------- 信号历史页 ----------
        print("\n[历史信号]")
        pg.goto(BASE + "signals.html", wait_until="networkidle"); pg.wait_for_timeout(1800)
        ck("信号" in pg.title() or "信号" in pg.inner_text("body"), "信号页可访问")

        if bad_resources:
            print("  资源失败:", bad_resources[:4])
        ck(len(console_errors) == 0, "全站无 JS 报错",
           "；".join(console_errors[:2])[:120])
        br.close()

    print("\n通过 %d · 失败 %d" % (len(OK), len(BAD)))
    if BAD:
        print("失败项：")
        for b in BAD:
            print("  -", b)
    return 0 if not BAD else 1


if __name__ == "__main__":
    sys.exit(main())
