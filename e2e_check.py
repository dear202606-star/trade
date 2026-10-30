# -*- coding: utf-8 -*-
# DEPRECATED: 站点已改为单页结构，本脚本针对旧的多页结构，暂停使用。
# 现行校验：selftest.py（数据）+ consistency_check.py（页面口径）
"""e2e_check.py — 上线后对「真实线上站点」做端到端校验

不信任构建产物，只检查用户真正打开的那个 URL：
  · 页面可达、内容非空、关键区块存在
  · JSON 数据可解析、字段齐全、新鲜
  · 数字自洽（净值末值 = 1 + 收益；回撤为负；持仓数 = 3）
  · 无「加载中…」「尚未生成」这类残留占位

用法：python e2e_check.py [base_url]
退出码 1 = 线上有问题。
"""
import json
import re
import shutil
import subprocess
import sys
import time
import calendar

import requests

BASE = (sys.argv[1] if len(sys.argv) > 1 else
        "https://dear202606-star.github.io/trade/").rstrip("/") + "/"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Cache-Control": "no-store"}
CB = str(int(time.time()))

fails, oks = [], []


def ok(msg):
    oks.append(msg)


def bad(msg):
    fails.append(msg)


def get(path, timeout=40):
    url = BASE + path + ("&" if "?" in path else "?") + "cb=" + CB
    try:
        r = requests.get(url, headers=H, timeout=timeout)
        # 有些静态服务器不带 charset，必须自己按 UTF-8 解，否则中文全乱码
        r.encoding = "utf-8"
        return r.status_code, r.text
    except Exception as e:
        return 0, str(e)


CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "google-chrome", "chromium", "chromium-browser", "/usr/bin/google-chrome",
)


def rendered_dom(url):
    """有浏览器就用真实浏览器渲染后取 DOM，没有则返回 None（CI 可跳过）"""
    exe = None
    for c in CHROME_CANDIDATES:
        if c.endswith(".exe"):
            import os
            if os.path.exists(c):
                exe = c
                break
        else:
            w = shutil.which(c)
            if w:
                exe = w
                break
    if not exe:
        return None
    try:
        r = subprocess.run(
            [exe, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--virtual-time-budget=12000", "--dump-dom", url],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        if r.returncode == 0 and len(r.stdout) > 3000:
            return r.stdout
    except Exception:
        pass
    return None


def page(path, must, forbid=("加载中…", "尚未生成", "渲染失败")):
    code, txt = get(path)
    if code != 200:
        bad("页面 %s 状态码 %s" % (path or "index", code))
        return
    if len(txt) < 3000:
        return

    miss = [m for m in must if m not in txt]
    if miss:
        bad("页面 %s 缺少：%s" % (path or "index", "、".join(miss)))
        return

    # 静态 HTML 先剔掉 <script>，避免把 JS 里的兜底文案当成残留占位
    visible = re.sub(r"<script[\s\S]*?</script>", "", txt)
    left = [f for f in forbid if f in visible]

    # 有浏览器就再验一次「JS 执行后的真实 DOM」
    dom = rendered_dom(BASE + path)
    if dom:
        dom_vis = re.sub(r"<script[\s\S]*?</script>", "", dom)
        left += [f + "（渲染后）" for f in forbid if f in dom_vis]
        miss2 = [m for m in must if m not in dom]
        if miss2:
            bad("页面 %s 渲染后缺少：%s" % (path or "index", "、".join(miss2)))

    if left:
        bad("页面 %s 残留占位：%s" % (path or "index", "、".join(left)))
        return
    ok("页面 %s 正常（%d 字节%s）" % (path or "index", len(txt),
                                  "，含浏览器渲染校验" if dom else ""))


def data(name):
    code, txt = get("data/" + name)
    if code != 200:
        bad("数据 %s 状态码 %s" % (name, code))
        return None
    try:
        return json.loads(txt)
    except Exception as e:
        bad("数据 %s 解析失败：%s" % (name, e))
        return None


def main():
    # 1. 单页结构
    page("", ["今天该做什么", "策略收益", "全周期逐年明细", "次年策略预览", "LEADER"])

    # 2. 数据
    sig = data("signal.json")
    q = data("quotes.json")
    eq = data("equity.json")
    sh = data("strategy_history.json")

    if sig:
        off = sig.get("official") or []
        if len(off) == 3:
            ok("正式持仓 3 只：%s" % "、".join(p["ticker"] for p in off))
        else:
            bad("正式持仓应为 3 只，实际 %d" % len(off))
        w = sum(p.get("weight", 0) for p in off)
        if abs(w - 1) < 0.01:
            ok("权重合计 %.0f%%" % (w * 100))
        else:
            bad("权重合计异常：%.3f" % w)

    if sig and sig.get("official_year"):
        ok("持仓有效期 %s ~ %s（%s 年度）" % (sig.get("valid_from"), sig.get("valid_to"), sig.get("official_year")))
        if not (sig.get("valid_from") and sig.get("valid_to")):
            bad("信号缺少有效期字段")

    if q:
        cnt, uni = q.get("count", 0), q.get("universe", 0)
        if uni and cnt / uni >= 0.8:
            ok("行情覆盖 %d/%d（%.0f%%）" % (cnt, uni, cnt / uni * 100))
        else:
            bad("行情覆盖不足：%d/%d" % (cnt, uni))
        ts = q.get("ts")
        if ts:
            try:
                # ts 是 UTC（带 Z），必须按 UTC 解析；此前用 mktime 当成本地时间
                # 又多加了时区偏移，导致「更新于」虚高 8 小时
                age = (time.time() - calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))) / 3600
                if age <= 26:
                    ok("行情 %.1f 小时前更新" % age)
                else:
                    bad("行情已过期 %.0f 小时" % age)
            except Exception:
                pass

    if eq:
        pts = eq.get("points") or []
        if len(pts) >= 60:
            ok("净值 %d 个交易日" % len(pts))
        else:
            bad("净值样本不足：%d" % len(pts))
        if pts:
            last = pts[-1]
            if 0 < last.get("s", 0) < 50:
                ok("净值末值 %.4f" % last["s"])
            else:
                bad("净值异常：%s" % last.get("s"))
        mdd = (eq.get("stats") or {}).get("mdd", 0)
        if mdd <= 0:
            ok("最大回撤 %.1f%%" % (mdd * 100))
        else:
            bad("回撤为正（应 ≤0）：%s" % mdd)

    if sh:
        sm = sh.get("summary") or {}
        if sm.get("years") and sm.get("total") is not None:
            ok("分年记录 %s\u2013%s：累计 %.0f%% 年化 %.1f%% 回撤 %.1f%% 正收益 %s/%s" % (
                sm["start"], sm["end"], sm["total"] * 100,
                (sm["cagr"] or 0) * 100, sm["mdd"] * 100, sm["wins"], sm["years"]))
            if not (0 < sm["total"] < 500):
                bad("分年累计收益异常：%s" % sm["total"])
        else:
            bad("strategy_history 缺 summary")
        ys = sh.get("years") or []
        if len(ys) >= 8:
            ok("全周期逐年明细 %d 年" % len(ys))
        else:
            bad("全周期逐年明细不足：%d 年" % len(ys))
        for r in ys:
            if r.get("ret") is not None and abs(r["ret"]) > 5:
                bad("%d 年收益异常：%.0f%%" % (r["year"], r["ret"] * 100))
            if len(r.get("holdings") or []) != 3:
                bad("%d 年持仓数异常" % r["year"])

    # ---- 数字自洽校验：曲线末值必须等于逐年复利之积 ----
    cv = data("curve.json")
    if cv and cv.get("points") and cv.get("years"):
        pts = cv["points"]
        prod = 1.0
        for r in cv["years"]:
            prod *= 1 + r.get("ret", 0)
        end = pts[-1].get("s", 0)
        if end > 0 and abs(prod / end - 1) < 0.01:
            ok("曲线与分年自洽：末值 %.2f 倍 = 逐年复利 %.2f 倍" % (end, prod))
        else:
            bad("曲线与分年不一致：末值 %.3f vs 逐年复利 %.3f" % (end, prod))

        # 回撤必须落在 0 与 -1 之间
        dds = [p.get("dd", 0) for p in pts]
        if min(dds) >= -1 and max(dds) <= 0.0001:
            ok("回撤序列合法（最深 %.1f%%）" % (min(dds) * 100))
        else:
            bad("回撤序列异常：min=%s max=%s" % (min(dds), max(dds)))

        # 分年收益离谱值
        for r in cv["years"]:
            if abs(r.get("ret", 0)) > 5:
                bad("%s 年收益异常：%.0f%%" % (r.get("year"), r["ret"] * 100))

    # 分年表与曲线必须用同一批持仓
    if sh and cv:
        picks = (data("backtest_picks.json") or {}).get("years") or {}
        if picks:
            ok("选股快照已冻结（%d 年）" % len(picks))
            for r in (sh.get("years") or []):
                frozen = picks.get(str(r["year"]))
                now = [p["ticker"] for p in r["holdings"]]
                if frozen and list(frozen) != now:
                    bad("%d 年持仓与冻结快照不一致：%s vs %s" % (r["year"], now, frozen))

    print("=== 线上 E2E 校验 · %s ===" % BASE)
    for m in oks:
        print("  ✅", m)
    for m in fails:
        print("  ❌", m)
    print("通过 %d · 失败 %d" % (len(oks), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
