# -*- coding: utf-8 -*-
"""让 CI 在无口令时快速失败并走 REST（原来要白等 10 分钟）。"""
import io

# 1) db_select：首个分片失败就抛错，不再逐块重试
p = "db_select.py"
s = io.open(p, encoding="utf-8").read()
old = '''    out = {}
    for lo, hi in (chunks or RANGES):'''
new = '''    out = {}
    attempted = 0
    for lo, hi in (chunks or RANGES):'''
if old in s:
    s = s.replace(old, new, 1)

old2 = '''            except Exception as e:
                print("  %s..%s 重试 %d: %s" % (lo, hi, attempt + 1, str(e)[:50]), flush=True)
                _t.sleep(3)'''
new2 = '''            except Exception as e:
                print("  %s..%s 重试 %d: %s" % (lo, hi, attempt + 1, str(e)[:50]), flush=True)
                attempted += 1
                if attempted >= 1:
                    # 首块就连不上，说明口令/网络不可用，继续重试只是白等
                    raise RuntimeError("数据库不可达（首个分片即失败）：%s" % str(e)[:80])
                _t.sleep(3)'''
if old2 in s:
    s = s.replace(old2, new2, 1)

# connect_timeout 缩短，避免 30s×N
s = s.replace('dbname="postgres", connect_timeout=20, sslmode="require")',
              'dbname="postgres", connect_timeout=6, sslmode="require")')
io.open(p, "w", encoding="utf-8").write(s)

# 2) db_prices：进程内记住「DB 不可用」，后续进程用 marker 文件
p = "db_prices.py"
s = io.open(p, encoding="utf-8").read()
if "_DB_DEAD" not in s:
    s = s.replace('_conn = None', '_conn = None\n_DB_DEAD = None      # True = 本进程已确认连不上\n_MARKER = os.path.join(BASE, "output", ".db_unavailable")', 1)
    s = s.replace('''def connect():
    global _conn
    if _conn is not None and not _conn.closed:
        return _conn''', '''def connect():
    global _conn, _DB_DEAD
    if _DB_DEAD:
        raise RuntimeError("数据库不可用（本进程已判定）")
    if _conn is not None and not _conn.closed:
        return _conn''')
    s = s.replace('''    _conn = psycopg2.connect(host=HOST, port=6543, user="postgres." + REF, password=pw,
                             dbname="postgres", connect_timeout=20, sslmode="require")
    return _conn''', '''    try:
        _conn = psycopg2.connect(host=HOST, port=6543, user="postgres." + REF, password=pw,
                                 dbname="postgres", connect_timeout=6, sslmode="require")
    except Exception:
        _DB_DEAD = True
        try:
            open(_MARKER, "w").write("1")
        except Exception:
            pass
        raise
    return _conn''')
    # _db_stamp 快速返回 None
    s = s.replace('''def _db_stamp():
    """数据版本戳：行数 + 最大抓取时间 + 标的数（一条轻量查询）"""
    try:''', '''def _db_stamp():
    """数据版本戳：行数 + 最大抓取时间 + 标的数（一条轻量查询）"""
    if _DB_DEAD and os.path.exists(_MARKER):
        return None
    try:''')
    io.open(p, "w", encoding="utf-8").write(s)

import ast
for f in ("db_select.py", "db_prices.py"):
    ast.parse(io.open(f, encoding="utf-8").read())
print("快速失败已实现")
