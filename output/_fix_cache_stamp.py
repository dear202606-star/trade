# -*- coding: utf-8 -*-
"""修复：缓存新鲜度改用「数据版本戳」而不是文件 mtime。

背景（审计 P0）：output/_prices_cache.json 曾被提交入库，_cache_fresh() 用 mtime 判 TTL，
git checkout 会把 mtime 刷成检出时刻 → CI 里缓存永远有效，DB 被架空。

新做法：缓存文件内保存 stamp = (行数, 最大 fetched_at, 符号数)，
每次使用前用一条轻量查询比对；不一致就重建。
"""
import io

P = "db_prices.py"
s = io.open(P, encoding="utf-8").read()

old = '''def _cache_fresh():
    try:
        return (os.path.exists(CACHE_FILE) and
                __import__("time").time() - os.path.getmtime(CACHE_FILE) < CACHE_TTL)
    except Exception:
        return False'''

new = '''def _db_stamp():
    """数据版本戳：行数 + 最大抓取时间 + 标的数（一条轻量查询）"""
    try:
        with connect() as c, c.cursor() as cur:
            cur.execute("""select count(*), coalesce(max(fetched_at)::text,''),
                                  count(distinct symbol) from prices_monthly""")
            return cur.fetchone()
    except Exception:
        return None


def _cache_fresh():
    """只认数据版本戳，不认 mtime（mtime 会被 git checkout 刷新）"""
    try:
        if not os.path.exists(CACHE_FILE):
            return False
        import json as _json
        head = _json.load(open(CACHE_FILE, encoding="utf-8"))
        stamp = head.get("__stamp__")
        if not stamp:
            return False
        return tuple(stamp) == tuple(_db_stamp() or ())
    except Exception:
        return False'''

if old in s:
    s = s.replace(old, new, 1)

# 缓存写入时带 stamp
old2 = '''    if use_cache and prefer_basis == "total_return" and _cache_fresh():
        import json as _json
        raw = _json.load(open(CACHE_FILE, encoding="utf-8"))
        return {k: {m: (v[0], v[1]) for m, v in d.items()} for k, d in raw.items()}'''
new2 = '''    if use_cache and prefer_basis == "total_return" and _cache_fresh():
        import json as _json
        raw = _json.load(open(CACHE_FILE, encoding="utf-8"))
        raw.pop("__stamp__", None)
        return {k: {m: (v[0], v[1]) for m, v in d.items()} for k, d in raw.items()}'''
if old2 in s:
    s = s.replace(old2, new2, 1)

old3 = '''    try:
        import json as _json
        _json.dump({k: {m: [v[0], v[1]] for m, v in d.items()} for k, d in out.items()},
                   open(CACHE_FILE, "w"), separators=(",", ":"))'''
new3 = '''    try:
        import json as _json
        payload = {k: {m: [v[0], v[1]] for m, v in d.items()} for k, d in out.items()}
        payload["__stamp__"] = list(_db_stamp() or [])
        _json.dump(payload, open(CACHE_FILE, "w"), separators=(",", ":"))'''
if old3 in s:
    s = s.replace(old3, new3, 1)

io.open(P, "w", encoding="utf-8").write(s)

import ast
ast.parse(io.open(P, encoding="utf-8").read())
print("缓存判据已改为数据版本戳:", "__stamp__" in s)
