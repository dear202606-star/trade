"""Audit live December PIT total-return coverage; never change source picks."""
import argparse
import datetime as dt
import json
import math
from pathlib import Path

import db_sources


def audit():
    with db_sources.db() as connection, connection.cursor() as cursor:
        cursor.execute("""
            with need as (
              select distinct m.symbol, y as year, to_char(make_date(y,12,31),'YYYY-MM') as month
              from members m cross join generate_series(2010,2025) y
              where m.start_date <= make_date(y,12,31) and m.end_date >= make_date(y,12,31)
            )
            select n.symbol,n.year,s.source,s.basis,
                   exists(select 1 from prices_monthly a where a.symbol=n.symbol
                          and a.month=n.month and a.source in ('kaggle_cc0','kaggle_yahoo2020')) as archive_candidate,
                   exists(select 1 from locked_symbols l where l.symbol=n.symbol) as locked,
                   exists(select 1 from series_quarantine q where q.symbol=n.symbol) as quarantined,
                   array(select distinct a.source from prices_monthly a
                         where a.symbol=n.symbol and a.month=n.month and a.basis='total_return'
                           and a.quality='ok' and a.source not in ('kaggle_cc0','kaggle_yahoo2020')
                           and not exists(select 1 from series_quarantine q where q.symbol=n.symbol)
                         order by a.source) as existing_tr_candidates,
                   array(select a.source from prices_monthly a where a.symbol=n.symbol and a.month=n.month
                         and a.source in ('kaggle_cc0','kaggle_yahoo2020') order by a.source) as archive_sources
            from need n left join series_selected s on s.symbol=n.symbol and s.month=n.month
            order by n.year,n.symbol
        """)
        rows = cursor.fetchall()
        cursor.execute("select count(*) from series_pick where source='kaggle_cc0'")
        archive_picks = cursor.fetchone()[0]
    if not rows:
        raise RuntimeError("PIT universe is empty")
    total_return = sum(row[3] == 'total_return' for row in rows)
    gaps = [{"symbol": symbol, "year": year, "source": source,
             "reason": "no_data" if source is None else "price_only",
             "archive_candidate_unreviewed": candidate, "locked": locked, "quarantined": quarantined,
             "existing_tr_candidates_unreviewed": alternatives,
             "archive_sources_unreviewed": archive_sources}
            for symbol, year, source, basis, candidate, locked, quarantined, alternatives, archive_sources in rows
            if basis != 'total_return']
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "definition": "2010-2025, member on December 31, selected December basis=total_return",
        "need": len(rows), "total_return": total_return,
        "coverage": total_return / len(rows),
        "additional_to_95pct": max(0, math.ceil(len(rows) * .95) - total_return),
        "no_data": sum(g['reason'] == 'no_data' for g in gaps),
        "price_only": sum(g['reason'] == 'price_only' for g in gaps),
        "unreviewed_archive_alternatives": sum(g['archive_candidate_unreviewed'] for g in gaps),
        "unreviewed_cc0_2017_alternatives": sum('kaggle_cc0' in g['archive_sources_unreviewed'] for g in gaps),
        "unreviewed_yahoo2020_alternatives": sum('kaggle_yahoo2020' in g['archive_sources_unreviewed'] for g in gaps),
        "archive_selected": archive_picks,
        "quarantined_gap_cells": sum(g['quarantined'] for g in gaps),
        "existing_tr_candidate_cells": sum(bool(g['existing_tr_candidates_unreviewed']) for g in gaps),
        "gaps": gaps,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = audit()
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in result.items() if key != 'gaps'}, ensure_ascii=False, indent=2))
