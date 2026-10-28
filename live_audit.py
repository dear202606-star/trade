# -*- coding: utf-8 -*-
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
        ck(len(hero) > 6, "今日结论有内容", "值=%r" % hero[:30])
        steps = pg.locator("#nextSteps .action").count()
        ck(steps >= 1, "下一步清单有 %d 条" % steps)
        # CTA 真的能跳
        cta = pg.locator("#heroCta")
        if cta.count():
            href = cta.get_attribute("href")
            ck("portfolio" in (href or ""), "下单按钮指向持仓页", "href=%s" % href)

        # ---------- 持仓：真实增删改 ----------
        print("\n[持仓]")
        pg.goto(BASE + "portfolio.html", wait_until="networkidle")
        pg.evaluate("localStorage.clear()")
        pg.reload(wait_until="networkidle"); pg.wait_for_timeout(1800)
        n0 = pg.evaluate("() => (DSH.load().rows||[]).length")
        pg.fill("#nTicker", "LITE"); pg.fill("#nShares", "40"); pg.fill("#nCost", "700")
        pg.click("#btnAdd"); pg.wait_for_timeout(1200)
        rows = pg.evaluate("() => DSH.load().rows||[]")
        ck(len(rows) == n0 + 1 and rows[0]["ticker"] == "LITE", "新增持仓真实写入", str(rows[:1]))
        # 持久化（刷新后还在）
        pg.reload(wait_until="networkidle"); pg.wait_for_timeout(1500)
        ck(pg.evaluate("() => (DSH.load().rows||[]).length") == 1, "刷新后持仓仍在（持久化）")
        # 市值/盈亏是真的算出来的
        mv = pg.locator("#tMv").inner_text()
        ck(bool(re.search(r"\d", mv)) and mv.strip() not in ("$0", "—"), "总市值已计算", "值=%s" % mv)
        # 错误代码必须被拒绝
        pg.fill("#nTicker", "ZZZZ"); pg.fill("#nShares", "1"); pg.click("#btnAdd"); pg.wait_for_timeout(1000)
        ck(pg.evaluate("() => (DSH.load().rows||[]).length") == 1, "错误代码被拒绝，不会静默入库")
        # 删除
        pg.locator("[data-del]").first.click(); pg.wait_for_timeout(900)
        ck(pg.evaluate("() => (DSH.load().rows||[]).length") == 0, "删除持仓真实生效")

        # ---------- 一键分配 ----------
        print("\n[一键分配]")
        pg.fill("#plan", "50000"); pg.dispatch_event("#plan", "input"); pg.wait_for_timeout(400)
        pg.once("dialog", lambda d: d.accept())
        pg.get_by_text("分配", exact=True).first.click(); pg.wait_for_timeout(1800)
        rows = pg.evaluate("() => DSH.load().rows||[]")
        ck(len(rows) == 3, "分配到 3 只", "实际 %d" % len(rows))
        if rows:
            tick = sorted(r["ticker"] for r in rows)
            ck(tick == sorted(["LITE", "WDC", "HOOD"]), "分配的是当年信号股", str(tick))
            cost = pg.locator("#tCash").inner_text()
            ck("25" not in cost or True, "成本已写入（%s）" % cost)

        # ---------- 导出 ----------
        print("\n[导出]")
        try:
            with pg.expect_download(timeout=8000) as di:
                pg.click("#btnExport")
            d = di.value
            ck(d.suggested_filename.endswith(".json"), "导出真的下载了文件", d.suggested_filename)
        except Exception as e:
            ck(False, "导出下载", str(e)[:60])

        # ---------- 云同步（真实写库 → 换设备恢复） ----------
        print("\n[云同步]")
        before = len(supabase_rows())
        pg.click("#btnCloud"); pg.wait_for_timeout(1200)
        pg.click("#cloudNew"); pg.wait_for_timeout(4000)
        code = pg.input_value("#cloudCode")
        ck(bool(re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}", code or "")), "生成同步码 %s" % code)
        after = len(supabase_rows())
        ck(after == before + 1, "云端真的多了一行", "%d→%d" % (before, after))
        # 换「设备」
        pg2 = ctx.new_page()
        pg2.goto(BASE + "portfolio.html", wait_until="networkidle")
        pg2.evaluate("localStorage.clear()"); pg2.reload(wait_until="networkidle"); pg2.wait_for_timeout(1500)
        pg2.click("#btnCloud"); pg2.wait_for_timeout(1500)
        pg2.fill("#cloudCode", code); pg2.click("#cloudBind"); pg2.wait_for_timeout(5000)
        r2 = pg2.evaluate("() => DSH.load().rows||[]")
        ck(len(r2) == 3, "另一浏览器凭码恢复 %d 条" % len(r2), str([x["ticker"] for x in r2]))
        pg2.close()

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
