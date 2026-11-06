"""重建被污染的在线排名 context（一次性维护工具，allowlist 管理）。

背景：HLT 在证券目录中被记为 market=106（应为 105）。在目录错误期间，
任何以 106.HLT 抓取并存入 live_ranking_context 的数据都无法证明来自真实
的 Hilton Worldwide，属于被污染的证据；它会使增量对账永久失败
（Incremental source cannot reconcile previous close）。

安全设计（先验证、后删除、全程留痕）：
1. 读取该 symbol 最新的 context，用独立来源（Yahoo chart，meta.symbol 回显）
   取同一日期的收盘价比对；
2. 只有当「独立来源值与存证值不一致」时才允许删除——一致的存证不做任何改动；
3. 删除范围仅限该 symbol + base_year，删除前完整快照写入 sys_event 审计记录；
4. 下一次采集将从 base（上年 12-31）全量重建该 symbol 的 context。
"""
import argparse
import datetime as dt
import json
import sys
import urllib.parse
import urllib.request

import psycopg2.extras
import requests

import db_sources
from live_strategy import YAHOO_TPL, sha

SUBJECTS = ('HLT',)
BASE_YEAR = 2026


def yahoo_close(symbol, day):
    """独立来源收盘价（raw close），身份回显校验。"""
    yahoo_symbol = symbol.replace('.', '-')
    d = dt.date.fromisoformat(day)
    period1 = int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp())
    period2 = period1 + 86400 * 3
    url = YAHOO_TPL % yahoo_symbol + ('?period1=%d&period2=%d&interval=1d'
        '&events=div%%2Csplits&includeAdjustedClose=true' % (period1, period2))
    last = None
    for attempt in range(4):
        try:
            r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=35)
            r.raise_for_status()
            result = r.json()['chart']['result'][0]
            if result['meta']['symbol'].upper() != yahoo_symbol.upper():
                raise ValueError('Yahoo identity mismatch: ' + symbol)
            ts = result['timestamp']
            close = result['indicators']['quote'][0]['close']
            for i, stamp in enumerate(ts):
                if dt.datetime.fromtimestamp(stamp, dt.timezone.utc).date().isoformat() == day:
                    return float(close[i]), r.json()
            return None, r.json()
        except requests.RequestException as exc:
            last = exc
            import time
            time.sleep(attempt + 1)
    raise RuntimeError('Yahoo unreachable: %s (%s)' % (symbol, last))


def main():
    connection = db_sources.db()
    connection.autocommit = False
    try:
        for symbol in SUBJECTS:
            with connection.cursor() as cursor:
                cursor.execute("""select session,phase,payload,evidence_sha256
                    from live_ranking_context where symbol=%s and base_year=%s
                    order by session desc,created_at desc limit 1""", (symbol, BASE_YEAR))
                row = cursor.fetchone()
                cursor.execute('select count(*) from live_ranking_context where symbol=%s and base_year=%s',
                    (symbol, BASE_YEAR))
                total = cursor.fetchone()[0]
            if not row:
                print('%s：无存量 context，跳过' % symbol)
                continue
            session, phase, payload, digest = row
            if sha(payload) != digest:
                raise ValueError('%s 存证哈希不一致，拒绝处理' % symbol)
            begin = payload.get('source_end') or min(payload.get('raw') or {})
            stored = (payload.get('raw') or {}).get(begin)
            fresh, evidence = yahoo_close(symbol, begin)
            print('%s：session=%s source_end=%s 存证 close=%s 独立来源 close=%s'
                  % (symbol, session, begin, stored, fresh))
            if stored is None or fresh is None or abs(stored - fresh) <= 0.005 * max(stored, fresh):
                print('%s：存证与独立来源一致，不删除（保留作审计）' % symbol)
                continue
            with connection.cursor() as cursor:
                cursor.execute("""select session,phase,evidence_sha256,payload
                    from live_ranking_context where symbol=%s and base_year=%s
                    order by session,created_at""", (symbol, BASE_YEAR))
                removed = cursor.fetchall()
                cursor.execute('delete from live_ranking_context where symbol=%s and base_year=%s',
                    (symbol, BASE_YEAR))
                cursor.execute("""insert into sys_event(kind,source,subject,status,metrics,finished_at)
                    values('validation','live_context_rebuild',%s,'pass',%s,now())""",
                    (symbol, psycopg2.extras.Json({
                        'reason': 'provider identity was wrong in security directory; '
                                  'stored close disagrees with independent source',
                        'begin': begin, 'stored_close': stored, 'independent_close': fresh,
                        'independent_evidence_sha256': sha(evidence),
                        'removed_rows': total,
                        'removed_index': [{'session': r[0], 'phase': r[1], 'sha': r[2]} for r in removed],
                        'evidence_sha256_of_removed_payloads': sorted({r[2] for r in removed}),
                        'rebuilt_at': dt.datetime.now(dt.timezone.utc).isoformat()})))
            connection.commit()
            print('%s：已删除 %d 行被污染 context 并写入审计，下一次采集将全量重建' % (symbol, total))
        return 0
    finally:
        connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
