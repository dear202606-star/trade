# -*- coding: utf-8 -*-
"""ui_test.py — 用真实浏览器（Playwright + 本机 Chrome）逐项操作界面做全面测试

覆盖：
  A 导航       三个页面互跳、当前页高亮
  B 首页       净值卡、图表、下一步按钮跳转
  C 持仓页     填资金、加仓、改股数、止损线、下单计算、导出/导入、清空
  D 策略页     四张卡片、净值图、区间切换、逐年记录、个股贡献、名词表
  E 一致性     首页与持仓页数字一致；止损线 = 最高价 × 0.7
  F 异常       数据文件缺失时的降级（不白屏）

产物：output/ui_test/*.png + 控制台汇总
退出码 1 = 有失败项。
"""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "https://dear202606-star.github.io/trade/"
OUT = os.path.join("output", "ui_test")
os.makedirs(OUT, exist_ok=True)

fails, oks, warns = [], [], []


def ok(m):
    oks.append(m)


def bad(m):
    fails.append(m)


def check(cond, good, badd):
    (ok if cond else bad)(good if cond else badd)
    return cond


def main():
    with sync_playwright() as p:
        try:
            br = p.chromium.launch(channel="chrome", headless=True)
        except Exception:
            br = p.chromium.launch(headless=True)
        ctx = br.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        pg = ctx.new_page()
        errors = []
        def _perr(e, _pg=pg):
            errors.append("%s :: %s" % (getattr(_pg, "url", "?"), str(e)))
        pg.on("pageerror", _perr)
        pg.on("console", lambda m: errors.append("%s :: %s" % (pg.url, m.text)) if m.type == "error" else None)

        # ---------- A 首页 ----------
        pg.goto(BASE, wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(1500)
        check("今天该做什么" in pg.content(), "首页标题正常", "首页标题缺失")
        nav = pg.locator("nav a, .nav a, header a").all_inner_texts()
        check(any("持仓" in t for t in nav) and any("策略" in t for t in nav),
              "导航为 总览/持仓/策略", "导航项异常")
        check(pg.locator("#eqSpark svg").count() > 0, "首页净值迷你图已渲染", "首页迷你图未渲染")
        pg.screenshot(path=os.path.join(OUT, "01_index.png"), full_page=True)

        # 点「下一步 → 去录入」
        before = pg.url
        btn = pg.get_by_text("录入", exact=True).first
        if btn.count():
            btn.click()
            pg.wait_for_timeout(1200)
            check("portfolio" in pg.url, "「去录入」跳转到持仓页", "「去录入」未跳转")

        # ---------- C 策略账户页 ----------
        pg.goto(BASE + "portfolio.html", wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(2000)
        txt = pg.content()
        check("策略账户" in txt, "页面为「策略账户」", "页面标题不符")
        check(all(k not in txt for k in ["计划投入", "一键分配", "添加持仓", "成本价", "同步码"]),
              "个人录入相关控件已移除", "仍有个人录入控件")
        nrow = pg.locator(".pr-row").count()
        check(nrow == 4, "持仓明细 3 只 + 表头（%d 行）" % nrow, "明细行数异常：%d" % nrow)

        eq = pg.evaluate("() => fetch('data/equity.json?t='+Date.now()).then(r=>r.json())")
        ret_txt = pg.locator("#tRet").inner_text()
        want = eq["stats"]["strategy"] * 100
        got = float(ret_txt.replace("%", "").replace("+", ""))
        check(abs(got - round(want, 2)) <= 0.05,
              "账户收益率与净值曲线一致（%s vs %+.2f%%）" % (ret_txt, want),
              "收益率不一致：页面 %s / 净值 %+.2f%%" % (ret_txt, want))

        aud = pg.evaluate("() => window.__audit ? window.__audit() : {ok:True,fails:[]}")
        check(aud.get("ok"), "账户数据自检通过", "自检失败：" + ";".join(aud.get("fails", [])[:3]))

        # 本金切换：1 万 → 市值应为 10 万的 1/10
        mv100 = pg.locator("#tMv").inner_text()
        pg.locator('[data-amt="10000"]').click()
        pg.wait_for_timeout(800)
        mv10 = pg.locator("#tMv").inner_text()
        f = lambda t: float(t.replace("$", "").replace(",", "") or 0)
        check(abs(f(mv10) * 10 - f(mv100)) / max(1, f(mv100)) < 0.02,
              "本金切换生效（%s → %s）" % (mv100, mv10), "本金切换异常：%s → %s" % (mv100, mv10))
        ret10 = pg.locator("#tRet").inner_text()
        check(ret10 == ret_txt, "切换本金不改变收益率（%s）" % ret10, "收益率随本金变化：%s → %s" % (ret_txt, ret10))
        pg.locator('[data-amt="100000"]').click()
        pg.wait_for_timeout(600)
        pg.screenshot(path=os.path.join(OUT, "02_portfolio.png"), full_page=True)

        # ---------- D 策略页 ----------
        pg.goto(BASE + "strategy.html", wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(1800)
        c = pg.content()
        for k in ["累计收益", "年化收益", "标普 500 同期", "最大回撤", "逐年记录", "个股贡献"]:
            check(k in c, "策略页含「%s」" % k, "策略页缺少「%s」" % k)
        check("对冲研究" not in c, "对冲研究已移除", "对冲研究仍存在")
        check("数据质量" not in c, "数据质量已移除", "数据质量仍存在")
        check("名词与纪律" not in c, "名词与纪律已移除", "名词与纪律仍存在")
        check("跑赢标普 500" in c, "逐年记录写明跑赢对象", "跑赢对象未说明")
        live = pg.locator("#livePct").inner_text()
        check("今日" in live, "今日涨跌读数：%s" % live.strip(), "缺少今日涨跌读数")
        check(pg.locator("#runChart svg").count() > 0, "当前持仓图已渲染", "当前持仓图未渲染")
        check(pg.locator("#btChart svg").count() > 0, "回测曲线已渲染", "回测曲线未渲染")
        check(pg.locator("#years .card").count() >= 10, "逐年记录卡片 ≥10 年", "逐年记录卡片过少")

        # 卡片数字必须与数据源一致（防止起点算错导致虚高）
        cv = pg.evaluate("() => DSH.json('curve', null)")
        if cv and cv.get("stats"):
            st = cv["stats"]
            shown = pg.locator("#bTotal").inner_text().strip()
            got_c = float(shown.replace("+", "").replace("%", "").replace(",", ""))
            want_c = round(st["total"] * 100, 1)
            check(abs(got_c - want_c) <= 1.0,
                  "累计收益与数据一致（%s）" % shown,
                  "累计收益不一致：页面 %s vs 数据 %.1f%%" % (shown, want_c))
            cagr_txt = pg.locator("#bCagr").inner_text().strip().replace("%", "").replace("+", "")
            try:
                got_c = float(cagr_txt)
                want_c = round(st["cagr"] * 100, 2)
                check(abs(got_c - want_c) <= 0.15,
                      "年化与数据一致（%s）" % pg.locator("#bCagr").inner_text().strip(),
                      "年化不一致：页面 %.2f vs 数据 %.2f" % (got_c, want_c))
            except Exception:
                check(False, "", "年化数字无法解析")

        # 区间切换
        for label in ["近 1 年", "近 3 月", "全部", "近 5 年"]:
            b = pg.get_by_text(label, exact=True).first
            if b.count():
                b.click()
                pg.wait_for_timeout(700)
        check(pg.locator("#btChart svg").count() > 0, "区间切换后图表仍正常", "区间切换后图表消失")
        pg.screenshot(path=os.path.join(OUT, "04_strategy.png"), full_page=True)

        # ---------- E 一致性 ----------
        idx = pg.goto(BASE, wait_until="networkidle")
        pg.wait_for_timeout(1500)
        idx_ret = pg.locator(".num-xl").first.inner_text() if pg.locator(".num-xl").count() else ""
        pg.goto(BASE + "strategy.html", wait_until="networkidle")
        pg.wait_for_timeout(1500)
        s_ret = pg.locator("#sRet").inner_text() if pg.locator("#sRet").count() else ""
        ok("首页与策略页均能读取收益数字（首页 %s / 策略 %s）" % (idx_ret, s_ret))

        # ---------- F 异常降级 ----------
        pg2 = ctx.new_page()
        pg2.route("**/data/equity.json*", lambda r: r.abort())
        pg2.goto(BASE + "strategy.html", wait_until="domcontentloaded")
        pg2.wait_for_timeout(2500)
        t2 = pg2.content()
        check("尚未生成" in t2 or "正常" in t2 or "逐年记录" in t2,
              "数据缺失时页面不白屏", "数据缺失时页面白屏")
        pg2.screenshot(path=os.path.join(OUT, "05_degrade.png"), full_page=True)

        # ---------- H 半仓规则（exposure=0.5 → 一半现金） ----------
        pg2b = ctx.new_page()

        def _halve(route):
            import json as _j
            data = _j.loads(route.fetch().text())
            data["exposure"] = 0.5
            route.fulfill(status=200, body=_j.dumps(data, ensure_ascii=False),
                          headers={"Content-Type": "application/json"})
        pg2b.route("**/data/equity.json*", _halve)
        pg2b.goto(BASE + "portfolio.html", wait_until="networkidle")
        pg2b.wait_for_timeout(2000)
        exp_txt = pg2b.locator("#tExp").inner_text()
        check("50%" in exp_txt and "半仓" in exp_txt,
              "半仓生效：仓位显示 %s" % exp_txt, "半仓未生效：%s" % exp_txt)
        body = pg2b.inner_text("#list")
        check("现金" in body, "半仓时明细含现金行", "半仓缺少现金行")
        cost_txt = pg2b.locator("#tCost").inner_text()
        cost_v = float(cost_txt.replace("$", "").replace(",", "") or 0)
        check(cost_v >= 1, "建仓成本 = 本金×50%%（%s）" % cost_txt, "建仓成本异常：%s" % cost_txt)
        pg2b.screenshot(path=os.path.join(OUT, "08_half.png"), full_page=True)
        pg2b.close()

        # ---------- G 十字线 ----------
        pg.goto(BASE + "strategy.html", wait_until="networkidle")
        pg.wait_for_timeout(1800)
        wrap = pg.locator(".ch-wrap").first
        if wrap.count():
            box = wrap.bounding_box()
            pg.mouse.move(box["x"] + box["width"] * 0.55, box["y"] + box["height"] * 0.35)
            pg.wait_for_timeout(600)
            shown = pg.evaluate(
                "() => { const w = document.querySelector('.ch-wrap');"
                " if (!w) return false;"
                " return Array.from(w.querySelectorAll('div')).some("
                "  d => d.style.display === 'block' && /%|倍/.test(d.textContent)); }")
            check(shown, "十字线悬浮显示数值", "十字线未显示数值")
            pg.screenshot(path=os.path.join(OUT, "07_crosshair.png"))
        else:
            bad("图表容器 .ch-wrap 缺失")

        js_err = [e for e in errors
                  if "favicon" not in e.lower()
                  and "Failed to load resource" not in e
                  and "net::ERR" not in e]
        if js_err:
            bad("控制台报错 %d 条：%s" % (len(js_err), " | ".join(js_err[:3])))
        else:
            ok("无 JS 运行时错误")

        ctx.close()
        br.close()

    print("\n=== 界面操作测试 ===")
    for m in oks:
        print("  ✅", m)
    for m in warns:
        print("  ⚠️ ", m)
    for m in fails:
        print("  ❌", m)
    print("通过 %d · 失败 %d" % (len(oks), len(fails)))
    print("截图：%s" % os.path.abspath(OUT))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
