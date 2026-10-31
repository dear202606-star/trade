# -*- coding: utf-8 -*-
"""口径 B：排名池只保留「上一年整年都在成分股里」的股票。

对比：
  口径 A（现有）排名日时点成分  —— 含年中新纳入的票（如 LITE 2026-03 才入指）
  口径 B（本脚本）全年成分       —— 要求 start_date ≤ 上年 1/1 且 end_date ≥ 上年 12/31

产出（与 A 同结构，前端可原样渲染）：
  docs/data/b/strategy_history.json   逐年回测
  docs/data/b/curve.json              全周期曲线
  docs/data/b/signal.json             当年正式持仓
  docs/data/b/equity.json             当年净值与持仓明细
  docs/data/b/preview.json            次年预览
"""
import datetime as dt
import json
import os
import sys

import datafeed
import sys as _sys
try:                                    # Windows cp936 下避免 emoji 打印崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "docs", "data", "b")
START_YEAR = 2010
TOPN = 3
SECTOR_CAP = 0.60
MIN_PRICE = 5.0
MAX_ABS_RET12 = 5.0    # |12 个月涨幅| > 500% 视为价格异常


def _input_sha():
    """三份冻结输入的联合哈希，用于判断历史是否被静默改写"""
    import hashlib
    h = hashlib.sha256()
    for f in ("members_frozen.csv", "sector_frozen.json", "bars_monthly.json"):
        p = os.path.join(BASE, "static", f)
        if os.path.exists(p):
            with open(p, "rb") as fh:
                while True:
                    chunk = fh.read(1 << 20)
                    if not chunk:
                        break
                    h.update(chunk)
    return h.hexdigest()[:16]


def _meta():
    now = dt.datetime.now(dt.timezone.utc)
    return {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_id": os.environ.get("GITHUB_RUN_ID", "local"),
        "input_sha": _input_sha(),
        "variant": "B",
    }


def _dump(obj, path):
    """原子写：先写临时文件，再 rename"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


PRICES_DIR = os.path.join(BASE, "static", "prices")
UNIFIED_DIR = os.path.join(BASE, "static", "prices_unified")
PRICE_SOURCE = os.environ.get("PRICES_SOURCE", "unified")
MIN_COVERAGE = 0.95      # 新源覆盖不足 95% 时整体回退旧快照，避免新旧混用造成口径漂移
_new_src = {"n": 0}


def alpha_bars(ticker):
    """优先读统一数据集（本地修复 + 权威源覆盖），其次 Alpha Vantage 原始产出"""
    if PRICE_SOURCE not in ("alphavantage", "unified"):
        return None
    p = os.path.join(UNIFIED_DIR, ticker + ".json")
    if PRICE_SOURCE == "unified" and os.path.exists(p):
        try:
            d = json.load(open(p, encoding="utf-8"))
            bars = {r[0]: (r[1], r[1]) for r in d.get("bars") or []}
            if bars:
                _new_src["n"] += 1
                return bars
        except Exception:
            pass
    p = os.path.join(PRICES_DIR, ticker + ".json")
    if not os.path.exists(p):
        return None
    try:
        d = json.load(open(p, encoding="utf-8"))
        bars = {r[0]: (r[1], r[2]) for r in d.get("bars") or []}
        if bars:
            _new_src["n"] += 1
        return bars or None
    except Exception:
        return None


_FROZEN_BARS = None


def frozen_bars(ticker):
    """从 static/bars_monthly.json 取月度行情（复权价, 原始价）"""
    global _FROZEN_BARS
    if _FROZEN_BARS is None:
        p = os.path.join(BASE, "static", "bars_monthly.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
            _FROZEN_BARS = {t: {x[0]: (x[1], x[2]) for x in rows}
                            for t, rows in raw.items()}
            print("已加载冻结行情 %d 只" % len(_FROZEN_BARS), flush=True)
        else:
            _FROZEN_BARS = {}
    return _FROZEN_BARS.get(ticker)


NEED_MONTH = None      # 由 main() 设定：需要覆盖到的最新月份
_GAP_WARNED = set()


def load_bars(ticker):
    """统一入口：Alpha Vantage 复权月线 → 冻结快照（缺口回填）→ 实时"""
    a = alpha_bars(ticker)
    if a:
        return a
    b = frozen_bars(ticker)
    if not b:
        return datafeed.load_bars(ticker)
    if NEED_MONTH and max(b.keys()) < NEED_MONTH:
        live = datafeed.load_bars(ticker) or {}
        merged = dict(b)
        for k, v in live.items():
            if k >= max(b.keys()):
                merged[k] = v
        if ticker not in _GAP_WARNED:
            _GAP_WARNED.add(ticker)
        return merged
    return b


MAX_MONTH_JUMP = 1.5      # 单月 |涨跌| > 150% 视为数据断裂，该票当年不参与排名


def _series_ok(bars, start_ym, end_ym):
    if not bars:
        return False
    months = [k for k in sorted(bars) if start_ym <= k <= end_ym]
    if len(months) < 2:
        return False
    for i in range(1, len(months)):
        a = bars[months[i - 1]][0]
        b = bars[months[i]][0]
        if not a or a <= 0 or not b:
            return False
        if abs(b / a - 1) > MAX_MONTH_JUMP:
            return False
    return True


def members_full_year(m, year):
    """该年整年都在成分股里的股票集合"""
    a = "%d-01-01" % year
    b = "%d-12-31" % year
    return set(m[(m.start_date <= a) & (m.end_date >= b)]["ticker"].unique())


def ret12(bars, rank_month, base_month):
    if not bars or rank_month not in bars or base_month not in bars:
        return None
    a0 = bars[base_month][0]
    a1 = bars[rank_month][0]
    if not a0 or a0 <= 0 or not a1:
        return None
    return a1 / a0 - 1


def pick_year(m, sector, year):
    """选出「hold_year = year」的一年前三（口径 B）"""
    y0 = year - 1
    pool = members_full_year(m, y0)
    rank_m, base_m = "%d-12" % y0, "%d-12" % (y0 - 1)
    rows = []
    _dropped = []
    for t in pool:
        bars = load_bars(t)
        if not _series_ok(bars, "%d-12" % (y0 - 1), "%d-12" % y0):
            continue
        r = ret12(bars, rank_m, base_m)
        if r is None:
            continue
        if abs(r) > MAX_ABS_RET12:
            _dropped.append((t, round(r, 2)))
            continue
        raw = bars.get(rank_m, (None, None))[1]
        if not raw or raw < MIN_PRICE:
            continue
        rows.append({"ticker": t, "ret12": r,
                     "sector": sector.get(t) or "Unknown",
                     "name": t})
    rows.sort(key=lambda x: -x["ret12"])
    picks, cnt = [], {}
    for r in rows:
        if len(picks) >= TOPN:
            break
        sec = r["sector"]
        if sec == "Unknown":
            # 未分类统一成 1 个桶，最多 1 只（不再整体剔除）
            if cnt.get("__unk__", 0) >= 1:
                continue
            picks.append(r)
            cnt["__unk__"] = cnt.get("__unk__", 0) + 1
            continue
        if cnt.get(sec, 0) >= 2:
            continue
        picks.append(r)
        cnt[sec] = cnt.get(sec, 0) + 1

    dup = [s for s, c in cnt.items() if c >= 2]
    if dup:
        w = [(SECTOR_CAP / 2) if p["sector"] in dup else
             ((1 - SECTOR_CAP) / max(1, len(picks) - 2)) for p in picks]
    else:
        w = [1.0 / len(picks)] * len(picks)
    tot = sum(w) or 1
    ws = [round(x / tot, 4) for x in w]
    if ws:
        ws[-1] = round(1.0 - sum(ws[:-1]), 4)
    for p, ww in zip(picks, ws):
        p["weight"] = ww
    return picks, len(rows)


def year_return(picks, year):
    """该年持仓的收益（用月线复权，年末比年初）"""
    y0, y1 = "%d-12" % (year - 1), "%d-12" % year
    ret, covered = 0.0, 0.0
    for p in picks:
        bars = load_bars(p["ticker"])
        r = ret12(bars, y1, y0)
        if r is None:
            continue
        ret += p["weight"] * r
        covered += p["weight"]
    return (ret / covered) if covered > 0 else 0.0


def year_perf(picks, year):
    """年内收益与最大回撤（月末口径，与口径 A 一致）"""
    prev_ym = "%d-12" % (year - 1)
    seq = ["%d-%02d" % (year, m) for m in range(1, 13)]
    bars_cache = {}
    if not picks:
        raise RuntimeError("year_perf(%d)：没有持仓" % year)
    for p in picks:
        bars = load_bars(p["ticker"])
        if not bars or prev_ym not in bars or not bars[prev_ym][0]:
            raise RuntimeError("year_perf(%d)：%s 缺 %s 价格" % (year, p["ticker"], prev_ym))
        bars_cache[p["ticker"]] = bars
    vals = []
    for mm in seq:
        v = 0.0
        for p in picks:
            bars = bars_cache[p["ticker"]]
            keys = [k for k in sorted(bars) if k <= mm]
            if not keys:
                raise RuntimeError("year_perf(%d)：%s 缺 %s 价格" % (year, p["ticker"], mm))
            px = bars[keys[-1]][0]
            v += p["weight"] * (px / bars[prev_ym][0])
        vals.append(v)
    ret = vals[-1] - 1
    peak, mdd = 0.0, 0.0
    for v in [1.0] + vals:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return ret, mdd


def bench_year(year):
    """标普 500 该年收益（年末 vs 上年末）"""
    bars = load_bars("SPY")
    a, b = "%d-12" % (year - 1), "%d-12" % year
    if not bars or a not in bars or b not in bars:
        return 0.0
    p0, p1 = bars[a][0], bars[b][0]
    return (p1 / p0 - 1) if p0 else 0.0


def market_switch():
    """用冻结的 SPY 月线算 10 个月均线，返回 (spy, ma, trend)"""
    b = load_bars("SPY") or {}
    months = sorted(b)
    if len(months) < 11:
        return None, None, "满仓"
    px = b[months[-1]][1] or b[months[-1]][0]
    last10 = [b[m][1] or b[m][0] for m in months[-10:]]
    ma = sum(last10) / len(last10)
    trend = "满仓" if px >= ma else "半仓"
    return round(float(px), 2), round(float(ma), 2), trend


def main():
    os.makedirs(OUT, exist_ok=True)
    # 覆盖率闸门：新源未满 95% 前只用旧快照，避免新旧混算
    global PRICE_SOURCE
    try:
        _need = len([f for f in os.listdir(os.path.join(BASE, "static"))
                     if False]) or 0
        import csv as _csv
        with open(os.path.join(BASE, "static", "members_frozen.csv"), encoding="utf-8") as _f:
            _rows = list(_csv.DictReader(_f))
        _need = len({r["ticker"] for r in _rows if r["end_date"] >= "2009-01-01"})
        _dir = UNIFIED_DIR if os.path.isdir(UNIFIED_DIR) else PRICES_DIR
        _have = len([f for f in os.listdir(_dir) if f.endswith(".json")]) \
            if os.path.isdir(_dir) else 0
        _cov = _have / max(1, _need)
        if PRICE_SOURCE in ("alphavantage", "unified") and _cov < MIN_COVERAGE:
            print("⚠ 复权行情覆盖 %.1f%%（%d/%d）< %.0f%% → 本次仍用旧快照" % (
                _cov * 100, _have, _need, MIN_COVERAGE * 100), flush=True)
            PRICE_SOURCE = "legacy"
        else:
            print("复权行情覆盖 %.1f%%（%d/%d）" % (_cov * 100, _have, _need), flush=True)
    except Exception as _e:
        print("覆盖率检查失败：%s" % _e, flush=True)

    # 优先用冻结快照（static/），保证本地与 CI 结果完全一致
    import pandas as _pd
    _mem = os.path.join(BASE, "static", "members_frozen.csv")
    if os.path.exists(_mem):
        m = _pd.read_csv(_mem, dtype=str).fillna("2099-12-31")
    else:
        m = datafeed.load_members()
        m["start_date"] = m["start_date"].astype(str)
        m["end_date"] = m["end_date"].fillna("2099-12-31").astype(str)
    _secf = os.path.join(BASE, "static", "sector_frozen.json")
    if os.path.exists(_secf):
        sector = json.load(open(_secf, encoding="utf-8"))
    else:
        sector, _ = datafeed.load_sector()

    # 并行预热：CI 没有本地缓存，串行抓 700+ 只会很慢
    try:
        _all = sorted(set(m["ticker"].unique().tolist()))
        print("预热行情 %d 只…" % len(_all), flush=True)
        datafeed.prefetch(_all, workers=10)
    except Exception as _e:
        print("预热失败（继续）：%s" % _e)

    today_et = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)
    end_year = today_et.year - 1

    global NEED_MONTH
    NEED_MONTH = "%d-12" % end_year
    _fr = frozen_bars("SPY") or {}
    _last = max(_fr.keys()) if _fr else None
    if _last and _last < NEED_MONTH:
        print("⚠ 冻结行情仅到 %s，缺 %s → 用实时数据补齐（建议重跑 freeze_bars.py）"
              % (_last, NEED_MONTH), flush=True)   # 最后一个完整年份

    years = []
    for y in range(START_YEAR, end_year + 1):
        picks, pool_n = pick_year(m, sector, y)
        ret, mdd = year_perf(picks, y)
        bret = bench_year(y)
        years.append({
            "year": y,
            "holdings": [{"ticker": p["ticker"], "name": p["name"],
                          "sector": p["sector"], "weight": p["weight"],
                          "ret12": round(p["ret12"], 4)} for p in picks],
            "ret": round(ret, 4),
            "dd": round(mdd, 4),
            "bench": round(bret, 4),
            "pool": pool_n,
        })
        ret = ret
        print("  %d  %+7.2f%%  %s  （全年成分池 %d）" % (
            y, ret * 100, " / ".join(p["ticker"] for p in picks), pool_n))

    # 曲线（年末复利）
    eq, beq, pts = 1.0, 1.0, []
    spy = load_bars("SPY") or {}
    for r in years:
        y = r["year"]
        prev_ym = "%d-12" % (y - 1)
        # 年内月度形状（归一化用），缺数据时退化为平滑增长
        shape = []
        for mi in range(1, 13):
            ym = "%d-%02d" % (y, mi)
            v, ok_sh = 0.0, True
            for h in r["holdings"]:
                b = load_bars(h["ticker"])
                if not b or prev_ym not in b or not b[prev_ym][0]:
                    ok_sh = False
                    break
                keys = [k for k in sorted(b) if k <= ym]
                if not keys:
                    ok_sh = False
                    break
                v += h["weight"] * (b[keys[-1]][0] / b[prev_ym][0])
            shape.append(v if ok_sh else None)
        # 末月对齐到该年真实收益，前月按比例缩放（缺失则线性插值）
        target = 1 + r["ret"]
        last = shape[-1] if shape and shape[-1] else None
        if not last:
            shape = [1 + (target - 1) * (i + 1) / 12.0 for i in range(12)]
        else:
            shape = [((x / last) * target) if x else None for x in shape]
            # 填补空缺
            for i, x in enumerate(shape):
                if not x:
                    shape[i] = 1 + (target - 1) * (i + 1) / 12.0
        spy0 = spy.get(prev_ym, (None, None))[0]
        for mi, v in enumerate(shape, start=1):
            ym = "%d-%02d" % (y, mi)
            if spy0:
                skeys = [k for k in sorted(spy) if k <= ym]
                spx = spy[skeys[-1]][0] if skeys else None
                bval = beq * (spx / spy0) if spx else beq
            else:
                bval = beq
            pts.append({"d": ym, "s": round(eq * v, 6), "b": round(bval, 6)})
        eq *= target
        if spy0:
            skeys = [k for k in sorted(spy) if k <= "%d-12" % y]
            spx = spy[skeys[-1]][0] if skeys else None
            if spx:
                beq *= (spx / spy0)

    total = eq - 1
    n = len(years)
    cagr = (eq ** (1 / n) - 1) if n and eq > 0 else 0
    wins = sum(1 for r in years if r["ret"] > 0)
    peak, mdd = 1.0, 0.0
    for v in [1] + [p["s"] for p in pts]:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)

    hist = {
        "years": years,
        "summary": {"start": START_YEAR, "end": end_year, "years": n,
                    "total": round(total, 4), "cagr": round(cagr, 4),
                    "dd": round(mdd, 4), "worst_intra": round(mdd, 4),
                    "wins": wins, "losses": n - wins,
                    "benchmark": round(beq - 1, 4),
                    "note": "口径 B：上一年整年都在成分股里"},
        "variant": "B",
        "rule": "排名池 = 上一年 1/1–12/31 全程在标普 500 内的股票",
    }
    json.dump(hist, open(os.path.join(OUT, "strategy_history.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    json.dump({"points": pts, "years": years,
               "stats": {"total": round(total, 4), "cagr": round(cagr, 4),
                         "mdd": round(mdd, 4)},
               "variant": "B"},
              open(os.path.join(OUT, "curve.json"), "w", encoding="utf-8"), ensure_ascii=False)

    # 当年持仓（下一年）
    hold_year = end_year + 1
    picks, pool_n = pick_year(m, sector, hold_year)
    print("\n%d 年持仓（口径 B）：%s" % (hold_year, " / ".join(
        "%s %.0f%%" % (p["ticker"], p["weight"] * 100) for p in picks)))
    _spy, _ma, _trend = market_switch()
    sig = {
        "variant": "B",
        "month": "%s" % dt.datetime.now().strftime("%Y-%m"),
        "official_month": "%d-12" % end_year,
        "official_year": hold_year,
        "valid_from": "%d-01-01" % hold_year,
        "valid_to": "%d-12-31" % hold_year,
        "rebalance_hint": "%d-12 最后一个交易日" % hold_year,
        "trend": _trend,
        "exposure": 1.0,   # 年度口径恒满仓；trend 仅为盘中风控建议
        "spy": _spy,
        "ma": _ma,
        "date": dt.datetime.now().strftime("%Y-%m-%d"),
        "official": [{"rank": i + 1, "ticker": p["ticker"], "name": p["name"],
                      "sector": p["sector"], "ret12": round(p["ret12"], 4),
                      "weight": p["weight"]} for i, p in enumerate(picks)],
        "picks": [],
        "pool": pool_n,
        "rule": hist["rule"],
    }
    json.dump(sig, open(os.path.join(OUT, "signal.json"), "w", encoding="utf-8"),
              ensure_ascii=False)

    # ---- 次年预览（口径 B：今年以来全程在成分股里）----
    try:
        q = json.load(open(os.path.join(BASE, "docs", "data", "quotes.json"), encoding="utf-8"))
        quotes = q.get("quotes") or {}
        a = "%d-01-01" % hold_year
        today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
        live = set(m[(m.start_date <= a) & (m.end_date >= today)]["ticker"].unique())
        base_m = "%d-12" % (hold_year - 1)
        rows2 = []
        for t in live:
            qq = quotes.get(t) or {}
            cur = qq.get("p")
            bars = load_bars(t)
            if not cur or not bars or base_m not in bars:
                continue
            # 用复权价算涨幅（原始价在拆股/反向拆股前后不可比）
            months_ok = [k for k in sorted(bars) if k <= base_m]
            _b0 = bars[base_m][0]
            _last = [k for k in sorted(bars)][-1]
            _cur_adj = bars[_last][0]
            if not _b0 or _b0 <= 0 or not _cur_adj or _cur_adj < MIN_PRICE:
                continue
            rows2.append({"ticker": t, "sector": sector.get(t) or "Unknown",
                          "ytd": _cur_adj / _b0 - 1})
        rows2.sort(key=lambda x: -x["ytd"])
        picks2, cnt2 = [], {}
        for r in rows2:
            if len(picks2) >= TOPN:
                break
            s2 = r["sector"]
            if s2 == "Unknown" or cnt2.get(s2, 0) >= 2:
                continue
            picks2.append(r)
            cnt2[s2] = cnt2.get(s2, 0) + 1
        dup2 = [k for k, c in cnt2.items() if c >= 2]
        if dup2:
            w2 = [(SECTOR_CAP / 2) if p["sector"] in dup2 else
                  ((1 - SECTOR_CAP) / max(1, len(picks2) - 2)) for p in picks2]
        else:
            w2 = [1.0 / len(picks2)] * len(picks2)
        tot2 = sum(w2) or 1
        ws2 = [round(x / tot2, 4) for x in w2]
        if ws2:
            ws2[-1] = round(1.0 - sum(ws2[:-1]), 4)
        for i, (p, ww) in enumerate(zip(picks2, ws2)):
            p["weight"] = ww
            p["rank"] = i + 1
            p["ytd"] = round(p["ytd"], 4)
        _pv_last_month = None
        if picks2:
            _b0 = load_bars(picks2[0]["ticker"]) or {}
            _pv_last_month = sorted(_b0)[-1] if _b0 else None
        json.dump({"variant": "B", "as_of": _pv_last_month, "base_month": base_m,
                   "hold_year": hold_year + 1,
                   "official_month": "%d-12" % hold_year,
                   "top": picks2, "final": False, "ranked": len(rows2),
                   "universe": len(live), "session": q.get("session"),
                   "excluded_n": len(rows2) - len(picks2),
                   "note": "口径 B：今年以来全程在成分股里"},
                  open(os.path.join(OUT, "preview.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
        print("次年预览（B）：%s" % " / ".join(
            "%s %+.0f%%" % (p["ticker"], p["ytd"] * 100) for p in picks2))
    except Exception as e:
        print("✗ 预览生成失败：%s" % e, flush=True)
        return 3

    # 供 equity.py 复用（EQ_SIGNAL 指向它）
    json.dump([{"official_month": sig["official_month"],
                "official": sig["official"],
                "exposure": 1.0, "trend": sig["trend"]}],
              open("output/signal_history_b.json", "w", encoding="utf-8"),
              ensure_ascii=False)
    # ---- 统一补溯源信息 + 原子写 + 生成基线 ----
    _m = _meta()
    for _n in ("strategy_history.json", "curve.json", "signal.json", "preview.json"):
        _p = os.path.join(OUT, _n)
        if os.path.exists(_p):
            try:
                _o = json.load(open(_p, encoding="utf-8"))
                _o.update(_m)
                _dump(_o, _p)
            except Exception as _e:
                print("补 meta 失败 %s：%s" % (_n, _e))

    # ---- 历史基线：只比对，不默认写入（防止历史被静默改写）----
    _bl_p = os.path.join(BASE, "output", "baseline_b.json")
    _cur = {str(r["year"]): round(r["ret"], 6) for r in years}
    _cur_total = round(total, 6)
    _promote = os.environ.get("BASELINE_UPDATE") == "1"
    _drift = []
    if os.path.exists(_bl_p) and not _promote:
        try:
            _old = json.load(open(_bl_p, encoding="utf-8"))
            _oy = _old.get("years") or {}
            for y, v in _cur.items():
                if y in _oy and abs(_oy[y] - v) > 0.005:
                    _drift.append("%s: %.2f%% → %.2f%%" % (y, _oy[y] * 100, v * 100))
            if abs((_old.get("total") or 0) - _cur_total) > 0.02:
                _drift.append("累计: %.2f → %.2f" % (
                    _old.get("total") or 0, _cur_total))
        except Exception as _e:
            print("⚠ 基线读取失败：%s" % _e)
    if _drift:
        print("\n✗ 口径 B 历史发生漂移（与 output/baseline_b.json 不一致）：", flush=True)
        for d in _drift[:10]:
            print("   " + d, flush=True)
        print("  如确认是数据修正，请人工复核后用 BASELINE_UPDATE=1 提升基线。", flush=True)
        return 2
    if _promote or not os.path.exists(_bl_p):
        _bl = {"variant": "B", "generated_at": _m["generated_at"],
               "input_sha": _m["input_sha"], "total": _cur_total,
               "curve_end": round(pts[-1]["s"], 6) if pts else None,
               "years": _cur}
        _dump(_bl, _bl_p)
        print("基线%s：output/baseline_b.json（%d 年）" % (
            "已提升" if _promote else "已初始化", len(years)))

    try:
        _tot = len([f for f in os.listdir(PRICES_DIR) if f.endswith(".json")])
        print("行情来源：统一数据集命中 %d 次 ｜ 本地文件 %d 只" % (
            _new_src["n"], _tot), flush=True)
    except Exception:
        pass
    print("已写入:", OUT)


if __name__ == "__main__":
    sys.exit(main())
