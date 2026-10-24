# -*- coding: utf-8 -*-
"""monthly_pick.py — 月度信号（GitHub Actions 专用，无需本地缓存）

严格对齐策略口径（output/排名策略对比.html）：
  1. 股票池：当前标普500 成分股
  2. 过滤：股价 >= 5 美元；过去 12 个月日均成交额 >= 2000 万美元
  3. 排序：过去 12 个自然月涨幅（adj）
  4. 持仓：前 3 名；同一 GICS 行业最多 2 只，出现 2 只时该行业权重上限 60%
  5. 换仓：**每年 12 月末锁定**，次年全年不变；其余月份只监控开关
  6. 开关：标普500 上月收盘 < 10 月均线 -> 整体仓位减半
"""
import os, sys, json, re, time, argparse, datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import pandas as pd

import market_data

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0"
PAT = re.compile(r"\(\s*(\[.*\])\s*\)", re.S)
NICE = [1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 25, 30, 40, 50, 100]
PIPELINE_VERSION = "2.1"   # 数据同源(新浪)+年度锁定+实盘跟踪
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
    return df.drop_duplicates("ym").set_index("ym").sort_index()


def ranking_at(data, months, sp, m):
    """按 as-of 月份 m 的 12 个月动量排名，返回 (前10 DataFrame, 排名池大小)"""
    if m not in months:
        return None, 0
    i = months.index(m)
    if i < 12:
        return None, 0
    base = months[i - 12]
    rows = []
    for tk, df in data.items():
        if tk == "SPY":
            continue
        if base not in df.index or m not in df.index:
            continue
        win = df.loc[base:m]
        if len(win) != 13:                              # 13 个月必须连续
            continue
        ratio = (win["adj"] / win["adj"].shift(1)).dropna()
        if len(ratio) < 12 or ratio.max() > 8 or ratio.min() < 1 / 8:
            continue                                    # 剔除代码复用/拆股异常
        px = df.at[m, "raw"]
        if px < MIN_PRICE:
            continue
        seg = win.iloc[:-1]
        dv = (seg["raw"] * seg["vol"]).mean()
        if dv < MIN_DV:
            continue
        rows.append({"ticker": tk, "ret12": df.at[m, "adj"] / df.at[base, "adj"] - 1,
                     "price": px, "dv": dv})
    if not rows:
        return None, 0
    q = pd.DataFrame(rows).merge(sp, on="ticker", how="left")
    q = q.sort_values("ret12", ascending=False).reset_index(drop=True)
    q["rank"] = q.index + 1
    return q, len(q)


def select_holdings(q):
    """前 3 名 + 行业约束，返回 [(row, weight), ...]"""
    picks, sec_cnt = [], {}
    for _, r in q.iterrows():
        if len(picks) >= FINAL_N:
            break
        s = r["sector"] if isinstance(r["sector"], str) else "Unknown"
        if sec_cnt.get(s, 0) >= 2:
            continue
        picks.append(r)
        sec_cnt[s] = sec_cnt.get(s, 0) + 1
    secs = [p["sector"] for p in picks]
    dup = [s for s, c in pd.Series(secs).value_counts().items() if c >= 2]
    if dup:
        cap, rest = SECTOR_WEIGHT_CAP / 2, 1 - SECTOR_WEIGHT_CAP
        w = [cap if p["sector"] in dup else rest / max(1, len(picks) - 2) for p in picks]
        tot = sum(w)
        w = [x / tot for x in w]
    else:
        w = [1 / len(picks)] * len(picks)
    return list(zip(picks, w))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    today = dt.date.today().isoformat()
    print("[1/5] 获取标普500 成分股 ...", flush=True)
    sp = fetch_sp500()
    tickers = sp.ticker.tolist()
    if args.limit:
        tickers = tickers[: args.limit]
    print(f"      {len(tickers)} 只", flush=True)

    print("[2/5] 拉取行情（新浪，自建拆股复权）...", flush=True)
    data, fail = {}, []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_history, t): t for t in tickers + ["SPY"]}
        for i, fu in enumerate(as_completed(futs), 1):
            t = futs[fu]
            try:
                r, _src = market_data.get_history(t)
            except Exception:
                r, _src = None, None
            if r and len(r) >= 14:
                data[t] = month_end_series(r)
            else:
                fail.append(t)
            if i % 100 == 0:
                print(f"      {i}/{len(tickers)+1}  用时 {time.time()-t0:.0f}s", flush=True)

    print(f"      成功 {len(data)-1} / 失败 {len(fail)}  |  数据源 {market_data.STATS}", flush=True)
    if "SPY" not in data:
        print("ERROR: SPY 行情获取失败", file=sys.stderr)
        sys.exit(1)
    if len(data) - 1 < 0.5 * len(tickers):
        print(f"ERROR: 行情成功率过低 ({len(data)-1}/{len(tickers)})", file=sys.stderr)
        sys.exit(1)

    print("[3/5] 计算动量排名（当月 + 上一年 12 月）...", flush=True)
    months = sorted(data["SPY"].index)
    latest = months[-1]

    q_now, pool = ranking_at(data, months, sp, latest)
    if q_now is None:
        print("ERROR: 排名结果为空", file=sys.stderr)
        sys.exit(1)

    # ---- 正式持仓：以「上一年 12 月」为基准锁定 ----
    official_month = f"{int(latest[:4]) - 1}-12"
    q_off, _ = ranking_at(data, months, sp, official_month)
    if q_off is None:                       # 数据不足时退回当月
        q_off, official_month = q_now, latest
    official = select_holdings(q_off)
    preview = select_holdings(q_now)

    # ---- 趋势开关 ----
    spy = data["SPY"]
    ma10 = spy["raw"].iloc[-10:].mean()
    spy_px = spy["raw"].iloc[-1]
    trend_on = bool(spy_px >= ma10)
    exposure = 1.0 if trend_on else 0.5

    print(f"      当月排名池 {pool} 只；正式持仓基准 {official_month}", flush=True)

    out = pd.DataFrame([{
        "名次": int(p["rank"]), "代码": p["ticker"], "公司": str(p["name"])[:40],
        "行业": p["sector"], "过去12月涨幅": round(float(p["ret12"]), 4),
        "股价": round(float(p["price"]), 2),
        "策略内权重": round(float(wi), 4),
        "占总资产(按30%仓位)": round(float(wi) * exposure * 0.30, 4),
    } for p, wi in preview])
    out.to_csv("output/current_picks.csv", index=False, encoding="utf-8-sig")
    q_now.head(50).to_csv("output/current_picks_full.csv", index=False, encoding="utf-8-sig")

    # ---- 本期该做什么 ----
    this_year = int(latest[:4])
    is_rebalance_month = latest.endswith("-12")
    acts = []
    if is_rebalance_month:
        acts.append(f"⚠️ 本月末为年度换仓日：按当月前 3 名换仓（{', '.join(p['ticker'] for p, _ in preview)}）")
    else:
        acts.append(f"本月不换仓：持有 {this_year} 年度组合（基准 {official_month}）")
    if not trend_on:
        acts.append(f"大盘跌破 10 月均线（{spy_px:,.0f} < {ma10:,.0f}），整体仓位降至 50%")
    else:
        acts.append(f"大盘在 10 月均线上方（{spy_px:,.0f} ≥ {ma10:,.0f}），维持满仓")
    action = "；".join(acts)

    print("[4/5] 生成报告 ...", flush=True)
    os.makedirs("output/monthly", exist_ok=True)
    runner = "GitHub Actions" if os.environ.get("GITHUB_ACTIONS") else "本地"
    sha = (os.environ.get("GITHUB_SHA") or "local")[:7]

    lines = [
        f"# 月度信号 · {latest}", "",
        f"> 生成时间 {today} ｜ 数据截至 {latest} ｜ 排名池 {pool} 只",
        f"> 运行环境 {runner} ｜ 代码版本 {sha}", "",
        "## 本月动作", "",
        f"**{action}**", "",
        "## 大盘开关", "",
        f"- SPY 最新收盘：**{spy_px:,.2f}** ｜ 10 月均线：**{ma10:,.2f}**",
        f"- 状态：**{'满仓' if trend_on else '半仓'}** → 目标仓位 {exposure*100:.0f}%", "",
        f"## 正式持仓（{official_month} 锁定，本年度有效）", "",
        "| # | 代码 | 公司 | 行业 | 基准涨幅 | 策略内权重 | 占总资产 |",
        "|---|---|---|---|---|---|---|",
    ]
    for i_off, (p, wi) in enumerate(official, 1):
        lines.append(f"| {i_off} | **{p['ticker']}** | {str(p['name'])[:24]} | {p['sector']} "
                     f"| +{p['ret12']*100:.0f}% | {wi*100:.0f}% | {wi*exposure*0.30*100:.1f}% |")
    lines += ["", "## 若现在重排（参考，不据此换仓）", "",
              "| # | 代码 | 行业 | 12月涨幅 |", "|---|---|---|---|"]
    for _, p in q_now.head(TOPN_LIST).iterrows():
        lines.append(f"| {int(p['rank'])} | {p['ticker']} | {p['sector']} | +{p['ret12']*100:.0f}% |")
    lines += ["", "## 纪律", "",
              "- 换仓日：每年 12 月最后一个交易日；其余月份只看开关，不动持仓",
              "- 策略总仓位 ≤ 股票资产的 30%，单只 ≤ 总资产 10%",
              "- 不设个股止损；只看大盘开关",
              "", "---", "", "由 GitHub Actions 自动生成，仅供研究，不构成投资建议。"]

    path = f"output/monthly/{latest}.md"
    for p_ in (path, "output/monthly/latest.md"):
        with open(p_, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    print("\n".join(lines[:26]))

    # ---- 结构化信号 ----
    def pack(picks):
        return [{"rank": i_, "ticker": p["ticker"], "name": str(p["name"])[:40],
                 "sector": p["sector"], "ret12": round(float(p["ret12"]), 4),
                 "weight": round(float(wi), 4)} for i_, (p, wi) in enumerate(picks, 1)]

    rec = {
        "month": latest, "date": today, "official_month": official_month,
        "trend": "满仓" if trend_on else "半仓", "exposure": round(float(exposure), 3),
        "spy": round(float(spy_px), 2), "ma": round(float(ma10), 2),
        "action": action,
        "official": pack(official), "picks": pack(preview),
        "bench": [{"rank": int(r["rank"]), "ticker": r["ticker"], "sector": r["sector"],
                   "ret12": round(float(r["ret12"]), 4)} for _, r in q_now.head(TOPN_LIST).iterrows()],
    }

    hist_path = os.path.join("output", "signal_history.json")
    hist = []
    if os.path.exists(hist_path):
        try:
            hist = json.load(open(hist_path, encoding="utf-8"))
        except Exception:
            hist = []
    prev = next((h for h in hist if h.get("month") != latest), hist[0] if hist else None)
    if prev is None:
        rec["change"] = "首次生成"
    else:
        msgs = []
        if prev.get("trend") != rec["trend"]:
            msgs.append(f"大盘开关 {prev.get('trend')} → {rec['trend']}")
        if prev.get("official_month") != rec["official_month"]:
            msgs.append(f"年度换仓：{rec['official_month']} 新组合生效")
        rec["change"] = "；".join(msgs) if msgs else "无变化"

    # ---- 实盘跟踪账本 ----
    try:
        import track_record
        prices = {tk: float(df.at[latest, "raw"]) for tk, df in data.items()
                  if tk != "SPY" and latest in df.index}
        tr = track_record.update(official, prices, float(spy_px), latest, today)
        rec["track"] = tr.get("summary", {})
        rec["track_records"] = tr.get("records", [])[-24:]
        print("      跟踪: " + json.dumps(rec["track"], ensure_ascii=False))
    except Exception as e:
        print(f"WARN: 跟踪账本失败 {e}", file=sys.stderr)

    hist = [h for h in hist if h.get("month") != latest]
    hist.insert(0, rec)
    hist.sort(key=lambda x: x.get("month", ""), reverse=True)
    with open(hist_path, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)

    print("[5/5] 更新看板 ...", flush=True)
    try:
        import dashboard
        dashboard.build(hist)
        print("      看板: output/dashboard.html")
    except Exception as e:
        print(f"WARN: 看板生成失败 {e}", file=sys.stderr)

    if len(fail) > 0.3 * len(tickers):
        print(f"WARN: 失败率过高 {len(fail)}/{len(tickers)}", file=sys.stderr)


if __name__ == "__main__":
    main()
