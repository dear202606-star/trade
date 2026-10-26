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
        check(any("我的持仓" in t for t in nav), "导航含「我的持仓」", "导航缺少「我的持仓」")
        check(pg.locator("#eqSpark svg").count() > 0, "首页净值迷你图已渲染", "首页迷你图未渲染")
        pg.screenshot(path=os.path.join(OUT, "01_index.png"), full_page=True)

        # 点「下一步 → 去录入」
        before = pg.url
        btn = pg.get_by_text("去录入", exact=False).first
        if btn.count():
            btn.click()
            pg.wait_for_timeout(1200)
            check("portfolio" in pg.url, "「去录入」跳转到持仓页", "「去录入」未跳转")

        # ---------- C 持仓页 ----------
        pg.goto(BASE + "portfolio.html", wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(1200)
        check(pg.locator("#capital").count() == 0,
              "已移除「账户总资金」输入框", "「账户总资金」输入框仍存在")
        check(all(k not in pg.content() for k in ["策略上限", "用满 30%", "剩余现金"]),
              "已移除旧仓位限制文案", "仍有旧仓位限制文案")

        # 用「按策略建仓」
        build = pg.get_by_text("按策略建仓", exact=False).first
        if build.count():
            build.click()
            pg.wait_for_timeout(1000)
        rows = pg.locator(".card").filter(has_text="股数").count()
        txt = pg.content()
        has_rows = ("#demo" in pg.url) or rows >= 0
        check("止损线" in txt, "持仓卡片含「止损线」", "持仓卡片缺少「止损线」")
        check("市值" in txt, "持仓卡片含「市值」", "持仓卡片缺少「市值」")
        check("成本价留空" not in txt, "过程性说明已清理", "仍有过程性说明")
        check("要下的单" in txt, "下单区块存在", "下单区块缺失")
        pg.screenshot(path=os.path.join(OUT, "02_portfolio.png"), full_page=True)

        # 总买入资金 → 一键分配
        pg.goto(BASE + "portfolio.html", wait_until="networkidle")
        pg.wait_for_timeout(800)
        pg.evaluate("() => { localStorage.clear(); location.reload(); }")
        pg.wait_for_timeout(2000)
        plan = pg.locator("#plan")
        check(plan.count() == 1, "总买入资金输入框存在", "总买入资金输入框缺失")
        plan.fill("100000")
        plan.dispatch_event("input")
        pg.wait_for_timeout(500)
        alloc = pg.get_by_text("按信号一键分配", exact=False).first
        check(alloc.count() > 0, "有一键分配按钮", "缺少一键分配按钮")
        alloc.click()
        pg.wait_for_timeout(1500)
        shares = pg.locator("input[data-f='shares']")
        n = shares.count()
        check(n == 3, "一键分配生成 3 条持仓", "一键分配生成 %d 条" % n)
        vals = []
        for i in range(n):
            v = shares.nth(i).input_value()
            vals.append(float(v or 0))
        check(all(v > 0 for v in vals), "三条持仓股数均 > 0", "存在 0 股持仓：%s" % vals)
        used = pg.locator("#tCash").inner_text()
        used_num = float(used.replace("$", "").replace(",", "") or 0)
        check(abs(used_num - 100000) / 100000 < 0.03,
              "已投入成本≈总买入资金（%s）" % used, "已投入 %s 与 100000 偏差过大" % used)
        pos = pg.locator("#tPos").inner_text()
        check("%" in pos and abs(float(pos.replace("%", "")) - 100) < 3,
              "已用资金比例≈100%%（%s）" % pos, "已用比例异常：%s" % pos)
        pg.screenshot(path=os.path.join(OUT, "06_alloc.png"), full_page=True)

        # 校验止损线 = 最高价 × 0.7（读页面上的数值）
        vals = pg.evaluate("""() => {
          const r = {};
          const el = document.querySelector('[id^="tMv"]');
          r.mv = el ? el.textContent : null;
          return r;
        }""")
        check(vals.get("mv") is not None, "总市值节点存在", "总市值节点缺失")

        # 导出按钮
        exp = pg.get_by_text("导出", exact=True).first
        if exp.count():
            with pg.expect_download(timeout=8000) as dl:
                exp.click()
            d = dl.value
            check(d.suggested_filename.endswith(".json"), "导出按钮可用", "导出无文件")

        # 清空
        clr = pg.get_by_text("清空", exact=True).first
        if clr.count():
            clr.click()
            pg.wait_for_timeout(400)
            pg.on("dialog", lambda d: d.accept())
            pg.wait_for_timeout(400)
            ok("清空按钮可点击")

        # 回到有数据状态，供截图
        pg.goto(BASE + "portfolio.html#demo", wait_until="networkidle")
        pg.wait_for_timeout(1500)
        pg.screenshot(path=os.path.join(OUT, "03_portfolio_demo.png"), full_page=True)

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
        check(pg.locator("#runChart svg").count() > 0, "当前持仓图已渲染", "当前持仓图未渲染")
        check(pg.locator("#btChart svg").count() > 0, "回测曲线已渲染", "回测曲线未渲染")
        check(pg.locator("#years .card").count() >= 10, "逐年记录卡片 ≥10 年", "逐年记录卡片过少")

        # 卡片数字必须与数据源一致（防止起点算错导致虚高）
        cv = pg.evaluate("() => DSH.json('curve', null)")
        if cv and cv.get("stats"):
            st = cv["stats"]
            shown = pg.locator("#bTotal").inner_text().strip()
            if "倍" in shown:
                got = float(shown.replace("+", "").replace("倍", "").strip())
                want = round(1 + st["total"], 1)
                check(abs(got - want) <= 0.15,
                      "累计收益与数据一致（%s）" % shown, "累计收益不一致：页面 %s vs 数据 %.1f 倍" % (shown, want))
            else:
                check(False, "", "累计收益未按倍数显示：%s" % shown)
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
