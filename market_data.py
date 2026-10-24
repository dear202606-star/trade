# -*- coding: utf-8 -*-
"""market_data.py — 行情数据层：多源获取 + 口径归一 + 交叉核对

设计原则
--------
1. **口径唯一**：全部只用「拆股复权」，不用分红复权。
   Yahoo 的 adjclose 含分红，会系统性抬高分红股的收益，必须用
   close + 官方拆股事件表重建复权序列，才与新浪口径一致。
2. **符号归一**：新浪 BRK.B ↔ Yahoo BRK-B。
3. **交叉核对**：同一只票两个源都取，比较 12 个月收益；
   差异超过阈值就"隔离"该票，绝不静默采用其中一个。
4. **可审计**：每次运行输出数据质量统计（各源条数、隔离名单）。

返回统一结构：[[ym, adj_close, raw_close, month_volume], ...]
"""
import json
import re
import time
import datetime as dt

import requests
import pandas as pd

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
PAT = re.compile(r"\(\s*(\[.*\])\s*\)", re.S)
NICE = [1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 25, 30, 40, 50, 100]

TOL_RET = 0.015          # 12 个月收益允许偏差（绝对）
TOL_PX = 0.02            # 同月末收盘价允许偏差（相对）

STATS = {"sina": 0, "yahoo": 0, "both": 0, "quarantine": 0, "fail": 0}
QUARANTINE = {}          # ticker -> 原因


def reset_stats():
    for k in STATS:
        STATS[k] = 0
    QUARANTINE.clear()


def _nice(k, tol=0.04):
    best = min(NICE, key=lambda z: abs(z - k))
    return best if abs(best - k) / k <= tol else None


def to_yahoo(tk):
    return tk.replace(".", "-")


def to_sina(tk):
    return tk.replace("-", ".")


# ---------------------------------------------------------------- Yahoo
def fetch_yahoo(tk, years=26):
    """只用 close + 官方拆股事件重建复权，得到与新浪同口径的序列"""
    sym = to_yahoo(tk)
    now = int(time.time())
    start = now - int(years * 365.25 * 86400)
    url = (f"https://query2.finance.yahoo.com/v8/finance/chart/{sym}"
           f"?period1={start}&period2={now}&interval=1mo&events=split")
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=8)
        if r.status_code != 200:
            return None
        res = ((r.json().get("chart") or {}).get("result") or [])
        if not res:
            return None
        res = res[0]
        ts = res.get("timestamp") or []
        q = (res.get("indicators") or {}).get("quote") or [{}]
        q = q[0]
        close = q.get("close") or []
        vol = q.get("volume") or []
        if len(ts) < 13:
            return None

        # 拆股事件：splits 里 numerator/denominator
        splits = (((res.get("events") or {}).get("splits")) or {})
        sp = []
        for k, v in splits.items():
            try:
                sp.append((int(v.get("date") or k), float(v["numerator"]) / float(v["denominator"])))
            except Exception:
                continue
        sp.sort()

        rows = []
        for i, t in enumerate(ts):
            c = close[i] if i < len(close) else None
            if c is None or c <= 0:
                continue
            d = dt.datetime.utcfromtimestamp(t)
            # 后复权：拆股之后的价格乘以拆股比例（2:1 拆股 → 后段价格 ×2）
            # 收益 = adj(t1)/adj(t0)，跨拆股时比例自动抵消，与新浪口径一致
            f = 1.0
            for sd, ratio in sp:
                if sd <= t:
                    f *= ratio
            v = vol[i] if i < len(vol) and vol[i] is not None else 0
            rows.append([d.strftime("%Y-%m"), float(c) * f, float(c), float(v)])

        ded = {}
        for row in rows:
            ded[row[0]] = row
        return [ded[k] for k in sorted(ded)]
    except Exception:
        return None


# ---------------------------------------------------------------- Sina
def fetch_sina(tk):
    sym = to_sina(tk)
    url = ("https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/x/"
           f"US_MinKService.getDailyK?symbol={sym}")
    try:
        r = requests.get(url, headers={"User-Agent": UA,
                                       "Referer": "https://finance.sina.com.cn"}, timeout=10)
        m = PAT.search(r.text)
        if not m:
            return None
        arr = json.loads(m.group(1))
        if not arr:
            return None
    except Exception:
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
                    nk = _nice(k1 if k1 >= 1 else 1 / k1)
                    if nk:
                        K = nk if k1 >= 1 else 1.0 / nk
            if K is None:
                k2 = prev_c / c
                if k2 >= 1.45 or k2 <= 0.69:
                    nk = _nice(k2 if k2 >= 1 else 1 / k2, tol=0.025)
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


# ---------------------------------------------------------------- 校验与对齐
def validate(rows, max_age_days=10):
    if not rows or len(rows) < 13:
        return False, "月份不足"
    ym = [r[0] for r in rows]
    if len(set(ym)) != len(ym):
        return False, "月份重复"
    for r in rows[-12:]:
        if not (r[1] and r[1] > 0 and r[2] and r[2] > 0):
            return False, "价格非正"
    last = pd.Timestamp(ym[-1] + "-01") + pd.offsets.MonthEnd(0)
    if (pd.Timestamp(dt.date.today()) - last).days > max_age_days + 31:
        return False, f"数据过期 {ym[-1]}"
    tail = [r[1] for r in rows[-13:]]
    for a, b in zip(tail, tail[1:]):
        if b / a > 8 or b / a < 1 / 8:
            return False, "价格异常跳变"
    return True, "ok"


def alignment(r1, r2):
    """比较两源能否对上：共同月份数、12 个月收益差、同月末收盘价差"""
    d1 = {r[0]: r for r in r1}
    d2 = {r[0]: r for r in r2}
    common = sorted(set(d1) & set(d2))
    if len(common) < 13:
        return False, len(common), None, None
    # 同月末原始收盘价（未复权）应在同一量级
    pxs = [abs(d1[m][2] / d2[m][2] - 1) for m in common[-6:] if d2[m][2] > 0]
    px_diff = max(pxs) if pxs else None
    # 12 个月收益（复权）
    m0, m12 = common[-1], common[-13]
    try:
        r_a = d1[m0][1] / d1[m12][1] - 1
        r_b = d2[m0][1] / d2[m12][1] - 1
        ret_diff = abs(r_a - r_b)
    except Exception:
        return False, len(common), px_diff, None
    ok = (ret_diff <= TOL_RET) and (px_diff is None or px_diff <= TOL_PX)
    return ok, len(common), px_diff, ret_diff


def get_history(tk, dual=True, yahoo_ok=None):
    """取一只票的历史。dual=True 时两源都取并核对。"""
    a = fetch_sina(tk)
    ok_a, _ = validate(a) if a else (False, "")
    b = None
    if dual and (yahoo_ok if yahoo_ok is not None else True):
        b = fetch_yahoo(tk)
        ok_b, _ = validate(b) if b else (False, "")
        if not ok_b:
            b = None

    if a and ok_a and b:
        ok, ncom, pxd, retd = alignment(a, b)
        STATS["both"] += 1
        if ok:
            STATS["sina"] += 1
            return a, "sina+yahoo"          # 两源一致，用新浪（与回测同源）
        QUARANTINE[tk] = (f"两源 12 月收益差 {retd*100:.1f}%"
                          if retd is not None else "两源无法对齐")
        STATS["quarantine"] += 1
        return None, "quarantine"

    if a and ok_a:
        STATS["sina"] += 1
        return a, "sina"
    if b:
        ok_b, _ = validate(b)
        if ok_b:
            STATS["yahoo"] += 1
            return b, "yahoo"
    STATS["fail"] += 1
    return None, None


def probe_yahoo():
    try:
        return fetch_yahoo("AAPL") is not None
    except Exception:
        return False
