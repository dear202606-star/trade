# -*- coding: utf-8 -*-
"""variant_b 改为纯在线库口径：成分/行业从 DB 读，禁止退回文件。"""
import io
import ast

P = "variant_b.py"
s = io.open(P, encoding="utf-8").read()

start_marker = "    # 优先用冻结快照（static/），保证本地与 CI 结果完全一致"
end_marker = "    today_et = dt.datetime.now(dt.timezone.utc)"

i = s.find(start_marker)
j = s.find(end_marker)
if i < 0 or j < 0:
    raise SystemExit("定位失败: %d %d" % (i, j))

new_block = '''    # 口径定义一律来自在线库（用户要求：禁止线下文件做数据依据）
    import pandas as _pd
    import db_ref as _ref
    try:
        _mem_db = _ref.load_members()
        _rows = [{"ticker": k, "start_date": a, "end_date": b}
                 for k, segs in _mem_db.items() for a, b in segs]
        m = _pd.DataFrame(_rows)
        sector = _ref.load_sectors()
        print("口径来自在线库：成分 %d 行 / 行业 %d 只" % (len(m), len(sector)), flush=True)
    except Exception as _e:
        raise RuntimeError("在线库不可用，禁止退回线下文件：%s" % str(_e)[:120])

'''

s = s[:i] + new_block + s[j:]

# 覆盖率检查块也改为按库（找到旧的 csv 统计片段并替换）
old_cov_start = s.find("        import csv as _csv")
if old_cov_start > 0:
    old_cov_end = s.find("    except Exception as _e:", old_cov_start)
    if old_cov_end > old_cov_start:
        cov_new = '''        import db_ref as _ref0
        _mem0 = _ref0.load_members()
        _need = len([t for t in _mem0 if max(b for _, b in _mem0[t]) >= "2009-01-01"])
        import db_prices as _dbp0
        _have = len(_dbp0.load_all())
        _cov = _have / max(1, _need)
        print("在线库行情覆盖 %.1f%%（%d/%d 只）" % (_cov * 100, _have, _need), flush=True)
'''
        s = s[:old_cov_start] + cov_new + s[old_cov_end:]

io.open(P, "w", encoding="utf-8").write(s)
ast.parse(io.open(P, encoding="utf-8").read())
print("已改为纯库口径")
