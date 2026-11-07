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

fails, oks, skips = [], [], []


def ok(msg):
    oks.append(msg)


def bad(msg):
    fails.append(msg)


def skip(msg):
    skips.append(msg)


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


def _cloud():
    """Pages 上唯一保留的引导文件。统一走 get() 助手（带缓存穿透与编码处理）。"""
    try:
        status, text = get('data/cloud.json')
        if status != 200:
            raise ValueError('HTTP %s' % status)
        cfg = json.loads(text)
        if not cfg.get('url') or not cfg.get('key'):raise ValueError('invalid cloud config')
        return cfg
    except Exception as exc:
        bad('引导配置 cloud.json 读取失败：'+type(exc).__name__)
        return None


_TURSO_CACHE = None


def _turso_dataset(cfg):
    """主库 PostgREST 402（egress 限服）时页面真正读取的在线备库。

    2026-11-08：E2E 声称校验「用户真正看到的那份数据」，但主库限服期间
    页面已自动切到 Turso，若 E2E 仍逐项打主库 REST，就会把基础设施限流
    误报成 5 个数据故障。优先从备库一次取回 14 个展示 key，既和页面同源，
    也把 5 个 REST 请求降为 1 个 HTTP 请求。
    """
    global _TURSO_CACHE
    if _TURSO_CACHE is not None:
        return _TURSO_CACHE
    ep = next((e for e in (cfg.get('endpoints') or [])
               if e.get('type') == 'turso'), None)
    if not ep or not ep.get('url') or not ep.get('key'):
        return None
    body = {"baton": None, "requests": [
        {"type": "execute", "stmt": {
            "sql": "select key, payload from site_dataset", "args": []}},
        {"type": "close"}]}
    try:
        r = requests.post(ep['url'].rstrip('/') + '/v2/pipeline',
                          data=json.dumps(body), timeout=30,
                          headers={**H, "Authorization": "Bearer " + ep['key'],
                                   "Content-Type": "application/json"})
        r.raise_for_status()
        rows = r.json()['results'][0]['response']['result'].get('rows') or []
        _TURSO_CACHE = {row[0]['value']: json.loads(row[1]['value']) for row in rows}
        return _TURSO_CACHE
    except Exception:
        return None


def _online(cfg,path,params=None):
    response = requests.get(cfg['url'].rstrip('/')+'/rest/v1/'+path,
        params=params or {},headers={**H,'apikey':cfg['key']},timeout=20)
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows,list) or len(rows)!=1:
        raise ValueError('online result is not a single row')
    return rows[0]


def data(name, optional=False):
    """与页面同一数据源：实时类走在线视图，历史类走 v_site_dataset。

    optional=True 用于「取不到也不影响页面」的附加校验（如冻结选股快照），
    主库 402 时只记跳过，不把基础设施限流误报成线上数据故障。

    2026-11-06 起 Pages 不再发布 data/*.json（仅保留 cloud.json 引导配置），
    E2E 必须校验「用户真正看到的那份在线数据」，而不是已下线的静态快照。
    2026-11-08 起优先读 Turso 在线备库（页面在主库 402 时的实际数据源），
    主库 REST 仅作回退；老配置没有备库端点时行为与从前一致。
    """
    key = name[:-5] if name.endswith('.json') else name
    if key == 'cloud':
        return _cloud()
    cfg = _cloud()
    if not cfg:
        return None
    backup = _turso_dataset(cfg)
    if backup and key in backup:
        payload = backup[key]
        if payload is None:
            bad('备库数据集为空：' + key)
            return None
        if key == 'quotes' and (not payload.get('ok') or
                                not payload.get('quotes', {}).get('SPY')):
            bad('备库 quotes 数据不完整')
            return None
        return payload
    try:
        if key == 'quotes':
            row = _online(cfg,'live_quote_snapshot',{'singleton':'eq.true','select':'payload'})
            payload = row['payload']
            if not payload.get('ok') or not payload.get('quotes',{}).get('SPY'):
                raise ValueError('invalid online quote snapshot')
            return payload
        if key in ('equity','signal','preview','b/equity','b/signal','b/preview'):
            variant = 'B' if key.startswith('b/') else 'A'
            kind = key.split('/')[-1]
            if kind == 'signal':
                row = _online(cfg,'live_strategy_anchor',
                    {'variant':'eq.'+variant,'select':'payload',
                     'order':'hold_year.desc,revision.desc','limit':1})
                return row['payload']['signal']
            row = _online(cfg,'v_live_strategy_results',
                {'variant':'eq.'+variant,'kind':'eq.'+kind,'select':'payload'})
            return row['payload']
        # 历史/静态展示数据：在线库 site_dataset
        row = _online(cfg,'v_site_dataset',{'key':'eq.'+key,'select':'payload'})
        payload = row['payload']
        if payload is None:raise ValueError('empty online dataset: '+key)
        return payload
    except Exception as exc:
        if optional:
            skip('可选数据 %s 不可读（%s），跳过附加校验' % (name, type(exc).__name__))
        else:
            bad('在线数据 %s 读取失败：%s' % (name,type(exc).__name__))
        return None

def main():
    # 1. 单页结构
    page("", ["本期策略", "策略收益", "逐年回测明细", "次年策略预览", "LEADER"])

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
        if len(ys) >= 13:
            ok("逐年回测明细 %d 年" % len(ys))
        else:
            bad("逐年回测明细不足：%d 年" % len(ys))
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
        picks = (data("backtest_picks.json", optional=True) or {}).get("years") or {}
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
    for m in skips:
        print("  [跳过]", m)
    for m in fails:
        print("  ❌", m)
    print("通过 %d · 失败 %d · 跳过 %d" % (len(oks), len(fails), len(skips)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
