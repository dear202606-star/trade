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
import os
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
TOL_MONTH = 0.02         # 单月收益允许偏差（绝对）

SPLITS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "output", "splits.json")
_SPLIT_CACHE = {}


def load_splits():
    """启动时载入已缓存的拆股表（仓库内 output/splits.json）"""
    global _SPLIT_CACHE
    if _SPLIT_CACHE:
        return _SPLIT_CACHE
    try:
        with open(SPLITS_FILE, encoding="utf-8") as f:
            _SPLIT_CACHE = json.load(f)
    except Exception:
        _SPLIT_CACHE = {}
    return _SPLIT_CACHE


def save_splits():
    """合并写入：保留人工维护的除权记录，避免被自动检测结果覆盖

    splits.json 是权威公司行为表（含分拆/换股等自动检测不到的），
    这里只追加本次检测到的新条目，绝不整体覆盖。
    """
    try:
        os.makedirs(os.path.dirname(SPLITS_FILE), exist_ok=True)
        cur = {}
        if os.path.exists(SPLITS_FILE):
            try:
                cur = json.load(open(SPLITS_FILE, encoding="utf-8")) or {}
            except Exception:
                cur = {}
        for t, lst in (_SPLIT_CACHE or {}).items():
            have = cur.setdefault(t, [])
            for item in lst:
                if not any(abs(item[0] - x[0]) < 86400 for x in have):
                    have.append(item)
            have.sort()
        with open(SPLITS_FILE, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        pass


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
            # 注意：Yahoo chart API 的 close 已经是「按拆股前复权」的连续序列。
            # 实测 KLAC（2026-06-12 拆 10:1）：2026-05 的 close 显示 192 而非 1920。
            # 所以这里不能再乘拆股因子，否则等于复权两次。
            v = vol[i] if i < len(vol) and vol[i] is not None else 0
            rows.append([d.strftime("%Y-%m"), float(c), float(c), float(v)])

        ded = {}
        for row in rows:
            ded[row[0]] = row
        return [ded[k] for k in sorted(ded)]
    except Exception:
        return None


def fetch_splits(tk, years=26):
    """取权威拆股表 [(拆股日, 比例)]，优先用缓存，保证跨环境一致"""
    cache = load_splits()
    if tk in cache:
        return [tuple(x) for x in cache[tk]] if cache[tk] else []
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
        splits = (((res[0].get("events") or {}).get("splits")) or {})
        out = []
        for k, v in splits.items():
            try:
                out.append((int(v.get("date") or k),
                            float(v["numerator"]) / float(v["denominator"])))
            except Exception:
                continue
        out.sort()
        cache[tk] = [[a, b] for a, b in out]
        return out
    except Exception:
        return None


def apply_split_table(rows, splits):
    """用权威拆股表把原始价重算成后复权序列（拆股后的价格 × 比例）"""
    if not splits:
        return rows
    out = []
    for ym, _adj, raw, vol in rows:
        me = pd.Timestamp(ym + "-01") + pd.offsets.MonthEnd(0)
        me_ts = int(me.timestamp()) + 86399
        f = 1.0
        for sd, ratio in splits:
            if sd <= me_ts:
                f *= ratio
        out.append([ym, raw * f, raw, vol])
    return out


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
    """比较两源能否对上：只看「复权后」的月度收益序列，不看绝对价格

    两源绝对价可能相差一个常数（前复权 vs 后复权），但月度收益必须一致。
    """
    d1 = {r[0]: r for r in r1}
    d2 = {r[0]: r for r in r2}
    common = sorted(set(d1) & set(d2))
    if len(common) < 13:
        return False, len(common), None, None
    k = min(13, len(common))
    months = common[-k:]
    try:
        ra = [d1[months[t]][1] / d1[months[t - 1]][1] - 1 for t in range(1, k)]
        rb = [d2[months[t]][1] / d2[months[t - 1]][1] - 1 for t in range(1, k)]
    except Exception:
        return False, len(common), None, None
    diffs = [abs(x - y) for x, y in zip(ra, rb)]
    max_month_diff = max(diffs) if diffs else None
    ret_diff = abs((d1[months[-1]][1] / d1[months[0]][1] - 1)
                   - (d2[months[-1]][1] / d2[months[0]][1] - 1))
    ok = (ret_diff <= TOL_RET) and (max_month_diff is None or max_month_diff <= TOL_MONTH)
    return ok, len(common), max_month_diff, ret_diff


def get_history(tk, dual=True, yahoo_ok=None):
    """取一只票的历史

    新浪原始价 × Yahoo 权威拆股表 = 后复权序列；
    Yahoo 的 close 本身已是前复权序列，两者只差一个常数，收益应完全一致。
    """
    a = fetch_sina(tk)
    if a:
        sp = fetch_splits(tk) if (dual or yahoo_ok) else None
        if sp:
            a = apply_split_table(a, sp)
    ok_a = validate(a)[0] if a else False

    b = None
    if dual or yahoo_ok:
        b = fetch_yahoo(tk)
        if b and not validate(b)[0]:
            b = None

    if a and ok_a and b:
        ok, ncom, mdiff, retdiff = alignment(a, b)
        STATS["both"] += 1
        if ok:
            STATS["sina"] += 1
            return a, "sina+yahoo"
        QUARANTINE[tk] = (f"两源月收益最大差 {mdiff*100:.2f}%"
                          if mdiff is not None else "两源无法对齐")
        STATS["quarantine"] += 1
        return None, "quarantine"

    if a and ok_a:
        STATS["sina"] += 1
        return a, "sina"
    if b and validate(b)[0]:
        STATS["yahoo"] += 1
        return b, "yahoo"
    STATS["fail"] += 1
    return None, None


def probe_yahoo():
    try:
        return fetch_yahoo("AAPL") is not None
    except Exception:
        return False
