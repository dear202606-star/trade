"""Rehearse (and, only after approval, record) reviewed identity resolutions.

The quarantine table says "this series is unsafe". It does not say whether
anyone looked at it. That distinction is why db_ref currently reports every
historical delisting as "still pending verification" every single day.

This module computes a reproducible proof from the ONLINE database for each
quarantined historical index member and records the decision that the
quarantine is reviewed and must stay. It never releases the quarantine,
never edits prices/series_pick/data_lock/members, and never claims the
price history is complete (full_history_accepted stays False).

Evidence is intentionally dominated by facts that can be recomputed from the
online tables: membership end date, price observations surviving past that
date, and extreme month-over-month jumps. External SEC documents are
supporting context, not the sole basis of the decision.

Default behaviour rolls back everything and writes a rehearsal receipt.
--write is a separate decision and requires an explicit --decided-by.
"""
import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import db_sources

SQL_FILE = BASE / "sql" / "125_security_identity_resolutions.sql"
RECEIPT_DIR = BASE / "output"

# Historical index members only. P is deliberately excluded: it is a CURRENT
# member (members.end_date sentinel 2099-12-31) with its own live identity
# review, and must never be auto-excluded from the live ranking pool.
SYMBOLS = (
    "BMC", "CBE", "COV", "EP", "GAS", "HAR", "LSI", "PARA",
    "PCL", "SBNY", "SCG", "SUN", "TIE",
)

SCOPE = (
    "维持既有隔离并允许门禁显式排除；不解锁价格序列、不改写历史观察、"
    "不代表 full_history_accepted，原始价格与锁一律保留"
)

# Independently verified supporting documents. "partial" means the online
# facts are conclusive but an external issuer filing was not located in this
# pass; those symbols are listed in the delivery document for follow-up.
EXTERNAL = {
    "BMC": ("BMC Software, Inc. (CIK 835729)", "confirmed", [
        "https://sec.gov/edgar/browse/?CIK=835729"]),
    "CBE": ("Cooper Industries / Eaton", "partial", []),
    "COV": ("Covidien plc / Medtronic", "partial", []),
    "EP": ("El Paso Corporation / Kinder Morgan", "confirmed", [
        "https://sec.gov/Archives/edgar/data/1066107/000110465912035982/a12-11912_18k.htm",
        "https://sec.gov/Archives/edgar/data/1066107/000110465912035982/a12-11912_1ex99d1.htm"]),
    "GAS": ("AGL Resources -> Southern Company Gas (CIK 1004155)", "confirmed", [
        "https://sec.gov/cgi-bin/own-disp?action=getissuer&CIK=0001004155"]),
    "HAR": ("Harman International / Samsung (CIK 800459)", "confirmed", [
        "https://sec.gov/Archives/edgar/data/800459/000119312517078968/0001193125-17-078968-index.htm"]),
    "LSI": ("LSI Corporation (CIK 703360) / Avago", "confirmed", [
        "https://sec.gov/Archives/edgar/data/703360/000119312514157282/d699075d10ka.htm"]),
    "PARA": ("Paramount Global; HF vs Sina unit/currency mismatch", "partial", []),
    "PCL": ("Plum Creek Timber / Weyerhaeuser", "partial", []),
    "SBNY": ("Signature Bank / FDIC receivership 2023-03", "partial", []),
    "SCG": ("SCANA / Dominion; see research/review_scg_original_equity.py", "confirmed", []),
    "SUN": ("Sunoco, Inc. (CIK 95304) vs Sunoco LP (CIK 1552275)", "confirmed", [
        "https://www.sec.gov/Archives/edgar/data/1012569/000119312512416888/d421405d8k.htm",
        "https://www.sec.gov/Archives/edgar/data/1552275/000155227515000007/sun-20141231x10k.htm"]),
    "TIE": ("Titanium Metals / Precision Castparts; unit contamination", "partial", []),
}

SENTINEL = dt.date(2099, 12, 31)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")).hexdigest()


def _membership_ends(cursor, symbols):
    cursor.execute(
        "select symbol,max(end_date) from public.members "
        "where symbol=any(%s) and end_date <> %s group by symbol",
        (list(symbols), SENTINEL))
    return dict(cursor.fetchall())


def _post_end_observations(cursor, symbols):
    ends = _membership_ends(cursor, symbols)
    cursor.execute(
        "select symbol,count(*),max(month) from public.prices_monthly "
        "where symbol=any(%s) group by symbol", (list(symbols),))
    out = {}
    for symbol, count, last_month in cursor.fetchall():
        end = ends.get(symbol)
        cursor.execute(
            "select count(*) from public.prices_monthly "
            "where symbol=%s and month > %s", (symbol, end.isoformat()[:7]))
        after = cursor.fetchone()[0]
        out[symbol] = {
            "observations": count,
            "last_month": last_month,
            "after_membership_end": after,
        }
    return out


def _extreme_jumps(cursor, symbols):
    cursor.execute("""
        with s as (
          select symbol,month,adj_close,
                 lag(adj_close) over(partition by symbol order by month) as prev
          from public.prices_monthly
          where symbol=any(%s) and source='sina_repaired'
            and adj_close is not null and adj_close>0)
        select symbol,month,prev,adj_close,
               round((adj_close/prev)::numeric,4) as ratio
        from s
        where prev>0 and (adj_close/prev>10 or adj_close/prev<0.01)
        order by symbol,month
    """, (list(symbols),))
    out = {s: [] for s in symbols}
    for symbol, month, prev, value, ratio in cursor.fetchall():
        out[symbol].append({
            "month": month,
            "from": str(prev),
            "to": str(value),
            "ratio": str(ratio),
        })
    return out


def build_proofs(cursor):
    cursor.execute(
        "select symbol,reason from public.series_quarantine "
        "where symbol=any(%s) order by symbol", (list(SYMBOLS),))
    reasons = dict(cursor.fetchall())
    missing = sorted(set(SYMBOLS) - set(reasons))
    if missing:
        raise RuntimeError("隔离表中缺少预期标的：" + ",".join(missing))

    ends = _membership_ends(cursor, SYMBOLS)
    prices = _post_end_observations(cursor, SYMBOLS)
    jumps = _extreme_jumps(cursor, SYMBOLS)
    proofs = {}
    for symbol in SYMBOLS:
        issuer, status, urls = EXTERNAL[symbol]
        end = ends.get(symbol)
        proofs[symbol] = {
            "symbol": symbol,
            "resolution": "exclude_reviewed",
            "scope": SCOPE,
            "full_history_accepted": False,
            "issuer": issuer,
            "external_evidence_status": status,
            "evidence_urls": urls,
            "membership_end": end.isoformat() if end else None,
            "quarantine_reason_sha256": hashlib.sha256(
                (reasons[symbol] or "").encode("utf-8")).hexdigest(),
            "price_observations": prices.get(symbol, {}),
            "extreme_jump_count": len(jumps.get(symbol, [])),
            "extreme_jump_examples": jumps.get(symbol, [])[:3],
        }
    return proofs


def preserved(cursor):
    """No accepted financial data may move while resolutions are written."""
    result = {}
    for table, order in (
        ("prices_monthly", "symbol,month,source"),
        ("nominal_monthly", "symbol,month,source"),
        ("series_pick", "symbol"),
        ("data_lock", "id"),
        ("members", "symbol,start_date"),
        ("series_quarantine", "symbol"),
    ):
        cursor.execute(
            "select * from public.%s where symbol=any(%%s) order by %s"
            % (table, order), (list(SYMBOLS),))
        result[table] = hashlib.sha256(
            json.dumps(cursor.fetchall(), default=str, sort_keys=True,
                       separators=(",", ":")).encode("utf-8")).hexdigest()
    return result


def run(write=False, decided_by="rehearsal"):
    if write:
        if (db_sources.current_target() or "supabase") != "supabase":
            raise RuntimeError("--write 只能作用于主库 Supabase，禁止写入 Neon 冷备")
        if decided_by == "rehearsal":
            raise RuntimeError("--write 必须显式传 --decided-by 记录批准人")

    connection = db_sources.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute("set local lock_timeout='5s'")
            cursor.execute(SQL_FILE.read_text(encoding="utf-8"))
            cursor.execute(
                "select symbol,resolution,evidence_sha256 from "
                "public.security_identity_resolution where symbol=any(%s) "
                "order by symbol", (list(SYMBOLS),))
            existing = cursor.fetchall()
            proofs = build_proofs(cursor)
            expected = [(s, "exclude_reviewed", digest(p))
                        for s, p in sorted(proofs.items())]
            if existing:
                if existing != expected:
                    raise RuntimeError("裁定与既有证据冲突，保留既有结论，禁止覆盖")
                connection.rollback()
                print(json.dumps({"write": write, "status": "already_online",
                                  "resolutions": len(expected)}))
                return
            before = preserved(cursor)
            for symbol, proof in sorted(proofs.items()):
                cursor.execute(
                    "insert into public.security_identity_resolution"
                    "(symbol,resolution,membership_end,proof,evidence_sha256,decided_by) "
                    "values(%s,%s,%s,%s,%s,%s)",
                    (symbol, "exclude_reviewed", proof["membership_end"],
                     json.dumps(proof, ensure_ascii=False), digest(proof), decided_by))
            if preserved(cursor) != before:
                raise RuntimeError("价格、选源、锁、成员或隔离表发生变更，立即回滚")
        if write:
            connection.commit()
        else:
            connection.rollback()
        receipt = {
            "write": write,
            "decided_by": decided_by,
            "preserved_sha256": before,
            "financial_data_changed": False,
            "resolutions": len(proofs),
            "symbols": sorted(proofs),
            "proofs": proofs,
            "scope": SCOPE,
        }
        name = ("security_identity_resolutions_applied.json" if write
                else "security_identity_resolutions_rollback.json")
        RECEIPT_DIR.mkdir(exist_ok=True)
        (RECEIPT_DIR / name).write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(json.dumps({"write": write, "resolutions": len(proofs),
                          "financial_data_changed": False,
                          "receipt": name}))
    finally:
        connection.rollback()
        connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--decided-by", default="rehearsal")
    args = parser.parse_args()
    run(args.write, args.decided_by)
