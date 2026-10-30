# -*- coding: utf-8 -*-
"""全系统口径一致性检查（单页结构）。

校验：数据源自洽 + 页面显示值与数据源一致 + 无 JS 错误 + 移动端无溢出。
"""
import json
import re
import sys

import requests
from playwright.sync_api import sync_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1
        else "https://dear202606-star.github.io/trade/")
BASE = BASE.rstrip("/") + "/"
H = {"User-Agent": "Mozilla/5.0", "Cache-Control": "no-store"}
OK, BAD = [], []


def ck(cond, name, detail=""):
    (OK if cond else BAD).append(name if cond else "%s %s" % (name, detail))
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else "  " + detail))


def load(n):
    r = requests.get(BASE + "data/" + n, headers=H, timeout=30)
    r.raise_for_status()
    return r.json()


def num(t):
    if t is None:
        return None
    t = str(t).replace(",", "").replace("$", "").replace("%", "").replace("+", "").strip()
    try:
        return float(t)
    except Exception:
        return None


def main():
    eq = load("equity.json")
    cv = load("curve.json")
    sig = load("signal.json")
    q = load("quotes.json")
    sh = load("strategy_history.json")
    pv = load("preview.json")

    strat = eq["stats"]["strategy"] * 100
    bench = eq["stats"]["benchmark"] * 100

    print("\n[数据源]")
    ck(abs(sum(p["weight"] for p in eq["weights"]) - 1) < 1e-6, "权重合计 = 1")
    sw = {p["ticker"]: round(p["weight"], 6) for p in sig["official"]}
    ew = {p["ticker"]: round(p["weight"], 6) for p in eq["weights"]}
    ck(sw == ew, "signal 与 equity 持仓一致", "%s vs %s" % (sw, ew))
    ck(all(p.get("sector") and p["sector"] != "Unknown" for p in sig["official"]),
       "持仓行业均已分类")
    ck(len(sig["official"]) == 3, "正式持仓 3 只")

    with sync_playwright() as p:
        br = p.chromium.launch(channel="chrome", headless=True)
        ctx = br.new_context(viewport={"width": 1280, "height": 1000})
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:100]))
        pg.goto(BASE, wait_until="networkidle")
        pg.wait_for_timeout(2600)

        print("\n[当期]")
        v = num(pg.inner_text("#yearRet"))
        ck(v is not None and abs(v - strat) <= 0.05,
           "当期收益 = 数据源（%.2f%%）" % strat, "页面 %s" % v)
        mv, cost, pnl = (num(pg.inner_text("#tMv")), num(pg.inner_text("#tCost")),
                         num(pg.inner_text("#tPnl")))
        ck(bool(mv and cost and pnl) and abs((mv - cost) - pnl) <= max(1, abs(pnl) * 1e-6),
           "市值 − 成本 = 累计盈亏", "%s-%s≠%s" % (mv, cost, pnl))
        vs = pg.inner_text("#yearVs")
        m = re.search(r"同期标普\s*([+\-]?[\d.]+)%", vs)
        ck(bool(m) and abs(float(m.group(1)) - bench) <= 0.05,
           "同期标普 = 数据源（%.2f%%）" % bench, vs[:40])
        foot = pg.inner_text("#footStamp")
        ck("（北京）" in foot, "页脚标注时区（%s）" % foot.strip(), "缺时区")
        body = pg.inner_text("body")
        _ts = re.findall(r"\d{2}-\d{2}\s+\d{2}:\d{2}", body)
        ck(len(_ts) <= 1, "全页仅一个时间戳（%s）" % (_ts or "无"),
           "出现多个时间戳：%s" % _ts)

        print("\n[今日]")
        t = pg.inner_text("#todayPct")
        tv = num(t)
        qs = q["quotes"]
        # 与页面一致：按真实持股市值加权（不是目标权重）
        amt = 100000.0
        day_amt = 0.0
        mv_now = 0.0
        for p in eq["position"]:
            qq = qs.get(p["ticker"]) or {}
            shares = amt * p["weight"] / p["entry"] if p["entry"] else 0
            px = qq.get("p") or p["last"]
            pc = qq.get("pc")
            mv_now += shares * px
            if pc:
                day_amt += shares * (px - pc)
        exp = (day_amt / (mv_now - day_amt) * 100) if mv_now > day_amt else 0.0
        ck(tv is not None and abs(tv - exp) < 1.5,
           "今日涨跌与行情方向一致（页面 %+.2f / 加权 %+.2f）" % (tv or 0, exp),
           "页面 %s" % t)
        lbl = pg.inner_text("#todayLabel")
        ck("盘中" in lbl or "收盘" in lbl, "今日注明盘中/收盘（%s）" % lbl.strip())

        print("\n[全周期]")
        bt = num(pg.inner_text("#bTotal"))
        cv_total = (cv["points"][-1]["s"] - 1) * 100
        ck(bt is not None and abs(bt - cv_total) <= 1.5,
           "全周期 = 曲线末值（%.0f%%）" % cv_total, "页面 %s" % bt)
        bc = num(pg.inner_text("#bCagr"))
        ck(bc is not None and abs(bc - sh["summary"]["cagr"] * 100) <= 0.15,
           "年化 = strategy_history（%.2f%%）" % (sh["summary"]["cagr"] * 100), "页面 %s" % bc)
        bm = num(pg.inner_text("#bMdd"))
        ck(bm is not None and abs(abs(bm) - abs(sh["summary"]["worst_intra"] * 100)) <= 0.15,
           "最大回撤 = 谷底口径（%.1f%%）" % (sh["summary"]["worst_intra"] * 100), "页面 %s" % bm)

        print("\n[动态性]")
        body = pg.inner_text("body")
        shown = set(re.findall(r"20\d\d-\d{2}(?:-\d{2})?", body))
        y = int(str(eq["base_date"])[:4]) + 1
        allowed = {
            "%d-12" % (y - 1),
            "%d-12-31" % (y - 1),
            str(sig.get("valid_from", ""))[:7],
            str(sig.get("valid_to", ""))[:7],
            str(pv.get("base_month", ""))[:7],
            str(pv.get("official_month", ""))[:7],
        }
        allowed = {x for x in allowed if x}
        unknown = sorted(d for d in shown if d not in allowed)
        ck(not unknown, "页面上所有日期均来自数据源（%d 个）" % len(shown),
           "无法追溯：%s" % unknown)

        lbl = pg.inner_text("#todayLabel")
        ck(any(k in lbl for k in ("盘中", "收盘")), "今日状态为盘中/收盘（%s）" % lbl.strip(),
           "状态异常：%s" % lbl)

        titles = pg.evaluate("() => [...document.querySelectorAll('.card-title')].map(e => e.innerText)")
        ck(any(str(y) in t for t in titles), "卡片标题含当年（%d）" % y,
           "标题未随年份：%s" % titles)
        ck(any(str(y + 1) in t for t in titles), "卡片标题含次年（%d）" % (y + 1),
           "标题未随年份：%s" % titles)

        print("\n[年度]")
        n = pg.locator("#yearRows .yr-row").count()
        ck(n == len(sh["years"]), "年度表 %d 行（数据 %d）" % (n, len(sh["years"])), "行数不符")

        ck(not errs, "无 JS 报错", "；".join(errs[:2]))

        ctx2 = br.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        pg2 = ctx2.new_page()
        pg2.goto(BASE, wait_until="networkidle")
        pg2.wait_for_timeout(2400)
        over = pg2.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        ck(over <= 0, "移动端无横向溢出（%dpx）" % over, "溢出 %dpx" % over)
        br.close()

    print("\n通过 %d · 失败 %d" % (len(OK), len(BAD)))
    for b in BAD:
        print("  -", b)
    return 0 if not BAD else 1


if __name__ == "__main__":
    sys.exit(main())
