"""verify_frozen_history.py — 只读巡检「2025 年前历史是否真正冻结且未误伤新增月份」。

回答两个问题：
  1) 冻结区间内还有没有人在改写/重抓？（库内守卫 + 月度连续性）
  2) 冻结有没有把 2026 年之后的新增月份也一起挡掉？（旧消费端会整只跳过）

只读，不写库、不新建/撤销任何锁。退出码 0 表示健康，1 表示发现问题。
"""
import argparse
import datetime as dt
import json
import sys

FROZEN_FROM, FROZEN_TO = "2012-01", "2025-12"
FROZEN_BEFORE = "2026-01-01"


def connect():
    import db_sources
    return db_sources.db()


def inspect():
    conn = connect()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("select * from public.v_history_freeze_policy")
            cols = [d[0] for d in cur.description]
            policy = dict(zip(cols, cur.fetchone()))

            # 冻结区间内：已锁标的的月度覆盖是否有缺口
            cur.execute("""
                select count(*) from public.v_frozen_month_boundaries b
                 where not exists (
                   select 1 from generate_series(
                     to_date(b.month_from,'YYYY-MM'),
                     to_date(b.month_to,'YYYY-MM'),
                     interval '1 month') g(m)
                   where not exists (
                     select 1 from public.prices_monthly p
                      where p.symbol=b.symbol
                        and p.month=to_char(g.m,'YYYY-MM')))
            """)
            complete_symbols = cur.fetchone()[0]
            cur.execute("select count(*) from public.v_frozen_month_boundaries")
            locked_symbols = cur.fetchone()[0]

            # 冻结区外：2026 年之后是否仍有新增月数据（锁不能误伤）
            cur.execute("""
                select count(distinct symbol), max(month), count(*)
                  from public.prices_monthly where month >= '2026-01'
            """)
            new_symbols, newest_month, new_rows = cur.fetchone()

            # 判定函数与库内守卫是否一致（抽查边界月份）
            cur.execute("""
                select symbol from public.v_frozen_month_boundaries limit 5
            """)
            samples = [r[0] for r in cur.fetchall()]
            checks = []
            for s in samples:
                cur.execute("select public.is_frozen_month(%s,%s),public.is_frozen_month(%s,%s)",
                            (s, FROZEN_TO, s, "2026-02"))
                inside, outside = cur.fetchone()
                checks.append({"symbol": s, "frozen_2025_12": inside,
                               "frozen_2026_02": outside})
    finally:
        conn.close()

    problems = []
    if locked_symbols == 0:
        problems.append("没有生效的历史锁")
    if complete_symbols < locked_symbols:
        problems.append("冻结区间存在月度缺口：%d/%d 只标的未覆盖全区间"
                        % (locked_symbols - complete_symbols, locked_symbols))
    if not new_rows:
        problems.append("2026 年后无任何新增月数据，可能被「整只跳过」误伤")
    for c in checks:
        if not c["frozen_2025_12"] or c["frozen_2026_02"]:
            problems.append("冻结边界判定异常：%s" % c["symbol"])

    return {
        "ok": not problems,
        "frozen_before": FROZEN_BEFORE,
        "policy": policy,
        "locked_symbols": locked_symbols,
        "symbols_with_full_month_coverage": complete_symbols,
        "new_months_since_2026": {"symbols": new_symbols,
                                  "newest_month": newest_month, "rows": new_rows},
        "boundary_checks": checks,
        "problems": problems,
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--allow-missing-new-months", action="store_true",
                    help="非交易期允许 2026+ 暂无新增月（仅用于本地演练）")
    a = ap.parse_args()
    try:
        r = inspect()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])},
                         ensure_ascii=False))
        return 1
    if a.allow_missing_new_months:
        r["problems"] = [p for p in r["problems"] if "2026" not in p]
        r["ok"] = not r["problems"]
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
