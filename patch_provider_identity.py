"""修正在线证券目录中的 provider 身份（一次性维护工具）。

背景：目录记录 HLT 的 market=106，而东方财富源实际以 105.HLT 返回
（code/market 回显校验一致），导致 live_ranking 的 parse_daily 报
「Daily source identity mismatch」，连续多轮 503/504、硬校验不放行。

安全设计：
1. 每个修正值必须先经源站实时探测验证（rc==0 且 code/market 回显一致），
   验证不过即中止，绝不写库。
2. 只改指定 symbol 的 market 字段，其余记录原样复制，整体记录数不变。
3. 写入新的 pass 记录而非改写历史，旧记录保留作审计留痕。
"""
import datetime as dt
import json
import sys
import urllib.parse
import urllib.request

import psycopg2.extras

import db_sources
from live_strategy import API, sha


def probe(symbol, market):
    """源站验证：secid 必须回显一致的 code/market 且返回有效 K 线。"""
    params = urllib.parse.urlencode({
        'secid': '%d.%s' % (market, symbol), 'klt': 101, 'fqt': 0,
        'beg': '20261001', 'end': '20261231',
        'fields1': 'f1,f2,f3,f4,f5,f6',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'})
    req = urllib.request.Request(
        API + '?' + params,
        headers={'User-Agent': 'Mozilla/5.0',
                 'Referer': 'https://quote.eastmoney.com/'})
    with urllib.request.urlopen(req, timeout=25) as r:
        body = json.load(r)
    data = body.get('data') or {}
    ok = (body.get('rc') == 0 and data.get('code') == symbol
          and data.get('market') == str(market) and data.get('klines'))
    return ok, body


CORRECTIONS = {'HLT': 105}


def main():
    connection = db_sources.db()
    connection.autocommit = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("""select metrics from sys_event
                where source='live_security_directory' and status='pass'
                order by id desc limit 1""")
            row = cursor.fetchone()
        if not row:
            print('FATAL: 无既有目录记录', file=sys.stderr)
            return 1
        body = row[0]['proof']
        if sha(body) != row[0]['evidence_sha256']:
            raise ValueError('现有目录证据哈希不一致，拒绝在未知状态上修正')
        records = body['records']
        index = {r['code']: r for r in records}
        for symbol, market in CORRECTIONS.items():
            record = index.get(symbol)
            if not record:
                print('FATAL: %s 不在目录中' % symbol, file=sys.stderr)
                return 1
            if record['market'] == market:
                print('跳过 %s：market 已是 %d' % (symbol, market))
                continue
            ok, evidence = probe(symbol, market)
            if not ok:
                print('FATAL: 源站验证未通过 %s -> market=%d' % (symbol, market),
                      file=sys.stderr)
                return 1
            old = record['market']
            record['market'] = market
            print('已验证并修正 %s: market %s -> %d' % (symbol, old, market))
        body['patched_at'] = dt.datetime.now(
            dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        body['patch_note'] = 'provider identity correction: ' + ', '.join(
            '%s->%d' % kv for kv in sorted(CORRECTIONS.items()))
        with connection.cursor() as cursor:
            cursor.execute("""insert into sys_event(kind,source,subject,status,metrics,finished_at)
                values('validation','live_security_directory','current','pass',%s,now())""",
                (psycopg2.extras.Json(
                    {'proof': body, 'evidence_sha256': sha(body)}),))
        connection.commit()
        print('目录修正已写入（新 pass 记录）')
        return 0
    finally:
        connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
