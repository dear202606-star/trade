# -*- coding: utf-8 -*-
"""把 lock_symbols.py 的写入改到 data_lock（M5 后 locked_symbols 是只读视图）"""
import io
import re
import ast

P = "lock_symbols.py"
s = io.open(P, encoding="utf-8").read()

# 1) 不再建表 / 不再改函数（DDL 归 sql/）
start = s.find("DDL = ")
if start > 0:
    end = s.find("def db():", start)
    if end > start:
        s = s[:start] + s[end:]

# 2) ensure() 只保留「确认表存在」的检查，不做 DDL
s = re.sub(r"def ensure\(\):(?:.|\n)*?\n\n\ndef ", '''def ensure():
    """DDL 已收归 sql/050_data_lock.sql；这里只确认结构就绪"""
    c = db()
    with c.cursor() as cur:
        cur.execute("select to_regclass('public.data_lock') is not null")
        if not cur.fetchone()[0]:
            raise RuntimeError("data_lock 不存在：请先执行 sql/050_data_lock.sql")
    c.close()


def ''', s, count=1)

# 3) 写入改为 insert into data_lock(scope='symbol_month', ...)
s = s.replace('''            cur.execute("""insert into locked_symbols(symbol, months, hash, note)
                           values (%s,%s,%s,%s)
                           on conflict (symbol) do nothing""",
                        (sym, months, hash_series(ser), "%s..%s 全收益完整" % (Y0, Y1)))''',
'''            cur.execute("""insert into data_lock(scope, symbol, month_from, month_to, status,
                                                symbol_count, fingerprint, fingerprint_algo, note)
                           values ('symbol_month', %s, %s, %s, 'locked', 1, %s, 'sha256-16', %s)
                           on conflict do nothing""",
                        (sym, Y0, Y1, hash_series(ser), "%s..%s 全收益完整" % (Y0, Y1)))''')

# 4) 计数与列表改读视图/新表
s = s.replace('cur.execute("select count(*) from locked_symbols")', 'cur.execute("select count(*) from data_lock where scope=\'symbol_month\' and status=\'locked\'")')
s = s.replace('cur.execute("""select count(*), min(locked_at), sum(months) from locked_symbols""")',
              'cur.execute("""select count(*), min(locked_at), count(*)*168 from data_lock where scope=\'symbol_month\' and status=\'locked\'""")')
s = s.replace('cur.execute("select symbol from locked_symbols order by symbol limit 10")',
              'cur.execute("select symbol from locked_symbols order by symbol limit 10")')

io.open(P, "w", encoding="utf-8").write(s)
ast.parse(io.open(P, encoding="utf-8").read())
print("lock_symbols.py 已改为写 data_lock")
print("残留 locked_symbols 引用:", s.count("locked_symbols"))
