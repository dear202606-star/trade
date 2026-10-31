# -*- coding: utf-8 -*-
"""build_unified.py — 拼接一份完整、连续、高质量的历史行情

思路（不重拉历史，本地修复 + 权威源覆盖）
  1. 基础层：现有新浪月度数据（869 只，1999–2026），只有原始价
  2. 修复层：用「跳变 ≈ 拆股比例」识别公司行动，把序列拉连续
     —— 只对形似拆股比例的跳变动手（2/3/4/5/10…），避免误伤真实暴涨
  3. 权威层：Alpha Vantage 复权月线（已抓到的部分）直接覆盖并标记为验证通过
  4. 校验层：两层重叠月份的收益偏差 > 3% 的标的标记为 suspect，优先回填

产物
  static/prices_unified/<TICKER>.json   {ticker, bars:[[ym, adj]], source, quality}
  static/prices_unified_manifest.json   覆盖率/来源分布/可疑清单
"""
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
OLD = os.path.join(BASE, "static", "bars_monthly.json")
AV_DIR = os.path.join(BASE, "static", "prices")
OUT = os.path.join(BASE, "static", "prices_unified")
MANIFEST = os.path.join(BASE, "static", "prices_unified_manifest.json")

SPLIT_RATIOS = [1.5, 2, 2.5, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30]
JUMP_TH = 0.45          # 单月 |涨跌| 超过 45% 才可能是拆股
RATIO_TOL = 0.12        # 与标准拆股比例的相对容差
QUALITY_TOL = 0.03      # 与权威源单月收益偏差超过 3% 判为可疑


def looks_like_split(r):
    """跳变 r 是否接近某个拆股比例（正向或反向）"""
    if abs(r) <= JUMP_TH:
        return None
    f = 1.0 + r
    for base in SPLIT_RATIOS:
        for cand in (base, 1.0 / base):
            if abs(f - cand) <= RATIO_TOL * cand:
                return f
    return None


def repair(raw):
    """把原始价序列按拆股跳变拉连续；返回 (bars, n_action)"""
    ks = sorted(k for k in raw if raw[k])
    out, factor, n = {}, 1.0, 0
    prev = None
    for k in ks:
        p = raw[k]
        if prev and prev > 0:
            f = looks_like_split(p / prev - 1)
            if f:
                factor /= f
                n += 1
        out[k] = round(p * factor, 4)
        prev = p
    return out, n


def monthly_returns(bars):
    ks = sorted(bars)
    return {ks[i]: (bars[ks[i]] / bars[ks[i - 1]] - 1)
            for i in range(1, len(ks)) if bars[ks[i - 1]]}


def main():
    old = json.load(open(OLD, encoding="utf-8"))
    os.makedirs(OUT, exist_ok=True)

    av_files = {}
    if os.path.isdir(AV_DIR):
        for f in os.listdir(AV_DIR):
            if f.endswith(".json"):
                d = json.load(open(os.path.join(AV_DIR, f), encoding="utf-8"))
                av_files[d["ticker"]] = {r[0]: r[1] for r in d["bars"]}

    stats = {"total": 0, "av": 0, "repaired": 0, "plain": 0, "suspect": [], "actions": 0}
    for ticker, rows in old.items():
        raw = {r[0]: r[2] for r in rows if len(r) > 2 and r[2]}
        if not raw:
            continue
        bars, n_act = repair(raw)
        stats["actions"] += n_act
        source, quality = "sina_repaired", "ok"
        if ticker in av_files:
            av = av_files[ticker]
            # 重叠月份收益偏差
            ra, rr = monthly_returns(av), monthly_returns(bars)
            common = [k for k in ra if k in rr]
            dev = max((abs(ra[k] - rr[k]) for k in common), default=0)
            if dev > QUALITY_TOL:
                stats["suspect"].append({"ticker": ticker, "dev": round(dev, 4)})
                quality = "suspect"
            else:
                quality = "verified"
            bars = av                      # 权威源直接覆盖
            source = "alphavantage"
            stats["av"] += 1
        else:
            stats["repaired" if n_act else "plain"] += 1
        stats["total"] += 1
        with open(os.path.join(OUT, ticker + ".json"), "w", encoding="utf-8") as f:
            json.dump({"ticker": ticker, "source": source, "quality": quality,
                       "actions": n_act,
                       "bars": [[k, bars[k]] for k in sorted(bars)]},
                      f, ensure_ascii=False, separators=(",", ":"))

    # 全量连续性统计
    jumps = total = 0
    for f in os.listdir(OUT):
        d = json.load(open(os.path.join(OUT, f), encoding="utf-8"))
        b = {r[0]: r[1] for r in d["bars"]}
        ks = sorted(b)
        for i in range(1, len(ks)):
            if not b[ks[i - 1]]:
                continue
            total += 1
            if abs(b[ks[i]] / b[ks[i - 1]] - 1) > 1.5:
                jumps += 1

    man = {
        "generated": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
        "symbols": stats["total"],
        "by_source": {"alphavantage": stats["av"],
                      "sina_repaired": stats["repaired"],
                      "sina_plain": stats["plain"]},
        "split_actions_neutralized": stats["actions"],
        "continuity": {"months": total, "jumps_over_150pct": jumps,
                       "rate": round(jumps / max(1, total), 6)},
        "suspect": sorted(stats["suspect"], key=lambda x: -x["dev"])[:60],
        "note": "阿里层修复公司行动；Alpha Vantage 到位的标的直接覆盖为权威值；suspect 优先回填",
    }
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=2)

    print("统一数据集：%d 只" % stats["total"])
    print("  权威源 %d ｜ 修复 %d ｜ 未动 %d ｜ 中和拆股动作 %d 次" % (
        stats["av"], stats["repaired"], stats["plain"], stats["actions"]))
    print("  连续性：%d/%d 个月跳变（%.3f%%）" % (
        jumps, total, 100.0 * jumps / max(1, total)))
    print("  可疑（与权威源偏差>3%%）：%d 只" % len(stats["suspect"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
