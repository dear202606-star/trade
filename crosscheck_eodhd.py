# -*- coding: utf-8 -*-
"""crosscheck_eodhd.py — 用 EODHD（第二数据源）交叉校验最近 12 个月

为什么只用近 12 个月：EODHD 免费额度只提供最近一年，历史由 Alpha Vantage 回填。
但「当年收益 / 当年持仓 / 次年预览」都基于最近一年，正是最该被校验的部分。

校验对象：当前持仓 + 次年预览前 5 名（约 8 只，远低于每日额度）
判定：同一月份收益，两源偏差 > 2% 记为异常，写入 output/eodhd_check.json
"""
import json
import os
import sys
import time
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
UNI = os.path.join(BASE, "static", "prices_unified")
KEY_FILE = os.path.join(BASE, "output", "_api_keys.local.txt")
OUT = os.path.join(BASE, "output", "eodhd_check.json")
TOL = 0.02


def token():
    t = os.environ.get("EODHD_API_TOKEN")
    if t:
        return t.strip()
    if os.path.exists(KEY_FILE):
        for line in open(KEY_FILE, encoding="utf-8"):
            if line.startswith("eodhd_token="):
                return line.split("=", 1)[1].strip()
    return ""


def eod_monthly(sym, tok):
    u = ("https://eodhd.com/api/eod/%s.US?api_token=%s&fmt=json&period=m&order=a"
         % (sym.replace(".", "-"), tok))
    raw = urllib.request.urlopen(
        urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=40).read()
    d = json.loads(raw.decode())
    return {x["date"][:7]: x["adjusted_close"] for x in d if x.get("adjusted_close")}


def local_monthly(sym):
    p = os.path.join(UNI, sym + ".json")
    if not os.path.exists(p):
        return {}
    d = json.load(open(p, encoding="utf-8"))
    return {r[0]: r[1] for r in d["bars"]}


def apply_patch(sym, ext, loc):
    """用 EODHD 覆盖最近 13 个月，并把更早的历史按接缝比例缩放，保证连续"""
    common = sorted(set(ext) & set(loc))
    if not common:
        return False
    seam = common[0]
    scale = ext[seam] / loc[seam] if loc[seam] else 1.0
    p = os.path.join(UNI, sym + ".json")
    d = json.load(open(p, encoding="utf-8"))
    bars = []
    for ym, px in d["bars"]:
        if ym >= seam:
            bars.append([ym, round(ext.get(ym, px * scale), 4)])
        else:
            bars.append([ym, round(px * scale, 4)])
    d["bars"] = bars
    d["source"] = "sina_repaired+eodhd"
    d["quality"] = "eodhd_patched"
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    return True


def main():
    APPLY = "--apply" in sys.argv
    tok = token()
    if not tok:
        print("缺少 EODHD token（环境变量 EODHD_API_TOKEN 或 output/_api_keys.local.txt）")
        return 2

    targets = []
    sig = os.path.join(BASE, "docs", "data", "b", "signal.json")
    pv = os.path.join(BASE, "docs", "data", "b", "preview.json")
    if os.path.exists(sig):
        targets += [p["ticker"] for p in json.load(open(sig, encoding="utf-8")).get("official") or []]
    if os.path.exists(pv):
        targets += [p["ticker"] for p in (json.load(open(pv, encoding="utf-8")).get("top") or [])[:5]]
    targets = list(dict.fromkeys(targets))[:10]

    results, bad = [], []
    for t in targets:
        try:
            ext = eod_monthly(t, tok)
        except Exception as e:
            results.append({"ticker": t, "error": str(e)[:80]})
            print("  ! %-5s 取数失败：%s" % (t, str(e)[:60]))
            continue
        loc = local_monthly(t)
        common = sorted(set(ext) & set(loc))
        if len(common) < 3:
            results.append({"ticker": t, "note": "重叠月份不足"})
            continue
        devs = []
        for i in range(1, len(common)):
            a0, a1 = ext[common[i - 1]], ext[common[i]]
            b0, b1 = loc[common[i - 1]], loc[common[i]]
            if a0 and b0:
                devs.append(abs(a1 / a0 - b1 / b0))
        mx = max(devs) if devs else 0
        rec = {"ticker": t, "months": len(common), "max_dev": round(mx, 4)}
        results.append(rec)
        flag = "✅" if mx <= TOL else "❌"
        print("  %s %-5s 重叠 %d 月  单月收益最大偏差 %.2f%%" % (flag, t, len(common), mx * 100))
        if mx > TOL:
            bad.append(rec)
            if APPLY:
                try:
                    if apply_patch(t, ext, loc):
                        print("      ↳ 已用 EODHD 覆盖最近 %d 个月并重接历史" % len(common))
                except Exception as e:
                    print("      ↳ 修补失败：%s" % str(e)[:60])
        time.sleep(0.4)

    summary = {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "source": "EODHD（免费额度仅近 1 年，用于当年数据交叉校验）",
               "targets": len(targets), "mismatch": len(bad),
               "tolerance": TOL, "details": results}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("\n交叉校验：%d 只 ｜ 超差 %d 只%s" % (
        len(targets), len(bad), "" if not bad else "  ← 需排查"))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
