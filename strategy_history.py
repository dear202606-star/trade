# -*- coding: utf-8 -*-
"""strategy_history.py — 生成逐年策略记录（无未来函数）

对每年 Y（2010 起）：
  1. 排序基准日 = Y-1 年 12 月；股票池 = Y 年 1 月 1 日的标普 500 成分股
  2. 取 12 个月动量前 3 名（同行业最多 2 只、行业权重上限 60%）
  3. 计算该组合在 Y 年的收益、年内最大回撤、与标普 500 对比
  4. 记录持仓、权重、是否发生换仓

产物：docs/data/strategy_history.json
  {years:[{year, holdings:[{ticker,name,sector,weight,ret12_at_pick,ret_year}],
           ret, dd, bench, switched}], stocks:[{ticker, years, total_ret, appear}]}
"""
import datetime as _dt

# 最后一个「已结束」的年份：进入新一年后，上一年才算完整
_today = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=8)
END_YEAR = _today.year - 1 if _today.month >= 1 else _today.year
import json
import os
import shutil
import sys

import pandas as pd

import datafeed

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "data", "sina2")
MEMBERS = os.path.join(BASE, "data", "sp500_ticker_start_end.csv")
SP500 = os.path.join(BASE, "data", "sp500.csv")
OUT = os.path.join(BASE, "docs", "data", "strategy_history.json")
CURVE = os.path.join(BASE, "docs", "data", "curve.json")

FINAL_N = 3
MIN_PRICE = 5.0
SECTOR_CAP = 0.60


def load_bars(t):
    import datafeed
    return datafeed.load_bars(t)


def ret_12m(bars, ym):
    """返回 (12个月收益, 是否合格)"""
    i = int(ym[:4]) * 12 + int(ym[5:7])
    base = f"{i//12:04d}-{i%12 or 12:02d}"
    # 精确算：ym 往前 12 个月
    y, m = int(ym[:4]), int(ym[5:7])
    y2, m2 = (y - 1, m)
    base = f"{y2:04d}-{m2:02d}"
    seq = []
    yy, mm = y2, m2
    for _ in range(13):
        seq.append(f"{yy:04d}-{mm:02d}")
        mm += 1
        if mm > 12:
            mm = 1
            yy += 1
    if any(s not in bars for s in seq):
        return None, False
    adj0 = bars[base][0]
    adj1 = bars[ym][0]
    if not adj0 or adj0 <= 0 or not adj1:
        return None, False
    prev = adj0
    for s in seq:
        cur = bars[s][0]
        # 单月超过 3 倍必然有问题（真实拆股要么已复权，要么就是漏检）
        if prev > 0 and (cur / prev > 3 or cur / prev < 1 / 3):
            return None, False
        prev = cur
    r = adj1 / adj0 - 1
    if r > 10:            # 12 个月涨 10 倍以上，视为数据异常
        return None, False
    return r, True


def picks_from_frozen(y, tickers, sector):
    """用冻结的代码列表重建 picks（仍按当时数据算 12 个月涨幅用于展示）"""
    rank_ym = f"{y-1}-12"
    picks = []
    for t in tickers:
        bars = load_bars(t)
        r = None
        if bars and rank_ym in bars:
            rr, ok = ret_12m(bars, rank_ym)
            if ok:
                r = rr
        picks.append({"ticker": t, "ret12": r if r is not None else 0.0,
                      "sector": sector.get(t, "Unknown")})
    return assign_weights(picks)


MONTHS = []
SPYB = []


def ym_add(ym, k):
    i = int(ym[:4]) * 12 + int(ym[5:7]) + k
    yy, mm = divmod(i - 1, 12)
    return f"{yy:04d}-{mm+1:02d}"


def assign_weights(picks):
    """按行业上限分配权重（同行业 2 只时该行业合计 60%，否则等权）"""
    if not picks:
        return picks
    secs = [p["sector"] for p in picks]
    dup = [s for s, c in pd.Series(secs).value_counts().items() if c >= 2 and len(picks) > 2]
    if dup:
        cap, rest = SECTOR_CAP / 2, 1 - SECTOR_CAP
        w = [cap if p["sector"] in dup else rest / max(1, len(picks) - 2) for p in picks]
        tot = sum(w)
        w = [x / tot for x in w]
    else:
        w = [1 / len(picks)] * len(picks)
    for p, wi in zip(picks, w):
        p["weight"] = round(wi, 4)
    return picks


def pick_for_year(y, members, sector):
    cand = []
    rank_ym = f"{y-1}-12"
    for t in members:
        bars = load_bars(t)
        if not bars or rank_ym not in bars:
            continue
        price = bars[rank_ym][1]
        if price < MIN_PRICE:
            continue
        r, ok = ret_12m(bars, rank_ym)
        if not ok:
            continue
        cand.append((t, r, price))
    cand.sort(key=lambda x: -x[1])

    picks, sec_cnt = [], {}
    for t, r, px in cand:
        if len(picks) >= FINAL_N:
            break
        s = sector.get(t, "Unknown")
        if sec_cnt.get(s, 0) >= 2:
            continue
        picks.append({"ticker": t, "ret12": r, "sector": s})
        sec_cnt[s] = sec_cnt.get(s, 0) + 1

    if not picks:
        return []
    return assign_weights(picks)


def year_perf(y, picks):
    """组合在 Y 年的收益与年内最大回撤（月末）"""
    seq = [f"{y}-{m:02d}" for m in range(1, 13)]
    prev_ym = f"{y-1}-12"
    curves, ok = [], True
    for p in picks:
        bars = load_bars(p["ticker"])
        if not bars or prev_ym not in bars:
            ok = False
            break
        p["_bars"] = bars

    if not ok:
        return None, None, None, None

    vals = []
    for m in seq:
        v = 0.0
        for p in picks:
            bars = p["_bars"]
            if m not in bars:
                # 用上一个有效月
                cand_m = [k for k in sorted(bars) if k <= m]
                if not cand_m:
                    v = None
                    break
                px = bars[cand_m[-1]][0]
            else:
                px = bars[m][0]
            base = bars[prev_ym][0]
            if not base or base <= 0:
                v = None
                break
            v += p["weight"] * (px / base)
        if v is None:
            return None, None, None, None
        vals.append(v)

    ret = vals[-1] - 1
    peak, mdd = 0, 0
    for v in [1.0] + vals:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)

    # 月度收益序列（曲线与分年共用同一份计算）
    months, prev_v = [], 1.0
    for i, v in enumerate(vals):
        months.append({"ym": seq[i], "p": round(v / prev_v - 1, 6)})
        prev_v = v

    # 每只股票在 Y 年的收益
    for p in picks:
        bars = p.pop("_bars", None)
        if bars and prev_ym in bars and f"{y}-12" in bars:
            p["ret_year"] = round(bars[f"{y}-12"][0] / bars[prev_ym][0] - 1, 4)
        else:
            p["ret_year"] = None
    return round(ret, 4), round(mdd, 4), True, months


def _write_error(msg):
    try:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump({"years": [], "stocks": [], "error": msg}, f, ensure_ascii=False)
    except Exception:
        pass


def locked_ok():
    """历史是否已锁定且自洽：锁定 = 不再重算、不再拉历史数据

    校验通过才锁定；任何一项不过就返回 False（走重算+人工确认流程）。
    校验内容：
      1. output/strategy_history.json 与 output/curve.json 都存在
      2. 逐年收益全部是有限数
      3. 曲线末值 == 逐年复利之积（1% 容差）
      4. 与 output/history_baseline.json 记录的历史收益一致（0.5pp 容差）
    """
    try:
        sh_p = os.path.join(BASE, "output", "strategy_history.json")
        cv_p = os.path.join(BASE, "output", "curve.json")
        if not (os.path.exists(sh_p) and os.path.exists(cv_p)):
            return False, "缺少已锁定的历史文件"
        sh = json.load(open(sh_p, encoding="utf-8"))
        cv = json.load(open(cv_p, encoding="utf-8"))
        ys = sh.get("years") or []
        if len(ys) < 8:
            return False, "历史年份不足"
        for r in ys:
            v = r.get("ret")
            if v is None or not isinstance(v, (int, float)) or v != v:
                return False, "存在无效年份收益"
        prod = 1.0
        for r in cv.get("years") or []:
            prod *= 1 + (r.get("ret") or 0)
        end = (cv.get("points") or [{}])[-1].get("s", 0)
        if not end or abs(prod / end - 1) > 0.01:
            return False, "曲线与分年不一致"
        bp = os.path.join(BASE, "output", "history_baseline.json")
        if os.path.exists(bp):
            base = json.load(open(bp, encoding="utf-8")).get("years") or {}
            drift = [y for y in base if y in {str(x["year"]): x for x in ys}
                     and abs(base[y] - {str(x["year"]): x for x in ys}[y]["ret"]) > 0.005]
            if drift:
                return False, "历史收益被改动：%s" % ",".join(drift[:5])
        return True, "校验通过"
    except Exception as e:
        return False, "校验异常：%s" % e


def publish_locked():
    """把已锁定的历史直接发布到 docs/data，不做任何重算"""
    for fn in ("strategy_history.json", "curve.json"):
        src = os.path.join(BASE, "output", fn)
        dst = os.path.join(BASE, "docs", "data", fn)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)


def missing_new_year():
    """返回尚未归档的已结束年份（年度结转用），没有则返回 None"""
    try:
        sh = json.load(open(os.path.join(BASE, "output", "strategy_history.json"),
                            encoding="utf-8"))
        have = {r.get("year") for r in (sh.get("years") or [])}
    except Exception:
        return END_YEAR
    return None if END_YEAR in have else END_YEAR


def main():
    refresh = "--refresh" in sys.argv

    # 历史已锁定 → 直接发布，秒级完成，不再拉取任何历史数据
    if not refresh:
        ok, why = locked_ok()
        new_year = missing_new_year()
        if ok and new_year is None:
            publish_locked()
            sh = json.load(open(os.path.join(BASE, "output", "strategy_history.json"),
                                encoding="utf-8"))
            m = sh.get("summary") or {}
            print(f"历史已锁定（{m.get('start')}–{m.get('end')}，"
                  f"累计 {m.get('total', 0) * 100:.0f}%，{why}）→ 直接发布，不重算")
            return 0
        if ok and new_year is not None:
            print(f"年度结转：{new_year} 已结束但未归档，执行一次重算以补入该年…",
                  file=sys.stderr)
            refresh = True
        else:
            print(f"历史未锁定（{why}），执行一次完整重算…", file=sys.stderr)

    m = datafeed.load_members()
    if m is None:
        msg = "无法获取标普500 时点成分表（static/ 与网络均不可用）"
        print(msg, file=sys.stderr)
        _write_error(msg)
        return 1

    m["start_date"] = m["start_date"].astype(str)
    m["end_date"] = m["end_date"].fillna("2099-12-31").astype(str)
    sector, name_map = datafeed.load_sector()
    if not sector:
        print("WARN: 行业表不可用，按等权处理", file=sys.stderr)

    datafeed.prefetch(sorted(m["ticker"].unique().tolist()) + ["SPY"], workers=8)
    spy = load_bars("SPY")

    # 冻结选股：同样的规则 + 同样的数据源，回测结果必须稳定可复现。
    # 已冻结的年份直接沿用，不再重算；确需重算时加 --refresh。
    frozen, frozen_years = {}, {}
    fps = os.path.join(BASE, "output", "backtest_picks.json")
    refresh = "--refresh" in sys.argv
    if os.path.exists(fps) and not refresh:
        try:
            fd = json.load(open(fps, encoding="utf-8"))
            frozen = {int(k): v for k, v in (fd.get("years") or {}).items()}
            frozen_years = fd.get("generated_years") or {}
            print(f"      选股已冻结（{len(frozen)} 年，{fd.get('generated','')}）", flush=True)
        except Exception:
            frozen = {}

    MONTHS.clear()
    SPYB.clear()
    years, prev_set = [], None
    for y in range(2010, END_YEAR + 1):
        d = f"{y}-01-01"
        members = set(m[(m.start_date <= d) & (m.end_date >= d)]["ticker"])
        if y in frozen:
            picks = picks_from_frozen(y, frozen[y], sector)
        else:
            picks = pick_for_year(y, members, sector)
        if not picks:
            continue
        ret, dd, ok, months = year_perf(y, picks)
        bench = None
        if spy and f"{y-1}-12" in spy and f"{y}-12" in spy:
            bench = round(spy[f"{y}-12"][0] / spy[f"{y-1}-12"][0] - 1, 4)
        cur_set = tuple(sorted(p["ticker"] for p in picks))
        switched = (prev_set is not None and cur_set != prev_set)
        prev_set = cur_set
        for p in picks:
            p["name"] = str(name_map.get(p["ticker"], p["ticker"]))[:32]
            p["ret12"] = round(p["ret12"], 4)
        years.append({"year": y, "holdings": picks, "ret": ret, "dd": dd,
                      "bench": bench, "switched": switched,
                      "n_members": len(members)})
        if months:
            MONTHS.extend(months)
            for mo in months:
                ym = mo["ym"]
                pv = ym_add(ym, -1)
                if spy and ym in spy and pv in spy and spy[pv][0] > 0:
                    SPYB.append({"ym": ym, "b": round(spy[ym][0] / spy[pv][0] - 1, 6)})

    # 分股票统计
    stat = {}
    for r in years:
        for p in r["holdings"]:
            s = stat.setdefault(p["ticker"], {"ticker": p["ticker"], "name": p["name"],
                                              "years": [], "rets": [], "sector": p["sector"]})
            s["years"].append(r["year"])
            if p.get("ret_year") is not None:
                s["rets"].append(p["ret_year"])
    stocks = []
    for s in stat.values():
        tot = 1.0
        for x in s["rets"]:
            tot *= 1 + x
        stocks.append({"ticker": s["ticker"], "name": s["name"], "sector": s["sector"],
                       "years": s["years"], "n": len(s["years"]),
                       "avg_ret": round(sum(s["rets"]) / len(s["rets"]), 4) if s["rets"] else None,
                       "total_ret": round(tot - 1, 4) if s["rets"] else None})
    stocks.sort(key=lambda x: (-x["n"], -(x["total_ret"] or -9)))

    # 全周期汇总（用逐年收益复利，口径与页面逐年记录一致）
    eq = [1.0]
    for r in years:
        eq.append(eq[-1] * (1 + (r["ret"] or 0)))
    peak, mdd = 1.0, 0.0
    for v in eq[1:]:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    n = len(years)
    total = eq[-1] - 1
    cagr = (eq[-1] ** (1 / n) - 1) if n > 0 and eq[-1] > 0 else None
    wins = sum(1 for r in years if (r["ret"] or 0) > 0)
    summary = {
        "start": years[0]["year"] if years else None,
        "end": years[-1]["year"] if years else None,
        "years": n,
        "total": round(total, 4),
        "cagr": round(cagr, 4) if cagr is not None else None,
        "mdd": round(mdd, 4),
        "wins": wins,
        "losses": n - wins,
        "avg": round(sum((r["ret"] or 0) for r in years) / n, 4) if n else None,
        "best": max(((r["ret"] or 0), r["year"]) for r in years) if years else None,
        "worst": min(((r["ret"] or 0), r["year"]) for r in years) if years else None,
        "worst_intra": round(min((r["dd"] or 0) for r in years), 4) if years else None,
        "worst_intra_year": (min((r["dd"] or 0, r["year"]) for r in years)[1] if years else None),
        "note": "按年度收盘复利计算；年度最大回撤用年末净值，最深年内回撤取各年谷底",
    }

    # ---- 健康检查：数据抓不全时不要覆盖已发布的完整结果 ----
    GOOD_MONTHS = 180                      # 至少 15 年
    n_nan = sum(1 for r in years if r["ret"] is None)
    healthy = (len(MONTHS) >= GOOD_MONTHS and n_nan == 0)
    SNAP_SH = os.path.join(BASE, "output", "strategy_history.json")
    SNAP_CV = os.path.join(BASE, "output", "curve.json")

    if not healthy:
        print(f"WARN: 本次数据不完整（{len(MONTHS)} 个月，{n_nan} 个年份缺失），"
              f"沿用上次完整结果", file=sys.stderr)
        for src, dst in ((SNAP_SH, OUT), (SNAP_CV, CURVE)):
            if os.path.exists(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                print(f"      已沿用 {os.path.basename(src)}")
        if os.path.exists(OUT):
            return 0
        print("没有可沿用的历史结果，按本次输出", file=sys.stderr)

    out = {"years": years, "stocks": stocks, "summary": summary,
           "generated": (pd.Timestamp.utcnow() + pd.Timedelta(hours=8)).strftime("%Y-%m-%d")}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    # 全周期曲线（与分年同源，保证数字完全一致）
    if MONTHS:
        bmap = {x["ym"]: x["b"] for x in SPYB}
        pts, s_acc, b_acc, pk2 = [], 1.0, 1.0, 1.0
        for mo in MONTHS:
            s_acc *= 1 + mo["p"]
            b_acc *= 1 + bmap.get(mo["ym"], 0.0)
            pk2 = max(pk2, s_acc)
            pts.append({"d": mo["ym"], "s": round(s_acc, 4), "b": round(b_acc, 4),
                        "dd": round(s_acc / pk2 - 1, 4)})
        yrs = len(pts) / 12
        vol = (pd.Series([x["p"] for x in MONTHS]).std() * (12 ** 0.5)) if len(pts) > 2 else 0
        cstats = {
            "total": round(s_acc - 1, 4),
            "cagr": round(s_acc ** (1 / yrs) - 1, 4) if yrs > 0 else 0,
            "mdd": round(min(p["dd"] for p in pts), 4),
            "vol": round(float(vol), 4),
            "sharpe": round(float(pd.Series([x["p"] for x in MONTHS]).mean() * 12 / vol), 3) if vol else None,
            "bench_total": round(b_acc - 1, 4),
            "bench_cagr": round(b_acc ** (1 / yrs) - 1, 4) if yrs > 0 else 0,
            "months": len(pts), "start": pts[0]["d"], "end": pts[-1]["d"],
            "win": round(float(sum(1 for x in MONTHS if x["p"] > 0) / len(MONTHS)), 4),
        }
        cyears = []
        for r in years:
            mm = [x["p"] for x in MONTHS if x["ym"].startswith(str(r["year"]))]
            bb = [bmap.get(x["ym"], 0.0) for x in MONTHS if x["ym"].startswith(str(r["year"]))]
            if not mm:
                continue
            path, ipeak, ydd = 1.0, 1.0, 0.0
            for x in mm:
                path *= 1 + x
                ipeak = max(ipeak, path)
                ydd = min(ydd, path / ipeak - 1)
            cyears.append({"year": r["year"],
                           "ret": round(float(path - 1), 4),
                           "bench": round(float(pd.Series(bb).add(1).prod() - 1), 4),
                           "dd": round(ydd, 4)})
        with open(CURVE, "w", encoding="utf-8") as f:
            json.dump({"points": pts, "stats": cstats, "years": cyears,
                       "updated": (pd.Timestamp.utcnow() + pd.Timedelta(hours=8)).strftime("%Y-%m-%d")},
                      f, ensure_ascii=False, separators=(",", ":"))
        for _src, _dst in ((OUT, SNAP_SH), (CURVE, SNAP_CV)):
            try:
                shutil.copy2(_src, _dst)
            except Exception:
                pass
        print(f"      全周期曲线 {len(pts)} 个月：累计 {cstats['total']*100:.0f}% "
              f"年化 {cstats['cagr']*100:.1f}% 回撤 {cstats['mdd']*100:.1f}%")

    # 写入选股快照（回测的唯一事实来源，供全周期曲线复用）
    if not frozen:
        snap = {"generated": (pd.Timestamp.utcnow() + pd.Timedelta(hours=8)).strftime("%Y-%m-%d"),
                "note": "冻结的逐年持仓，回测与曲线均以此为准；重算用 --refresh",
                "years": {str(r["year"]): [p["ticker"] for p in r["holdings"]] for r in years}}
        with open(fps, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=1)
        print(f"      已冻结选股快照 → {fps}")

    print(f"逐年记录 {n} 年，涉及 {len(stocks)} 只股票")
    print(f"  全周期 累计 {total*100:.0f}%  年化 {cagr*100:.1f}%  最大回撤 {mdd*100:.1f}%  正收益 {wins}/{n} 年")
    for r in years[-6:]:
        print(f"  {r['year']} {' '.join(p['ticker'] for p in r['holdings'])} "
              f"ret={r['ret']*100 if r['ret'] is not None else float('nan'):+.1f}% "
              f"dd={r['dd']*100 if r['dd'] is not None else float('nan'):+.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
