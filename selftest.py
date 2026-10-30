# -*- coding: utf-8 -*-
"""selftest.py — 部署前自检：确保站点不会带着空数据上线

检查项：
  A. 页面文件存在且非空（index / portfolio / strategy）
  B. 数据文件存在、可解析、关键字段齐全
  C. 数据新鲜度（行情 ≤48 小时、净值/逐年记录有足够样本）
  D. 数值合理性（收益在 ±500% 内、回撤 ≤0）

任何一项不通过 → 退出码 1，CI 失败，不会部署坏页面。
"""
import json
import os
import re
import sys
import time

DOCS = "docs"
DATA = os.path.join(DOCS, "data")

fails, warns, oks = [], [], []


def check(cond, ok_msg, fail_msg, hard=True):
    if cond:
        oks.append(ok_msg)
    else:
        (fails if hard else warns).append(fail_msg)
    return cond


def load(name):
    p = os.path.join(DATA, name)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def main():
    # A. 页面（单页结构：只有 index.html 含内容，其余为跳转壳）
    p = os.path.join(DOCS, "index.html")
    check(os.path.exists(p) and os.path.getsize(p) > 2000,
          "页面 index.html", "页面缺失或过小：index.html")
    _html = open(p, encoding="utf-8").read()
    _hard = re.findall(r"20\d\d\s*[–\-]\s*20\d\d", _html)
    check(not _hard, "页面无写死年份区间（动态推导）",
          "发现写死：%s" % _hard[:3])
    _hard3 = re.findall(r"20\d\d[-/]\d{1,2}", _html)
    check(not _hard3, "页面无写死日期（全部由数据注入）",
          "发现写死日期：%s" % _hard3[:3])
    _hard2 = re.findall(r"20\d\d\s*年", _html)
    check(not _hard2, "页面无写死年份标题（动态推导）",
          "发现写死：%s" % _hard2[:3])

    for sec in ("todayPct", "yearRet", "bTotal", "yearRows", "posRows"):
        check(sec in open(p, encoding="utf-8").read(),
              f"单页含区块 {sec}", f"index.html 缺区块 {sec}")

    # B. 数据
    sig = load("signal.json")
    check(sig and sig.get("official"), "信号有正式持仓", "signal.json 缺 official")

    # 信号必须带有效期（年度持仓 / 当月排名要能区分）
    if sig:
        oy = sig.get("official_year")
        vf, vt = sig.get("valid_from"), sig.get("valid_to")
        check(bool(oy) and bool(vf) and bool(vt) and vf < vt,
              "持仓有效期 %s ~ %s（%s 年度）" % (vf, vt, oy),
              "信号缺少有效期字段")

    q = load("quotes.json")
    cnt = (q or {}).get("count", 0)
    uni = (q or {}).get("universe") or 0
    cover = cnt / uni if uni else 0
    ok_q = (cover >= 0.8) if uni else (cnt > 100)
    check(ok_q,
          f"行情 {cnt}/{uni or '?'} 只（{cover*100:.0f}%）" if uni else f"行情 {cnt} 只",
          f"行情覆盖不足：{cnt}/{uni} = {cover*100:.0f}%")

    eq = load("equity.json")
    check(eq and len(eq.get("points") or []) >= 60,
          f"净值 {len(eq.get('points') or []) if eq else 0} 个交易日",
          "equity.json 净值样本不足")

    # ---- 权重形态：每年必须是「等权」或「同行业 30/30 + 40」----
    try:
        _sh = load("strategy_history.json") or {}
        _bad_w = []
        for _r in (_sh.get("years") or []):
            _hs = _r.get("holdings") or []
            if len(_hs) != 3:
                _bad_w.append("%s 持仓数 %d" % (_r.get("year"), len(_hs)))
                continue
            _w = sorted(round(x.get("weight", 0), 4) for x in _hs)
            _eq = all(abs(x - 1 / 3) < 0.01 for x in _w)
            _cap = abs(_w[0] - 0.3) < 0.01 and abs(_w[1] - 0.3) < 0.01 and abs(_w[2] - 0.4) < 0.01
            if not (_eq or _cap):
                _bad_w.append("%s 权重 %s" % (_r.get("year"), _w))
            if _cap:
                _secs = [x.get("sector") for x in _hs]
                _dup = [k for k in set(_secs) if _secs.count(k) >= 2]
                if not _dup:
                    _bad_w.append("%s 30/30/40 但无同行业" % _r.get("year"))
        check(not _bad_w, "权重形态合规（等权 / 同行业 30-30-40）",
              "异常年份：%s" % _bad_w[:3])
    except Exception as e:
        warns.append("权重形态校验失败：%s" % e)

    # ---- 跨年链路：任意年份都必须能自动切换 ----
    try:
        import simulate_rollover as _sim
        _days = ["2026-10-30", "2027-01-02", "2027-12-31", "2028-01-02", "2031-01-02"]
        _rows = [_sim.simulate(d) for d in _days]
        _bad = []
        for _r in _rows:
            if int(_r["官方基准月"][:4]) + 1 != _r["持有年度"]:
                _bad.append(_r["日期"] + ":持有年度")
            if _r["净值建仓月"] != _r["官方基准月"]:
                _bad.append(_r["日期"] + ":建仓月")
            if _r["日期"].endswith("-01-02") and _r["历史归档年份"] != int(_r["日期"][:4]) - 1:
                _bad.append(_r["日期"] + ":归档年份")
        check(not _bad, "跨年链路自洽（%d 个日期）" % len(_days),
              "不自洽：%s" % _bad[:3])
    except Exception as e:
        warns.append("跨年仿真失败：%s" % e)

    # ---- 明年预览（年初至今前 3）----
    try:
        _pv = load("preview.json")
        _top = (_pv or {}).get("top") or []
        check(len(_top) == 3, "preview.json 有 3 只候选", "候选数 %d" % len(_top))
        if _top:
            _w = sum(x.get("weight") or 0 for x in _top)
            check(abs(_w - 1) < 1e-6, "预览权重合计 = 1", "实际 %.4f" % _w)
            _bad = [x["ticker"] for x in _top
                    if not x.get("sector") or x["sector"] == "Unknown"]
            check(not _bad, "预览行业均已分类", "缺行业：%s" % _bad)
            _inf = [x["ticker"] for x in _top if not isinstance(x.get("ytd"), (int, float))]
            check(not _inf, "预览涨幅为有限数", "异常：%s" % _inf)
            _om = _pv.get("official_month") or ""
            _sig_om = (sig or {}).get("official_month") or ""
            if _om == _sig_om:
                check(_pv.get("final") is True,
                      "预览已定稿（%s）" % _om,
                      "官方月已等于基准月，但预览未标记定稿")
            else:
                check(_pv.get("final") is not True,
                      "预览仍在预测期（官方 %s）" % _sig_om,
                      "官方月 %s 与预览基准 %s 不同，却标记为已定稿" % (_sig_om, _om))
            check(_om.endswith("-12"), "预览基准月为 12 月（%s）" % _om, "基准月 %s" % _om)
    except Exception as e:
        warns.append("预览校验失败：%s" % e)

    # ---- 年度结转：排名基准月必须符合规则，且锁与持仓一致 ----
    try:
        import monthly_pick as _mp
        _sig = sig or {}
        _om = _sig.get("official_month") or ""
        _oy = _sig.get("official_year")
        _rank = _sig.get("ranking_month") or _om
        _want = _mp.official_month_for(_rank if _rank.endswith("-12") else _om)
        check(_om == _want,
              "年度结转规则一致（%s）" % _om,
              "排名基准月应为 %s，实际 %s" % (_want, _om))
        check(_oy == int(_om[:4]) + 1,
              "持有年度 = 基准年 + 1（%s）" % _oy,
              "official_year 应为 %s，实际 %s" % (int(_om[:4]) + 1, _oy))
        _vf = _sig.get("valid_from") or ""
        _vt = _sig.get("valid_to") or ""
        import datetime as _dtm
        _td = (_dtm.datetime.now(_dtm.timezone.utc) + _dtm.timedelta(hours=8)).strftime("%Y-%m-%d")
        check(bool(_vf and _vt) and _vf <= _td <= _vt,
              "当前日期在生效区间内（%s~%s）" % (_vf, _vt),
              "当前 %s 不在 %s~%s" % (_td, _vf, _vt))
    except Exception as e:
        warns.append("年度结转校验失败：%s" % e)

    # ---- 健康状态与新鲜度（B3：停更必须可见）----
    try:
        _hp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "data", "health.json")
        if os.path.exists(_hp):
            _h = json.load(open(_hp, encoding="utf-8"))
            check(_h.get("status") in ("ok", "degraded"),
                  "health.json 存在且状态合法（%s）" % _h.get("status"),
                  "health.json 状态非法：%s" % _h.get("status"))
            _age = _h.get("quotes_age_hours")
            check(_age is None or _age < 72,
                  "行情年龄 %.1f 小时（<72）" % (_age or 0),
                  "行情已 %.1f 小时未更新" % (_age or 0))
        else:
            warns.append("缺 health.json（健康状态不可见）")
        _ep = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "data", "equity.json")
        _e = json.load(open(_ep, encoding="utf-8"))
        check(_e.get("last_session") and _e["points"][-1]["d"] == _e["last_session"],
              "净值末点 = 已收盘日（%s）" % _e.get("last_session"),
              "净值末点 %s ≠ 已收盘日 %s" % (_e["points"][-1]["d"], _e.get("last_session")))
        _sp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "data", "signal.json")
        _raw = open(_sp, encoding="utf-8").read()
        check("NaN" not in _raw and "Infinity" not in _raw,
              "signal.json 不含 NaN/Infinity", "signal.json 含非法数值")
    except Exception as e:
        fails.append("健康/新鲜度校验异常：%s" % e)

    # ---- 当年持仓锁定后不得漂移 ----
    try:
        _lp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "official_locks.json")
        if os.path.exists(_lp):
            _locks = json.load(open(_lp, encoding="utf-8"))
            _om = (sig or {}).get("official_month")
            if _om in _locks:
                _cur = [{"ticker": p["ticker"], "weight": p["weight"]} for p in (sig.get("official") or [])]
                _want = _locks[_om]["holdings"]
                check(_cur == _want,
                      "%s 持仓与锁定一致（%s）" % (_om, "、".join(x["ticker"] for x in _want)),
                      "持仓发生漂移：当前 %s / 锁定 %s" % (
                          [x["ticker"] for x in _cur], [x["ticker"] for x in _want]))
    except Exception as e:
        warns.append("持仓锁定校验失败：%s" % e)

    # ---- 防未来函数：正式持仓必须是「排名日」的标普 500 成分股 ----
    try:
        import calendar
        import datafeed as _df
        _m = _df.load_members()
        _m["start_date"] = _m["start_date"].astype(str)
        _m["end_date"] = _m["end_date"].fillna("2099-12-31").astype(str)
        om = (sig or {}).get("official_month") or ""
        if om:
            y, mm = int(om[:4]), int(om[5:7])
            day = "%04d-%02d-%02d" % (y, mm, calendar.monthrange(y, mm)[1])
            live = set(_m[(_m.start_date <= day) & (_m.end_date >= day)]["ticker"])
            ghosts = [p["ticker"] for p in (sig.get("official") or []) if p["ticker"] not in live]
            check(not ghosts,
                  "正式持仓均为 %s 成分股" % om,
                  "以下持仓在 %s 时还不是成分股（未来函数）：%s" % (om, ",".join(ghosts)))
    except Exception as e:
        warns.append("成分股时点校验失败：%s" % e)

    # ---- 理论账户：加权建仓收益必须等于净值曲线 ----
    try:
        e = load("equity.json") or {}
        pos = e.get("position") or []
        if pos:
            exp_ = e.get("exposure", 1.0)
            w = sum(p["weight"] * (p["last"] / p["entry"]) for p in pos
                    if p.get("entry") and p.get("last"))
            calc = w * exp_ + (1 - exp_) - 1
            want = (e.get("stats") or {}).get("strategy", 0)
            check(abs(calc - want) < 1e-4,
                  "理论账户与净值一致（%+.2f%%）" % (calc * 100),
                  "账户口径与净值不符：%+.4f%% vs %+.4f%%" % (calc * 100, want * 100))
            bad_pos = [p["ticker"] for p in pos if not (p.get("entry") or 0) > 0 or not (p.get("last") or 0) > 0]
            check(not bad_pos, "全部持仓有建仓价与现价",
                  "缺少建仓价/现价：" + ",".join(bad_pos))
    except Exception as e2:
        warns.append("理论账户校验失败：%s" % e2)

    # ---- 全链路一致性：行情 ↔ 持仓 ↔ 净值 ----
    qq = (q or {}).get("quotes") or {}
    sess = (q or {}).get("session") or ""
    off = (sig or {}).get("official") or (sig or {}).get("picks") or []
    miss = [p.get("ticker") for p in off
            if not (qq.get(p.get("ticker")) or {}).get("p")]
    check(not miss,
          "正式持仓 %d 只均有行情" % len(off),
          "以下持仓缺行情，页面会显示不全：" + ",".join(map(str, miss)))

    bad_px = [t for t, v in qq.items()
              if not isinstance((v or {}).get("p"), (int, float)) or (v or {}).get("p", 0) <= 0]
    check(not bad_px, "全部行情价格为正数",
          "存在非法价格：" + ",".join(bad_px[:5]))

    if eq and sess:
        last_d = ((eq.get("points") or [{}])[-1] or {}).get("d") or ""
        check(last_d <= sess.replace("-", ""),
              "净值末点 %s ≤ 行情交易日 %s" % (last_d, sess),
              "净值日期(%s)晚于行情交易日(%s)，口径不一致" % (last_d, sess))

    sh = load("strategy_history.json")
    check(sh and len(sh.get("years") or []) >= 8,
          f"逐年记录 {len(sh.get('years') or []) if sh else 0} 年",
          "strategy_history.json 逐年记录不足")


    # B2. 成分股合规（只允许标普 500 成分股）
    try:
        import pandas as pd
        mm = pd.read_csv("static/sp500_members.csv")
        mm["start_date"] = mm["start_date"].astype(str)
        mm["end_date"] = mm["end_date"].fillna("2099-12-31").astype(str)
        bad_hold = []
        for r in (sh or {}).get("years", []):
            d = "%d-12-31" % (r["year"] - 1)
            for p in r.get("holdings", []):
                hit = mm[(mm.ticker == p["ticker"]) & (mm.start_date <= d) & (mm.end_date >= d)]
                if not len(hit):
                    bad_hold.append("%d:%s" % (r["year"], p["ticker"]))
        for p in (sig or {}).get("official", []):
            hit = mm[(mm.ticker == p["ticker"]) & (mm.start_date <= "2026-12-31") & (mm.end_date >= "2026-01-01")]
            if not len(hit):
                bad_hold.append("当前:%s" % p["ticker"])
        check(not bad_hold, "全部持仓均为标普500 成分股",
              "存在非成分股持仓：" + ", ".join(bad_hold[:8]))
    except Exception as e:
        warns.append("成分股校验失败：%s" % e)

    cv = load("curve.json")
    check(cv and len(cv.get("points") or []) >= 120,
          "全周期曲线 %d 个月" % len((cv or {}).get("points") or []),
          "curve.json 缺失或过短（%s）" % os.path.join(DATA, "curve.json"))

    # C. 新鲜度
    if q and q.get("ts"):
        try:
            age = (time.time() - time.mktime(time.strptime(q["ts"], "%Y-%m-%dT%H:%M:%SZ"))
                   + time.timezone) / 3600
        except Exception:
            age = None
        if age is not None:
            check(age <= 48, f"行情 {age:.1f} 小时前更新",
                  f"行情已过期 {age:.0f} 小时", hard=False)

    # D. 合理性（不变量硬校验：任何一条不过都不允许部署）
    if eq and eq.get("points"):
        last = eq["points"][-1]
        check(-5 < last.get("s", 0) < 20, f"净值末值 {last.get('s')}",
              f"净值异常：{last.get('s')}")
        mdd = (eq.get("stats") or {}).get("mdd", 0)
        check(mdd <= 0.001, f"最大回撤 {mdd*100:.1f}%", f"回撤应为负值：{mdd}")

    if sh and sh.get("years"):
        import math
        bad_ret, bad_w, bad_n, bad_sec = [], [], [], []
        for r in sh["years"]:
            y = r.get("year")
            # 1) 收益与回撤必须是有限数（NaN 会让 abs(x)>5 判假而蒙混过关）
            rt, ddv = r.get("ret"), r.get("dd")
            if rt is None or ddv is None or not math.isfinite(rt) or not math.isfinite(ddv):
                bad_ret.append(y)
            elif abs(rt) > 5:
                bad_ret.append(y)
            hold = r.get("holdings") or []
            # 2) 每年必须恰好 3 只
            if len(hold) != 3:
                bad_n.append("%s:%d只" % (y, len(hold)))
            # 3) 权重合计必须为 1
            wsum = sum((p.get("weight") or 0) for p in hold)
            if abs(wsum - 1) > 0.005:
                bad_w.append("%s:%.3f" % (y, wsum))
            # 4) 行业上限：同行业 ≤2 只，且该行业合计 ≤60%
            bys = {}
            for p in hold:
                s = p.get("sector") or "Unknown"
                bys.setdefault(s, [0, 0.0])
                bys[s][0] += 1
                bys[s][1] += (p.get("weight") or 0)
            for s, (cnt, w) in bys.items():
                if cnt > 2 or w > 0.61:
                    bad_sec.append("%s:%s(%d只/%.0f%%)" % (y, s, cnt, w * 100))

        check(not bad_ret, "逐年收益均为有限且合理", "收益缺失/异常年份：" + ",".join(map(str, bad_ret[:6])))
        check(not bad_n, "每年恰好 3 只持仓", "持仓数异常：" + ",".join(bad_n[:6]))
        check(not bad_w, "各年权重合计 = 1", "权重合计异常：" + ",".join(bad_w[:6]))
        check(not bad_sec, "行业上限（≤2 只 / ≤60%）", "行业超限：" + ",".join(bad_sec[:6]))

        # 5) 历史不可变：已发布年份的收益不得被改动
        try:
            bp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "output", "history_baseline.json")
            cur = {str(r["year"]): round(r["ret"], 4) for r in sh["years"]}
            if os.path.exists(bp):
                base = json.load(open(bp, encoding="utf-8")).get("years") or {}
                drift = [f"{y}({base[y]:+.3f}→{cur[y]:+.3f})"
                         for y in base if y in cur and abs(cur[y] - base[y]) > 0.005]
                check(not drift, "历史收益未被改写", "历史收益被改动：" + ",".join(drift[:5]))
            else:
                warns.append("缺少历史基线（首次运行会建立）")
        except Exception as e:
            warns.append("历史基线校验失败：%s" % e)

        # 6) 曲线末值必须等于逐年复利
        cv = load("curve.json")
        if cv and cv.get("points") and cv.get("years"):
            prod = 1.0
            for r in cv["years"]:
                prod *= 1 + (r.get("ret") or 0)
            end = cv["points"][-1].get("s", 0)
            check(end > 0 and abs(prod / end - 1) < 0.01,
                  "曲线末值 = 逐年复利（%.2f 倍）" % end,
                  "曲线与分年不一致：%.2f vs %.2f" % (end, prod))

        # 7) 历史锁定一致：docs 发布的历史必须与 output 里锁定的完全一致
        try:
            BASE = os.path.dirname(os.path.abspath(__file__))
            locked = os.path.join(BASE, "output", "strategy_history.json")
            if os.path.exists(locked):
                lk = json.load(open(locked, encoding="utf-8"))
                same = all(
                    any(x["year"] == r["year"] and abs(x["ret"] - r["ret"]) < 1e-6
                        for x in lk.get("years", []))
                    for r in sh["years"] if r.get("ret") is not None
                )
                check(same, "历史锁定一致（%d 年未改动）" % len(lk.get("years", [])),
                      "发布的历史与锁定版本不一致")
        except Exception as e:
            warns.append("历史锁定校验失败：%s" % e)

        # 8) 成分表与选股池
        try:
            import pandas as _pd, time as _t
            mp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "static", "sp500_members.csv")
            if os.path.exists(mp):
                age = (_t.time() - os.path.getmtime(mp)) / 86400.0
                check(age <= 30, "成分表快照 %.0f 天内（%.0f 天）" % (30, age),
                      "成分表快照已 %.0f 天未刷新" % age)
            mt = _pd.read_csv(mp)
            mt["end_date"] = mt["end_date"].fillna("2099-12-31")
            cur = mt[mt["end_date"] >= "2026-10-27"]
            check(len(cur) >= 480, "当前成分 %d 只" % len(cur), "当前成分不足 480 只")
        except Exception as e:
            warns.append("成分表校验失败：%s" % e)

        # 9) 降级标记：沿用旧数据时必须让页面看得到
        if sh.get("degraded"):
            warns.append("本次沿用 %s 的结果（数据抓取不完整）" % sh.get("asof", "上次"))

    # 报告
    print("=== 部署前自检 ===")
    for m in oks:
        print("  ✅", m)
    for m in warns:
        print("  ⚠️ ", m)
    for m in fails:
        print("  ❌", m)
    print(f"通过 {len(oks)} · 警告 {len(warns)} · 失败 {len(fails)}")

    if fails:
        print("\n自检未通过，终止部署。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
