# -*- coding: utf-8 -*-
"""db_validate.py — 全链路校验并把结论写进 validations 表（审计 P1-4：表长期 0 行）

检查分四组
  A. 数据覆盖：12 月在册覆盖、全收益占比、完全缺失标的
  B. 数据质量：连续性（单月跳变）、价格合理性、口径自证
  C. 产物一致：signal == equity 持仓、逐年复利 == 累计、曲线末点 == 累计
  D. 锁定就绪：是否达到 100% 全收益 + 覆盖门槛

用法：python db_validate.py [--dataset NAME]
"""
import argparse
import io
import json
import os
import sys

import psycopg2

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
REF = "fnvahsyrzmmxwovbsqpc"
HOST = "aws-0-ap-northeast-1.pooler.supabase.com"
YEARS = list(range(2010, 2026))
ROWS = []


def rec(name, status, detail=""):
    ROWS.append((name, status, detail))
    mark = {"pass": "✅", "fail": "❌", "warn": "⚠"}.get(status, "·")
    print("  %s %-34s %s" % (mark, name, detail[:110]))


def db():
    pw = os.environ.get("SUPABASE_DB_PASSWORD", "")
    if not pw:
        p = os.path.join(BASE, "output", "_supabase_dbpass.txt")
        if os.path.exists(p):
            pw = io.open(p, encoding="utf-8").read().strip()
    return psycopg2.connect(host=HOST, port=6543, user="postgres." + REF, password=pw,
                            dbname="postgres", connect_timeout=20, sslmode="require")


def _reason_text(ratio, cov):
    """把「缺多少」说清楚：缺口里多少是源限制（退市票），多少是还没轮到"""
    need = cov.get("need") or 0
    full = cov.get("full") or 0
    miss = cov.get("missing") or 0
    head = "全收益口径 %.1f%%" % (100.0 * ratio)
    if not need:
        return head
    tail = "已覆盖 %d/%d 只" % (full, need)
    if miss:
        tail += "，缺 %d 只" % miss
        dl = cov.get("delisted_gap")
        if dl is not None and miss:
            share = 100.0 * dl / miss
            if share >= 50:
                tail += "（%.0f%% 为退市票，历史数据仍待核验）" % share
    return head + " · " + tail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="")
    ap.add_argument("--data-only", action="store_true",
                    help="只验收实时数据库；发布产物由完整模式验收")
    a = ap.parse_args()
    # DB 仅用于写台账；CI 无口令时仍可完成校验（数据走 db_prices）
    try:
        c = db()
        cur = c.cursor()
    except Exception as _e:
        print("⚠ 数据库不可用（%s），本次只做校验与质量快照" % str(_e)[:60])
        c, cur = None, None

    # ---------- A 覆盖（数据来源：db_prices，DB 或 REST 均可）----------
    import db_prices as _dbp
    _all = _dbp.load_all(use_cache=False)
    nrows = sum(len(d) for d in _all.values())
    syms = len(_all)
    # 全收益行数：用 series_selected 的口径近似——按 basis 无法从 load_all 得到，
    # 改为统计「有 12 月数据」的标的数（覆盖率的实质指标）
    dec_counts = {y: sum(1 for s2 in _all.values() if ("%d-12" % y) in s2) for y in YEARS}
    rec("序列标的数", "pass" if syms >= 840 else "warn", "%d 只" % syms)
    rec("总月数", "pass" if nrows > 100000 else "warn", "%d 行" % nrows)
    _tr_ratio = None
    try:
        import psycopg2 as _pg
        _c = db()
        with _c.cursor() as _cur:
            _cur.execute("select count(*) filter (where basis='total_return'), count(*) from series_selected")
            _t, _n = _cur.fetchone()
            _tr_ratio = _t / max(1, _n)
        _c.close()
    except Exception:
        rec("全收益口径读取", "fail", "无法读取实时口径，禁止使用固定覆盖率")
    if _tr_ratio is not None:
        rec("全收益行占比", "pass" if _tr_ratio >= 0.999 else "warn",
            "%.1f%%（目标 100%%）" % (100 * _tr_ratio))
    for y in YEARS:
        have = dec_counts.get(y, 0)
        rec("%d-12 有数据标的" % y, "pass" if have >= 480 else "warn", "%d 只" % have)

    # ---------- B 质量 ----------
    prev, jumps, bad = None, 0, 0
    for sym in sorted(_all):
        ks = sorted(_all[sym])
        for m in ks:
            adj = _all[sym][m][0]
            if not adj or adj <= 0:
                bad += 1
                continue
            if prev and prev[0] == sym and prev[2]:
                r = adj / prev[2]
                if r > 4 or r < 0.25:
                    jumps += 1
            prev = (sym, m, adj)
    rec("价格非正数", "pass" if bad == 0 else "fail", "%d 行" % bad)
    rec("单月跳变（>4x 或 <0.25x）", "pass" if jumps <= 60 else "warn", "%d 处" % jumps)

    for ticker, m0, m1, lo, hi in (("NVDA", "2020-12", "2021-12", 1.20, 1.30),
                                   ("NVDA", "2023-12", "2024-12", 1.65, 1.78),
                                   ("DUK", "2012-06", "2012-07", -0.06, 0.02)):
        d = _all.get(ticker, {})
        if m0 in d and m1 in d:
            r = d[m1][0] / d[m0][0] - 1
            rec("抽查 %s %s→%s" % (ticker, m0, m1),
                "pass" if lo <= r <= hi else "fail", "%+.2f%%（期望 %+.0f%%~%+.0f%%）" % (
                    r * 100, lo * 100, hi * 100))
        else:
            rec("抽查 %s %s→%s" % (ticker, m0, m1), "fail", "缺数据")

    # ---------- C 产物一致 ----------
    if not a.data_only:
        try:
            e = json.load(open(os.path.join(BASE, "docs/data/b/equity.json"), encoding="utf-8"))
            s = json.load(open(os.path.join(BASE, "docs/data/b/signal.json"), encoding="utf-8"))
            same = sorted(p["ticker"] for p in e["position"]) == sorted(
                x["ticker"] for x in s["official"])
            rec("signal 与 equity 持仓一致", "pass" if same else "fail", str(
                [p["ticker"] for p in e["position"]]))
            h = json.load(open(os.path.join(BASE, "docs/data/b/strategy_history.json"), encoding="utf-8"))
            cv = json.load(open(os.path.join(BASE, "docs/data/b/curve.json"), encoding="utf-8"))
            comp = 1.0
            for r in h["years"]:
                comp *= (1 + r["ret"])
            rec("逐年复利 == 累计", "pass" if abs(comp - (1 + h["summary"]["total"])) < 0.02 else "fail",
                "%.4f vs %.4f" % (comp, 1 + h["summary"]["total"]))
            rec("曲线末点 == 累计", "pass" if abs(cv["points"][-1]["s"] - comp) < 0.05 else "fail",
                "%.4f vs %.4f" % (cv["points"][-1]["s"], comp))
        except Exception as ex:
            rec("产物读取", "fail", str(ex)[:80])

    # ---------- D 锁定就绪 ----------
    if not a.data_only:
        ready = all(r[1] == "pass" for r in ROWS) and (_tr_ratio or 0) >= 1.0
        rec("锁定就绪（100% 全收益 + 全检查通过）", "pass" if ready else "warn",
            "全收益 %.1f%%" % (100.0 * (_tr_ratio or 0)))

    # ---------- 覆盖率明细（供页面显示进度）----------
    coverage_detail = {}
    try:
        _c = db()
        with _c.cursor() as _cur:
            _cur.execute("""
              with need as (
                select distinct symbol from members
                 where end_date >= '2009-01-01'
              ), have as (
                select symbol,
                       count(*) filter (where basis='total_return') tr,
                       count(*) n
                  from series_selected where month between '2012-01' and '2025-12'
                 group by symbol
              )
              select count(*) as need_total,
                     count(*) filter (where coalesce(h.tr,0) = 0) as missing_all,
                     count(*) filter (where coalesce(h.tr,0) > 0 and coalesce(h.tr,0) < coalesce(h.n,1)) as partial,
                     count(*) filter (where coalesce(h.tr,0) > 0 and coalesce(h.tr,0) = coalesce(h.n,0)) as full
                from need left join have h on h.symbol = need.symbol""")
            _row = _cur.fetchone()
            coverage_detail = {"need": _row[0], "missing": _row[1],
                               "partial": _row[2], "full": _row[3]}
            _cur.execute("""
              select count(*) from members m
               where m.symbol in (
                 select symbol from members where end_date >= '2009-06-30'
                 except
                 select symbol from series_selected
                  where month between '2012-01' and '2025-12' and basis='total_return')
                 and m.end_date < '2099-01-01'""")
            coverage_detail["delisted_gap"] = _cur.fetchone()[0]
        _c.close()
    except Exception as _e:
        print("覆盖率明细失败：%s" % str(_e)[:60])

    # ---------- 写站点质量快照（CI 无 DB 写权限时也可用）----------
    try:
        import json as _json
        _q = {
            "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "total_return_ratio": round(_tr_ratio, 4) if _tr_ratio is not None else None,
            "degraded": (_tr_ratio or 0) < 0.999,
            "reason": _reason_text(_tr_ratio or 0, coverage_detail),
            "checks": [{"name": n, "status": st, "detail": d} for n, st, d in ROWS],
            "coverage": coverage_detail,
            "summary": {"pass": sum(1 for r in ROWS if r[1] == "pass"),
                        "warn": sum(1 for r in ROWS if r[1] == "warn"),
                        "fail": sum(1 for r in ROWS if r[1] == "fail")},
        }
        _json.dump(_q, open(os.path.join(BASE, "docs/data/quality.json"), "w", encoding="utf-8"),
                   ensure_ascii=False, indent=1)
        print("质量快照已写 docs/data/quality.json")
    except Exception as _e:
        print("质量快照写入失败：%s" % _e)

    # ---------- 写库 ----------
    try:
        c = db()
        c.autocommit = True
    except Exception as _e:
        print("\n⚠ 无法写入 validations（%s），质量快照已生成" % str(_e)[:60])
        return 0 if all(r[1] != "fail" for r in ROWS) else 1
    with c.cursor() as cur:
        cur.execute("select id from datasets where name=%s", (a.dataset,)) if a.dataset else None
        did = cur.fetchone()[0] if a.dataset and cur.rowcount else None
        for name, status, detail in ROWS:
            cur.execute("""insert into sys_event(kind, source, subject, status, detail,
                                                dataset_id, finished_at)
                           values ('validation','db_validate',%s,%s,%s,%s, now())""",
                        (name, status, detail, did))
    c.close()
    ok = sum(1 for r in ROWS if r[1] == "pass")
    print("\n写入 validations：%d 条（通过 %d / 警告 %d / 失败 %d）" % (
        len(ROWS), ok, sum(1 for r in ROWS if r[1] == "warn"),
        sum(1 for r in ROWS if r[1] == "fail")))
    return 0 if all(r[1] != "fail" for r in ROWS) else 1


if __name__ == "__main__":
    sys.exit(main())
