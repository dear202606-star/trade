# -*- coding: utf-8 -*-
"""crosscheck.py — 数据源交叉核对：同一套规则，两个源是否给出同一答案

产物：
  output/data_quality.md    人读报告
  output/data_quality.json  机读明细
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

import market_data as md

BASE = os.path.dirname(os.path.abspath(__file__))


def main(limit=None, workers=8):
    md.reset_stats()
    sp = pd.read_csv(os.path.join(BASE, "data", "sp500.csv")) if os.path.exists(
        os.path.join(BASE, "data", "sp500.csv")) else None
    if sp is None:
        import requests
        from io import StringIO
        r = requests.get("https://raw.githubusercontent.com/fja05680/sp500/master/sp500.csv",
                         headers={"User-Agent": md.UA}, timeout=30)
        sp = pd.read_csv(StringIO(r.text))
    tickers = [t.strip() for t in sp["Symbol"].astype(str)]
    if limit:
        tickers = tickers[:limit]

    yahoo_ok = md.probe_yahoo()
    print(f"Yahoo 可用: {yahoo_ok}；样本 {len(tickers)} 只", flush=True)
    if not yahoo_ok:
        print("Yahoo 不可达，无法交叉核对（本地网络受限，请在 CI 运行）", file=sys.stderr)

    rows, done = [], 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(md.get_history, t, yahoo_ok) for t in tickers}
        for fu in as_completed(futs):
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(tickers)}", flush=True)

    # 重新逐只核对，收集明细（get_history 已缓存不了，这里重跑一遍代价大）
    # 改为：直接再跑一次 alignment，仅统计
    detail = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        def check(t):
            a = md.fetch_sina(t)
            b = md.fetch_yahoo(t) if yahoo_ok else None
            if not a or not b:
                return None
            ok_a, _ = md.validate(a)
            ok_b, _ = md.validate(b)
            if not (ok_a and ok_b):
                return None
            ok, ncom, mdiff, retd = md.alignment(a, b)
            return {"ticker": t, "ok": ok, "common_months": ncom,
                    "month_diff": mdiff, "ret_diff": retd}
        futs = {ex.submit(check, t): t for t in tickers}
        for fu in as_completed(futs):
            try:
                d = fu.result()
            except Exception:
                d = None
            if d:
                detail.append(d)

    md.save_splits()
    df = pd.DataFrame(detail)
    n = len(df)
    bad = df[~df.ok] if n else df
    s = md.STATS

    lines = [
        "# 数据质量核对报告", "",
        f"- 样本股票：{len(tickers)} 只",
        f"- 两源均可取到：**{n}** 只",
        f"- 两源一致：**{int(df.ok.sum()) if n else 0}** 只",
        f"- 两源不一致（已隔离，不参与排名）：**{len(bad)}** 只",
        "",
        "## 判定标准", "",
        f"- 12 个月累计收益偏差 ≤ {md.TOL_RET*100:.1f}%",
        f"- 任一月度收益偏差 ≤ {md.TOL_MONTH*100:.1f}%",
        "- 复权口径：新浪原始价 × Yahoo 权威拆股表（后复权）；"
        "Yahoo close 本身已是前复权，两者只差常数，收益必须一致",
        "",
        "## 运行统计", "",
        f"- 新浪成功：{s['sina']}",
        f"- 仅 Yahoo 兜底：{s['yahoo']}",
        f"- 两源核对：{s['both']}",
        f"- 隔离：{s['quarantine']}",
        f"- 失败：{s['fail']}",
        "",
    ]
    if len(bad):
        lines += ["## 不一致明细", "",
                  "| 代码 | 共同月份 | 单月最大差 | 12月累计差 |", "|---|---|---|---|"]
        for _, r in bad.sort_values("ret_diff", ascending=False).head(40).iterrows():
            px = f"{r['month_diff']*100:.2f}%" if pd.notna(r["month_diff"]) else "—"
            rd = f"{r['ret_diff']*100:.2f}%" if pd.notna(r["ret_diff"]) else "—"
            lines.append(f"| {r['ticker']} | {r['common_months']} | {px} | {rd} |")
    else:
        lines.append("两源全部一致 ✅" if n else "无法核对（Yahoo 不可达）")

    with open(os.path.join(BASE, "output", "data_quality.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open(os.path.join(BASE, "output", "data_quality.json"), "w", encoding="utf-8") as f:
        json.dump({"stats": s, "total": n, "bad": len(bad),
                   "rows": detail, "quarantine": md.QUARANTINE},
                  f, ensure_ascii=False, indent=2)

    print("\n".join(lines[:14]))
    print(f"\n已写入 output/data_quality.md / .json")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else None
    main(limit=n)
