# -*- coding: utf-8 -*-
"""monthly_pick.py — 月度自动选股（GitHub Actions 专用，无需本地缓存）

规则（与 output/排名策略对比.html 一致）：
  1. 股票池：当前标普500 成分股
  2. 过滤：股价 >= 5 美元；过去 12 个月日均成交额 >= 2000 万美元
  3. 排序：过去 12 个自然月涨幅
  4. 持仓：前 3 名，等权；同一 GICS 行业最多 2 只，超过则行业权重压到 60%
  5. 开关：标普500 上月收盘 < 10 月均线 -> 仓位减半
"""
import os, sys, json, re, time, argparse, datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import pandas as pd

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0"
PAT = re.compile(r"\(\s*(\[.*\])\s*\)", re.S)
NICE = [1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 25, 30, 40, 50, 100]
MIN_PRICE = 5.0
MIN_DV = 20e6
TOPN_LIST = 10
FINAL_N = 3
SECTOR_WEIGHT_CAP = 0.60


def nice_ratio(k, tol=0.04):
    best = min(NICE, key=lambda z: abs(z - k))
    return best if abs(best - k) / k <= tol else None


def get(url, tries=4, timeout=30):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
            if r.status_code == 200:
                return r
        except Exception as e:
            last = e
        time.sleep(1.2 + i)
    raise RuntimeError(f"GET failed {url}: {last}")


def fetch_sp500():
    r = get("https://raw.githubusercontent.com/fja05680/sp500/master/sp500.csv")
    from io import StringIO
    df = pd.read_csv(StringIO(r.text))
    df["Symbol"] = df["Symbol"].astype(str).str.strip()
    return df[["Symbol", "Security", "GICS Sector"]].rename(
        columns={"Symbol": "ticker", "Security": "name", "GICS Sector": "sector"})


def fetch_history(tk):
    """返回 [[ym, adj_close, raw_close, month_volume], ...]，自建拆股复权"""
    url = ("https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/x/"
           f"US_MinKService.getDailyK?symbol={tk}")
    for _ in range(3):
        try:
            r = requests.get(url, headers={"User-Agent": UA,
                                           "Referer": "https://finance.sina.com.cn"},
                             timeout=30)
            m = PAT.search(r.text)
            if not m:
                return None
            arr = json.loads(m.group(1))
            if not arr:
                return None
            out, factor = [], 1.0
            prev_c = prev_v = None
            cur_ym = cur_adj = cur_raw = None
            cur_v = 0.0
            for x in arr:
                try:
                    d, c = x["d"], float(x["c"])
                    v = float(x.get("v") or 0)
                    o = float(x.get("o") or 0)
                except Exception:
                    continue
                if c <= 0 or not d[:4].isdigit():
                    continue
                if prev_c and prev_c > 0:
                    K = None
                    if o > 0:
                        k1 = prev_c / o
                        if k1 >= 1.45 or k1 <= 0.69:
                            nk = nice_ratio(k1 if k1 >= 1 else 1 / k1)
                            if nk:
                                K = nk if k1 >= 1 else 1.0 / nk
                    if K is None:
                        k2 = prev_c / c
                        if k2 >= 1.45 or k2 <= 0.69:
                            nk = nice_ratio(k2 if k2 >= 1 else 1 / k2, tol=0.025)
                            if nk:
                                K = nk if k2 >= 1 else 1.0 / nk
                    if K is not None and prev_v and prev_v > 0:
                        if 0.25 <= (v / prev_v) / K <= 4.0:
                            factor *= K
                ym = d[:7]
                if ym != cur_ym:
                    if cur_ym:
                        out.append([cur_ym, cur_adj, cur_raw, cur_v])
                    cur_ym, cur_v = ym, 0.0
                cur_adj, cur_raw = c * factor, c
                cur_v += v
                prev_c, prev_v = c, v
            if cur_ym:
                out.append([cur_ym, cur_adj, cur_raw, cur_v])
            return out
        except Exception:
            time.sleep(0.8)
    return None


def month_end_series(rows):
    df = pd.DataFrame(rows, columns=["ym", "adj", "raw", "vol"])
    df = df.drop_duplicates("ym").set_index("ym").sort_index()
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    today = dt.date.today().isoformat()
    print(f"[1/4] 获取标普500 成分股 ...", flush=True)
    sp = fetch_sp500()
    tickers = sp.ticker.tolist()
    if args.limit:
        tickers = tickers[: args.limit]
    print(f"      {len(tickers)} 只", flush=True)

    print("[2/4] 拉取行情（新浪，自建拆股复权）...", flush=True)
    data, fail = {}, []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_history, t): t for t in tickers + ["SPY"]}
        for i, fu in enumerate(as_completed(futs), 1):
            t = futs[fu]
            try:
                r = fu.result()
            except Exception:
                r = None
            if r and len(r) >= 14:
                data[t] = month_end_series(r)
            else:
                fail.append(t)
            if i % 100 == 0:
                print(f"      {i}/{len(tickers)+1}  用时 {time.time()-t0:.0f}s", flush=True)

    print(f"      成功 {len(data)-1} / 失败 {len(fail)}", flush=True)
    if "SPY" not in data:
        print("ERROR: SPY 行情获取失败", file=sys.stderr)
        sys.exit(1)
    if len(data) - 1 < 0.5 * len(tickers):
        print(f"ERROR: 行情成功率过低 ({len(data)-1}/{len(tickers)})，疑似接口不可用", file=sys.stderr)
        sys.exit(1)

    print("[3/4] 计算 12 个月涨幅与流动性 ...", flush=True)
    months = sorted(data["SPY"].index)
    latest = months[-1]
    i_latest = months.index(latest)
    base_m = months[i_latest - 12] if i_latest >= 12 else None
    if base_m is None:
        print("ERROR: 历史不足 13 个月", file=sys.stderr)
        sys.exit(1)

    rows = []
    for tk, df in data.items():
        if tk == "SPY" or base_m not in df.index or latest not in df.index:
            continue
        # ---- 数据质量校验 ----
        win = df.loc[base_m:latest]
        if len(win) != 13:                      # 13 个月必须连续，防止代码复用/停牌
            continue
        ratio = (win["adj"] / win["adj"].shift(1)).dropna()
        if len(ratio) < 12 or ratio.max() > 8 or ratio.min() < 1 / 8:
            continue
        if (win["adj"] / win["raw"]).max() > 2000:   # 复权因子漂移
            continue
        r12 = df.at[latest, "adj"] / df.at[base_m, "adj"] - 1
        px = df.at[latest, "raw"]
        seg = df.loc[base_m:].iloc[:-1]
        dv = (seg["raw"] * seg["vol"]).mean() if len(seg) else 0
        rows.append({"ticker": tk, "ret12": r12, "price": px, "dv": dv})

    q = pd.DataFrame(rows).merge(sp, on="ticker", how="left")
    q = q[(q.price >= MIN_PRICE) & (q.dv >= MIN_DV)].copy()
    q = q.sort_values("ret12", ascending=False).reset_index(drop=True)
    q["rank"] = q.index + 1
    q.to_csv("output/current_picks_full.csv", index=False, encoding="utf-8-sig")

    # 趋势开关
    spy = data["SPY"]
    ma10 = spy["raw"].iloc[-10:].mean()
    spy_px = spy["raw"].iloc[-1]
    trend_on = bool(spy_px >= ma10)
    exposure = 1.0 if trend_on else 0.5

    # 选前 3，行业最多 2 只
    picks, sector_cnt = [], {}
    for _, r in q.iterrows():
        if len(picks) >= FINAL_N:
            break
        s = r["sector"] if isinstance(r["sector"], str) else "Unknown"
        if sector_cnt.get(s, 0) >= 2:
            continue
        picks.append(r)
        sector_cnt[s] = sector_cnt.get(s, 0) + 1

    # 权重：若两只同行业，该行业合计 60%
    secs = [p["sector"] for p in picks]
    dup = [s for s, c in pd.Series(secs).value_counts().items() if c >= 2]
    if dup:
        cap = SECTOR_WEIGHT_CAP / 2
        rest = 1 - SECTOR_WEIGHT_CAP
        w = []
        for p in picks:
            w.append(cap if p["sector"] in dup else rest / max(1, len(picks) - 2))
        tot = sum(w)
        w = [x / tot for x in w]
    else:
        w = [1 / len(picks)] * len(picks)

    out = pd.DataFrame([{
        "名次": int(p["rank"]), "代码": p["ticker"], "公司": str(p["name"])[:40],
        "行业": p["sector"], "过去12月涨幅": round(float(p["ret12"]), 4),
        "股价": round(float(p["price"]), 2), "建议权重": round(float(wi) * exposure, 4),
    } for p, wi in zip(picks, w)])
    out.to_csv("output/current_picks.csv", index=False, encoding="utf-8-sig")

    print("[4/4] 生成报告 ...", flush=True)
    os.makedirs("output/monthly", exist_ok=True)
    top10 = q.head(TOPN_LIST)
    lines = [
        f"# 月度选股 · {latest}", "",
        f"> 生成时间 {today} ｜ 数据截至 {latest} 月末 ｜ 股票池 {len(q)} 只（标普500 过滤后）", "",
        "## 大盘开关", "",
        f"- SPY 最新收盘：**{spy_px:,.2f}**",
        f"- 10 月均线：**{ma10:,.2f}**",
        f"- 状态：**{'满仓' if trend_on else '半仓（跌破 10 月均线）'}** → 建议仓位 {exposure*100:.0f}%", "",
        "## 本期持仓（前 3 名）", "",
        "| 排名 | 代码 | 公司 | 行业 | 12月涨幅 | 建议权重 |",
        "|---|---|---|---|---|---|",
    ]
    for p, wi in zip(picks, w):
        lines.append(f"| {int(p['rank'])} | **{p['ticker']}** | {str(p['name'])[:28]} | "
                     f"{p['sector']} | +{p['ret12']*100:.1f}% | {wi*exposure*100:.1f}% |")
    lines += ["", "## 备选池（第 4–10 名）", "",
              "| 排名 | 代码 | 行业 | 12月涨幅 |", "|---|---|---|---|"]
    for _, p in top10.iloc[len(picks):].iterrows():
        lines.append(f"| {int(p['rank'])} | {p['ticker']} | {p['sector']} | +{p['ret12']*100:.1f}% |")
    lines += ["", "## 操作提示", "",
              f"- 本次调仓：{'维持' if trend_on else '减半'}，目标仓位 {exposure*100:.0f}%",
              "- 换仓日：每年 12 月最后一个交易日（月度报告仅用于监控开关状态）",
              "- 单只不超过总资产 10%，策略总仓位不超过股票资产 30%", "",
              "---", "", "由 GitHub Actions 自动生成，仅供研究，不构成投资建议。"]

    path = f"output/monthly/{latest}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open("output/monthly/latest.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("\n" + "\n".join(lines[:40]))
    print(f"\n已写入 {path}")
    if len(fail) > 0.3 * len(tickers):
        print(f"WARN: 失败率过高 {len(fail)}/{len(tickers)}", file=sys.stderr)


if __name__ == "__main__":
    main()
