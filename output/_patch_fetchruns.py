# -*- coding: utf-8 -*-
"""补 fetch_runs 真实台账（审计 P1-2/P1-4：failed 恒 0、started==finished、失败零入库）"""
import io

# 1) db_prices 增加统一台账函数
p = "db_prices.py"
s = io.open(p, encoding="utf-8").read()
if "def log_run(" not in s:
    s = s.rstrip() + '''


def log_run(source, requested, ok, failed=0, duration_s=0.0, note=""):
    """统一采集台账：每次运行都写一行，含耗时与失败数"""
    try:
        with connect() as c, c.cursor() as cur:
            cur.execute("""insert into fetch_runs(source, requested, ok, failed,
                                                   started_at, finished_at, note)
                           values (%s,%s,%s,%s,
                                   now() - make_interval(secs => %s), now(), %s)""",
                        (source, requested, ok, failed, float(duration_s), (note or "")[:300]))
    except Exception as e:
        print("台账写入失败：%s" % str(e)[:80])
'''
    io.open(p, "w", encoding="utf-8").write(s)
    print("db_prices.log_run 已加")

# 2) relay_source 用 log_run，并记录失败
p = "relay_source.py"
s = io.open(p, encoding="utf-8").read()
if "log_run(" not in s:
    s = s.replace('''        cur.execute("""insert into fetch_runs(source,finished_at,requested,ok,failed,note)
                       values ('relay',now(),%s,%s,%s,%s)""",
                    (done, sum(1 for r in all_rows), 0, json.dumps(stats)))''',
'''    try:
        DB.log_run("relay", done, sum(1 for r in all_rows),
                   failed=sum(1 for r in all_rows if r[5] == "bad"),
                   duration_s=time.time() - _t0, note=json.dumps(stats))
    except Exception as _e:
        print("台账失败：%s" % str(_e)[:60])''')
    s = s.replace("    av_left = 0 if a.no_av else a.av_quota",
                  "    _t0 = time.time()\n    av_left = 0 if a.no_av else a.av_quota")
    s = s.replace("import db_prices", "import db_prices as DB", 1)
    io.open(p, "w", encoding="utf-8").write(s)
    print("relay_source 台账已接")

# 3) backfill_yahoo 结束时写台账
p = "backfill_yahoo.py"
s = io.open(p, encoding="utf-8").read()
if "log_run(" not in s:
    s = s.replace('''    print("完成：成功 %d / 跳过 %d / 失败 %d，用时 %.0fs" % (
        STATS["ok"], STATS["skip"], STATS["bad"], time.time() - t0))''',
'''    print("完成：成功 %d / 跳过 %d / 失败 %d，用时 %.0fs" % (
        STATS["ok"], STATS["skip"], STATS["bad"], time.time() - t0))
    try:
        DB.log_run("backfill_yahoo", len(syms), STATS["ok"], failed=STATS["bad"],
                   duration_s=time.time() - t0,
                   note="skip=%d（404/429/校验未过）" % STATS["skip"])
    except Exception as e:
        print("台账失败：%s" % str(e)[:60])''')
    io.open(p, "w", encoding="utf-8").write(s)
    print("backfill_yahoo 台账已接")

import ast
for f in ("db_prices.py", "relay_source.py", "backfill_yahoo.py"):
    ast.parse(io.open(f, encoding="utf-8").read())
print("语法 OK")
