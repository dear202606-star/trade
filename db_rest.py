# -*- coding: utf-8 -*-
"""db_rest.py — 用 Supabase REST（publishable key）只读访问数据

为什么需要
  CI 里没有 SUPABASE_DB_PASSWORD（PAT 无 secrets 权限，需人工配置），
  variant_b 退回旧文件 → 页面数字来自陈旧快照，属「假绿」。
  但 `anon` 角色的 **SELECT 权限是保留的**（只撤销了写权限），
  而 publishable key 本来就在 docs/cloud.json 里随站点公开。
  → 读路径可以直接走 REST，不依赖任何 secret。

限制
  · 仅读；写仍需数据库口令（采集在本地/带 secret 的环境跑）
  · REST 分页上限 1000 行/次，需按 Range 翻页
"""
import io
import json
import os
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
CANDIDATES = [os.path.join(BASE, "static", "cloud.json"),
              os.path.join(BASE, "docs", "data", "cloud.json")]
PAGE = 1000
UA = {"User-Agent": "trade-pipeline/1.0"}


def cfg():
    for p in CANDIDATES:
        if not os.path.exists(p):
            continue
        try:
            d = json.load(open(p, encoding="utf-8"))
            if d.get("url") and d.get("key"):
                return d["url"].rstrip("/"), d["key"]
        except Exception:
            continue
    return None


def get(table, params, page=PAGE, max_pages=400):
    """REST 分页读取；params 形如 {'select': 'a,b', 'month': 'gte.2008-01'}"""
    c = cfg()
    if not c:
        raise RuntimeError("缺少 docs/data/cloud.json（publishable key）")
    url, key = c
    q = urllib.parse.urlencode(params, safe=",")
    out = []
    for i in range(max_pages):
        req = urllib.request.Request("%s/rest/v1/%s?%s" % (url, table, q), headers={
            **UA, "apikey": key, "Authorization": "Bearer " + key,
            "Range": "%d-%d" % (i * page, (i + 1) * page - 1),
            "Range-Unit": "items",
            "Prefer": "count=none",
        })
        rows = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    rows = json.loads(r.read().decode())
                break
            except Exception as e:
                if attempt == 3:
                    raise
                __import__("time").sleep(3 * (attempt + 1))
        if not rows:
            break
        out += rows
        if len(rows) < page:
            break
    return out


def load_selected():
    """读 series_selected → {symbol: {month: (adj, raw)}}"""
    rows = get("series_selected", {
        "select": "symbol,month,adj_close,raw_close",
        "order": "symbol,month",
    })
    out = {}
    for r in rows:
        adj = r.get("adj_close")
        if adj is None:
            continue
        raw = r.get("raw_close") or adj
        out.setdefault(r["symbol"], {})[r["month"]] = (float(adj), float(raw))
    return out


def probe():
    c = cfg()
    print("cloud.json:", "有" if c else "缺")
    if not c:
        return False
    rows = get("series_selected", {"select": "symbol,month,adj_close", "limit": "3"})
    print("REST 读取样例:", rows[:2])
    return bool(rows)


if __name__ == "__main__":
    probe()
