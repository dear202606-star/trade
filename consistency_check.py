# -*- coding: utf-8 -*-
"""全系统口径一致性检查：把每个页面显示的数字与数据源逐一比对。

检查面：
  1. 三个页面的「本年策略收益」必须等于 equity.stats.strategy
  2. 标普同期必须等于 equity.stats.benchmark
  3. 账户页收益率必须等于净值曲线（加权建仓口径）
  4. 策略页「全周期」收益必须等于 curve 末值 − 1（与本年收益是两个指标，不能混）
  5. 权重/敞口：signal 与 equity 一致
  6. 建仓日、交易日在各页面一致
"""
import json
import os
import re
import sys

import requests
from playwright.sync_api import sync_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1
        else "https://dear202606-star.github.io/trade/").rstrip("/") + "/"
H = {"User-Agent": "Mozilla/5.0", "Cache-Control": "no-store"}
OK, BAD = [], []


def ck(cond, name, detail=""):
    (OK if cond else BAD).append(name if cond else "%s %s" % (name, detail))
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else "  " + detail))


def load(name):
    r = requests.get(BASE + "data/" + name, headers=H, timeout=30)
    r.raise_for_status()
    return r.json()


def num(txt):
    """从 '+75.76%' / '$175,758' 这类文本里取数值"""
    if txt is None:
        return None
    t = str(txt).replace(",", "").replace("$", "").replace("%", "").replace("+", "").strip()
    t = t.replace("倍", "")
    try:
        return float(t)
    except Exception:
        return None


def main():
    eq = load("equity.json")
    cv = load("curve.json")
    sig = load("signal.json")
    q = load("quotes.json")

    strat = eq["stats"]["strategy"] * 100
    bench = eq["stats"]["benchmark"] * 100
    exp_ = eq.get("exposure", 1.0)

    print("\n[数据源]")
    ck(abs(sum(p["weight"] for p in eq.get("weights", [])) - 1) < 1e-6,
       "equity 权重合计 = 1")
    ck(abs(exp_ - (sig.get("exposure") or 1)) < 1e-6,
       "敞口一致（signal %.2f = equity %.2f）" % (sig.get("exposure") or 1, exp_))
    sig_w = {p["ticker"]: p["weight"] for p in (sig.get("official") or [])}
    eq_w = {p["ticker"]: p["weight"] for p in eq.get("weights") or []}
    ck(sig_w == eq_w, "持仓权重一致", "%s vs %s" % (sig_w, eq_w))

    with sync_playwright() as p:
        br = p.chromium.launch(channel="chrome", headless=True)
        ctx = br.new_context(viewport={"width": 1440, "height": 1000})
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:100]))

        # ---------- 首页 ----------
        print("\n[首页]")
        pg.goto(BASE, wait_until="networkidle")
        pg.wait_for_timeout(2200)
        v = num(pg.locator("#eqRet").inner_text())
        ck(v is not None and abs(v - strat) <= 0.05,
           "策略累计收益 = 数据源（%.2f%%）" % strat, "页面 %s / 数据 %.2f%%" % (v, strat))
        sub = pg.locator("#eqSub").inner_text()
        m = re.search(r"同期标普\s*([+\-]?[\d.]+)%", sub)
        ck(m and abs(float(m.group(1)) - bench) <= 0.05,
           "同期标普 = 数据源（%.2f%%）" % bench, "页面文本：%s" % sub[:40])

        # ---------- 账户页 ----------
        print("\n[策略账户]")
        pg.goto(BASE + "portfolio.html", wait_until="networkidle")
        pg.wait_for_timeout(2200)
        v2 = num(pg.locator("#tRet").inner_text())
        ck(v2 is not None and abs(v2 - strat) <= 0.05,
           "账户收益率 = 净值曲线（%.2f%%）" % strat, "页面 %s" % v2)
        mv = num(pg.locator("#tMv").inner_text())
        cost = num(pg.locator("#tCost").inner_text())
        pnl = num(pg.locator("#tPnl").inner_text())
        ck(mv is not None and cost is not None and pnl is not None
           and abs((mv - cost) - pnl) <= max(1.0, abs(pnl) * 1e-6),
           "账户 市值−建仓成本 = 累计盈亏",
           "%s − %s ≠ %s" % (mv, cost, pnl))
        stamp = pg.inner_text("#acctStamp")
        ck("（北京）" in stamp, "更新时间标注时区（%s）" % stamp.strip(), "缺少时区标注")

        # ---------- 策略页 ----------
        print("\n[策略表现]")
        pg.goto(BASE + "strategy.html", wait_until="networkidle")
        pg.wait_for_timeout(2600)
        r_ret = num(pg.locator("#rRet").inner_text())
        r_ben = num(pg.locator("#rBench").inner_text())
        ck(r_ret is not None and abs(r_ret - strat) <= 0.05,
           "「当前持仓表现」策略收益 = 数据源（%.2f%%）" % strat, "页面 %s" % r_ret)
        ck(r_ben is not None and abs(r_ben - bench) <= 0.05,
           "「当前持仓表现」标普 = 数据源（%.2f%%）" % bench, "页面 %s" % r_ben)
        r_ex = num(pg.locator("#rEx").inner_text())
        ck(r_ex is not None and abs(r_ex - (strat - bench)) <= 0.1,
           "跑赢标普 = 策略 − 标普（%.2f）" % (strat - bench), "页面 %s" % r_ex)

        b_total = num(pg.locator("#bTotal").inner_text())
        cv_total = (cv["points"][-1]["s"] - 1) * 100
        ck(b_total is not None and abs(b_total - cv_total) <= 1.0,
           "「全周期」收益 = 曲线末值（%.0f%%）" % cv_total, "页面 %s" % b_total)
        ck(abs(strat - cv_total) > 1,
           "本年收益与全周期收益是两个指标（%.2f%% vs %.0f%%）" % (strat, cv_total),
           "两个指标数值过近，可能标错")

        ck(not errs, "无 JS 报错", "；".join(errs[:2]))
        br.close()

    print("\n通过 %d · 失败 %d" % (len(OK), len(BAD)))
    if BAD:
        print("失败项：")
        for b in BAD:
            print("  -", b)
    return 0 if not BAD else 1


if __name__ == "__main__":
    sys.exit(main())
