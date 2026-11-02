# -*- coding: utf-8 -*-
"""Import verified monthly rows from the CC0 Kaggle historical archive.

Default mode downloads and validates only unlocked S&P 500 symbol-years that
currently lack a total-return December close. Pass --write to register the
source and send the resulting rows through the existing ingest_staging path.
"""
import argparse
import csv
import io
import json
import socket
import time
import urllib.error
import urllib.request
import urllib.parse
import zipfile
from collections import defaultdict
from pathlib import Path

import db_sources
import ingest_validate
import staging_write


BASE = Path(__file__).resolve().parent
SOURCE = "kaggle_cc0"
OWNER = "borismarjanovic"
DATASET = "price-volume-data-for-all-us-stocks-etfs"
FIRST_YEAR = 2010
LAST_ARCHIVE_DECEMBER = 2016  # Dataset's last observation is 2017-11-10.
FIRST_MONTH = "2008-01"
LAST_MONTH = "2017-11"
MAX_FILE_BYTES = 20 * 1024 * 1024


class KaggleRateLimit(RuntimeError):
    pass


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        old, new = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
        if redirected and (old.scheme, old.netloc) != (new.scheme, new.netloc):
            redirected.remove_header("Authorization")
        return redirected


def _local_value(name):
    path = BASE / "output" / "_api_keys.local.txt"
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            key, sep, value = line.partition(":")
        if sep and key.strip() == name:
            return value.strip().strip("\"'")
    return ""


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _query_state():
    """Read the live PIT universe, selected data, and the active write guards."""
    manifest = json.loads((BASE / "static" / "kaggle_cc0_file_index.json").read_text(encoding="utf-8"))
    files = manifest["files"]
    years = list(range(FIRST_YEAR, LAST_ARCHIVE_DECEMBER + 1))
    decembers = ["%d-12" % year for year in years]

    members = defaultdict(set)
    selected_decembers = defaultdict(set)
    locked_symbols = set()
    month_locks = []
    existing_symbols = set()
    existing_selected = defaultdict(dict)
    existing_source_rows = set()
    staged_source_rows = set()

    with db_sources.db() as conn, conn.cursor() as cur:
        cur.execute("select symbol, start_date, end_date from members")
        for symbol, start, end in cur.fetchall():
            start, end = _iso(start), _iso(end)
            for year in years:
                dec31 = "%d-12-31" % year
                if start <= dec31 <= end:
                    members[year].add(symbol)

        cur.execute("""select symbol, month from series_selected
                       where basis='total_return' and month = any(%s)""", (decembers,))
        for symbol, month in cur.fetchall():
            selected_decembers[int(month[:4])].add(symbol)

        # The live prices_monthly trigger still uses this legacy guard. Treat
        # every row here as a whole-symbol lock, regardless of its lock dates.
        cur.execute("select distinct symbol from locked_symbols")
        locked_symbols = {row[0] for row in cur.fetchall()}
        cur.execute("""select scope_from, scope_to from locks
                       where kind='monthly_prices' and status='locked'""")
        month_locks = [(_iso(start), _iso(end)) for start, end in cur.fetchall()]

        targets = defaultdict(set)
        for year in years:
            missing = members[year] - selected_decembers[year]
            for symbol in missing:
                if symbol not in files and symbol.lower() not in files:
                    continue
                if symbol in locked_symbols:
                    continue
                month = "%d-12" % year
                if any(start <= month <= end for start, end in month_locks):
                    continue
                targets[symbol].add(year)

        symbols = sorted(targets)
        if symbols:
            cur.execute("""select symbol, month, adj_close, raw_close
                           from series_selected
                           where symbol = any(%s) and basis='total_return'
                             and month between %s and %s""",
                        (symbols, FIRST_MONTH, LAST_MONTH))
            for symbol, month, adj, raw in cur.fetchall():
                existing_selected[symbol][month] = (float(adj), float(raw) if raw else None)

            cur.execute("select distinct symbol from prices_monthly")
            existing_symbols = {row[0] for row in cur.fetchall()}
            cur.execute("select symbol, month from prices_monthly where source=%s", (SOURCE,))
            existing_source_rows = set(cur.fetchall())
            cur.execute("""select symbol, month from ingest_staging
                           where source=%s""", (SOURCE,))
            staged_source_rows = set(cur.fetchall())

    def is_locked(symbol, month):
        return symbol in locked_symbols or any(a <= month <= b for a, b in month_locks)

    return files, targets, existing_selected, existing_symbols, existing_source_rows, staged_source_rows, is_locked


def _token():
    token = _local_value("kaggle_token")
    if not token:
        raise RuntimeError("output/_api_keys.local.txt 缺少 kaggle_token")
    return token


def unpack_file(raw, file_name):
    """Some Kaggle single-file responses are ZIPs; never extract archive paths."""
    if len(raw) > MAX_FILE_BYTES:
        raise RuntimeError("Kaggle file exceeds download limit")
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        return raw
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        files = archive.infolist()
        if len(files) != 1 or Path(files[0].filename).name != Path(file_name).name:
            raise RuntimeError("Kaggle archive does not contain only the requested file")
        if files[0].file_size > MAX_FILE_BYTES:
            raise RuntimeError("Kaggle uncompressed file exceeds download limit")
        return archive.read(files[0])


def download_file(token, owner, dataset, file_name):
    """Download a Kaggle CSV using the official API without forwarding credentials."""
    body = json.dumps({"ownerSlug": owner, "datasetSlug": dataset,
                       "fileName": file_name}).encode("utf-8")
    url = "https://www.kaggle.com/api/v1/datasets.DatasetApiService/DownloadDataset"
    for attempt in range(2):
        req = urllib.request.Request(url, data=body, headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        try:
            getaddrinfo = socket.getaddrinfo

            def ipv4_only(host, port, family=0, socktype=0, proto=0, flags=0):
                return getaddrinfo(host, port, socket.AF_INET, socktype, proto, flags)

            socket.getaddrinfo = ipv4_only
            try:
                with urllib.request.build_opener(_SafeRedirect()).open(req, timeout=30) as response:
                    raw = response.read(MAX_FILE_BYTES + 1)
            finally:
                socket.getaddrinfo = getaddrinfo
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise KaggleRateLimit("Kaggle HTTP 429; stop requests until the server window resets") from None
            raise RuntimeError("%s: Kaggle HTTP %s" % (file_name, exc.code)) from None
        except Exception as exc:
            if attempt == 1:
                raise RuntimeError("%s: %s" % (file_name, type(exc).__name__)) from None
            time.sleep(2 * (attempt + 1))

    return unpack_file(raw, file_name)


def _download(token, symbol, file_name):
    raw = download_file(token, OWNER, DATASET, file_name)
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or not {"Date", "Close"}.issubset(reader.fieldnames):
        raise RuntimeError("%s: CSV schema mismatch" % symbol)
    daily = []
    for row in reader:
        date = row.get("Date", "")
        try:
            close = float(row.get("Close", ""))
        except (TypeError, ValueError):
            continue
        if len(date) >= 10 and close > 0:
            daily.append((date[:10], close))
    daily.sort(key=lambda x: x[0])
    bars = {}
    for date, close in daily:
        month = date[:7]
        if FIRST_MONTH <= month <= LAST_MONTH:
            # Dataset card says OHLC prices are dividend- and split-adjusted.
            # The archive does not provide a separate unadjusted close.
            bars[month] = (close, None)
    return symbol, bars, daily[-1][0] if daily else ""


def _register_source(source=SOURCE):
    guard_name, migration = {
        "kaggle_cc0": ("series_pick_archive_review_ck", "090_archive_selection_guard.sql"),
        "kaggle_yahoo2020": ("series_pick_yahoo2020_review_ck", "092_yahoo2020_archive_guard.sql"),
    }[source]
    row = next(row for row in db_sources.SEED if row[0] == source)
    (sid, display, kind, priority, basis, delisted, depth, frequency, quota,
     free, status, role, access, tested) = row
    with db_sources.db() as conn, conn.cursor() as cur:
        cur.execute("""select convalidated from pg_constraint
                       where conrelid='series_pick'::regclass
                         and conname=%s""", (guard_name,))
        guard = cur.fetchone()
        if not guard or not guard[0]:
            raise RuntimeError("先执行 sql/%s，禁止未核验档案自动选源" % migration)
        cur.execute("""insert into data_source
            (source_id, display, kind, priority, basis, delisted, depth, frequency,
             quota_per_day, free, status, role, access, tested, last_ok)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    case when %s='ok' then now() else null end)
            on conflict (source_id) do update set
              display=excluded.display, kind=excluded.kind, priority=excluded.priority,
              basis=excluded.basis, delisted=excluded.delisted, depth=excluded.depth,
              frequency=excluded.frequency, quota_per_day=excluded.quota_per_day,
              free=excluded.free, status=excluded.status, role=excluded.role,
              access=excluded.access, tested=excluded.tested, updated_at=now()""",
                    (sid, display, kind, priority, basis, delisted, depth, frequency,
                     quota, free, status, role, access, tested, status))


def collect():
    files, targets, existing, existing_symbols, existing_rows, staged_rows, is_locked = _query_state()
    if not targets:
        return [], {"targets": 0, "valid_symbols": 0, "failed_symbols": 0,
                    "candidate_cells": 0, "available_cells": 0, "rows": 0}
    token = _token()
    valid = {}
    failed = {}
    symbols = sorted(targets)
    for i, symbol in enumerate(symbols, 1):
        print("  下载 %d/%d %s" % (i, len(symbols), symbol), flush=True)
        try:
            file_name = files.get(symbol) or files.get(symbol.lower())
            _, bars, last_date = _download(token, symbol, file_name)
            ok, reason, cleaned = ingest_validate.check_payload(
                symbol, bars, "total_return", existing=existing.get(symbol),
                min_months=12, allow_stale=True)
            if not ok:
                failed[symbol] = reason
            elif not cleaned:
                failed[symbol] = "empty_after_validation"
            else:
                valid[symbol] = (cleaned, last_date)
        except KaggleRateLimit:
            raise
        except Exception as exc:
            failed[symbol] = str(exc)[:80]
        if i % 5 == 0 or i == len(symbols):
            print("  已处理 %d/%d 个文件" % (i, len(symbols)), flush=True)

    rows = []
    available_cells = 0
    for symbol, (bars, last_date) in valid.items():
        if not any("%d-12" % year in bars for year in targets[symbol]):
            failed[symbol] = "target December absent (archive ends 2017-11)"
            continue
        eligible = [(month, values) for month, values in bars.items()
                    if FIRST_MONTH <= month <= LAST_MONTH and not is_locked(symbol, month)]
        if symbol not in existing_symbols and len(eligible) < 24:
            failed[symbol] = "new symbol needs 24 months"
            continue
        if len(eligible) < 12:
            failed[symbol] = "fewer than 12 unlocked months"
            continue
        available_cells += sum(1 for year in targets[symbol]
                               if "%d-12" % year in bars and not is_locked(symbol, "%d-12" % year))
        for month, (adj, raw) in eligible:
            key = (symbol, month)
            if key in existing_rows or key in staged_rows:
                continue
            rows.append((symbol, month, adj, raw, SOURCE, "ok", "total_return"))

    summary = {
        "targets": len(targets),
        "valid_symbols": len(valid) - sum(1 for s in valid if s in failed),
        "failed_symbols": len(failed),
        "candidate_cells": sum(len(v) for v in targets.values()),
        "available_cells": available_cells,
        "rows": len(rows),
        "errors": failed,
    }
    return rows, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true",
                        help="register source and send validated rows through ingest_staging")
    args = parser.parse_args()
    started = time.monotonic()
    rows, summary = collect()
    print("候选代码: {targets}；校验通过: {valid_symbols}；失败/跳过: {failed_symbols}".format(**summary))
    print("缺失全收益年末候选: {available_cells}/{candidate_cells}；待写月线: {rows}".format(**summary))
    if summary.get("errors"):
        examples = list(summary["errors"].items())[:8]
        print("失败样例:", examples)
    if not args.write or not rows:
        print("只读预览；没有写入 Supabase。" if not args.write else "没有可写入的新行。")
        return

    _register_source()
    pushed = staging_write.push(rows)
    print("写入暂存: %d/%d 行" % (pushed, len(rows)))
    if pushed != len(rows):
        raise SystemExit("暂存只接受了部分行；请检查 ingest_staging 后再重跑。")
    print("已进入 ingest_staging；正式表仅由现有 promote_staging 与锁守卫处理。")
    print("耗时 %.1f 秒" % (time.monotonic() - started))


if __name__ == "__main__":
    main()
