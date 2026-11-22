import contextlib
import decimal
import io
import sqlite3
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

import db_prices
import fetch_stockanalysis as stockanalysis
import fill_yahoo_gaps as backfill
import lock_symbols
import lock_stage
import validate_prices as validate
import verify_locks
import yahoo_fetch


class Cursor:
    def __init__(self, connection):
        self.cursor = connection.cursor()

    def execute(self, sql, params):
        self.cursor.execute(sql.replace('%s', '?'), params)

    def fetchall(self):
        return self.cursor.fetchall()

    def fetchone(self):
        return self.cursor.fetchone()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class Connection:
    """fill_yahoo_gaps 用 conn.cursor() 自己开游标，这里套同一层占位符翻译。"""

    def __init__(self, connection):
        self.connection = connection

    def cursor(self):
        return Cursor(self.connection)

    def close(self):
        """故意不真关：内存库由 addCleanup 统一关，测试还要在 main() 之后回读台账。"""


SCHEMA = '''
            create table series_pick(symbol text, basis text, frozen boolean);
            create table members(symbol text, start_date text, end_date text);
            create table prices_monthly(symbol text, source text, basis text, month text);
            create table series_selected(symbol text, month text);
            create table locked_symbols(symbol text);
            create table series_quarantine(symbol text);
            create table data_lock(id integer, symbol text, scope text, status text, supersedes_id integer);
            create table source_absence_acceptance(symbol text not null, source text not null,
                                                   basis text not null,
                                                   accepted_basis text not null default 'price_only'
                                                     check(accepted_basis = 'price_only'),
                                                   reason text not null,
                                                   http_status integer not null,
                                                   membership_end text not null,
                                                   observed_on text not null default '1970-01-01',
                                                   runner text not null,
                                                   check((reason = 'provider_not_found'
                                                          and http_status between 400 and 499)
                                                     or (reason = 'provider_no_window_history'
                                                          and http_status = 200)),
                                                   primary key(symbol, source, basis));
'''
# source_absence_acceptance 的列、not null 与 check 逐条照抄 sql/127 + sql/128：2026-11-22 的
# CI 现场就是夹具少写一列 reason，close_out_absence 的 insert 缺 NOT NULL 直接把整轮
# 补采炸掉，而这道题在本地一直是「绿」的。

SYMBOLS = ['OPEN', 'TR', 'FROZEN', 'LEGACY', 'LOCKED', 'VOIDED', 'QUARANTINED', 'EXISTING']


class HistoricalBackfillGuards(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.addCleanup(self.connection.close)
        self.connection.executescript(SCHEMA)
        self.connection.executemany('insert into series_pick values(?,?,?)',
            [(s, 'total_return' if s == 'TR' else 'price_only', s == 'FROZEN') for s in SYMBOLS])
        self.connection.executemany('insert into members values(?,?,?)',
                                    [(s, '1996-01-01', '2099-12-31') for s in SYMBOLS])
        self.connection.execute("insert into locked_symbols values('LEGACY')")
        self.connection.execute("insert into series_quarantine values('QUARANTINED')")
        self.connection.execute("insert into prices_monthly values('EXISTING','yahoo_relay','total_return','2025-12')")
        self.connection.executemany('insert into data_lock values(?,?,?,?,?)',
            [(1, 'LOCKED', 'symbol', 'locked', None), (2, 'VOIDED', 'symbol_month', 'locked', None),
             (3, 'VOIDED', 'symbol_month', 'void', 2)])

    def cursor(self):
        return Cursor(self.connection)

    def test_actual_target_query_excludes_protected_and_quarantined_history(self):
        cursor = self.cursor()
        self.assertEqual(backfill.targets(cursor, 100), ['OPEN', 'VOIDED'])
        self.assertEqual(backfill.targets(cursor, 1, 'OPEN'), ['OPEN'])
        self.connection.execute("insert into data_lock values(4,null,'month_range','locked',null)")
        self.assertEqual(backfill.targets(cursor, 100), [])
        self.assertEqual(backfill.targets(cursor, 1, 'OPEN'), [])
        self.connection.execute("insert into data_lock values(5,null,'month_range','void',4)")
        self.assertEqual(backfill.targets(cursor, 100), ['OPEN', 'VOIDED'])

    def test_absence_is_closed_out_only_for_a_confirmed_delisting(self):
        # 2026-11-21 口径：退市票 Yahoo 明确 404 → 接受 price_only 并结案。
        # 现役成分股的 404 更可能是换号或供应商故障，结案等于把它永久逐出
        # 全收益补采，所以必须拒绝。库里没有的成员同样拒绝。
        self.connection.executemany('insert into members values(?,?,?)',
                                    [('GONE', '1996-01-01', '2020-06-30'),
                                     ('NOENTRY', '1996-01-01', '2020-06-30')])
        self.connection.execute("delete from members where symbol='NOENTRY'")
        self.connection.execute("insert into series_pick values('GONE','price_only',0)")
        self.connection.execute("insert into series_pick values('NOENTRY','price_only',0)")
        connection = Connection(self.connection)
        self.assertTrue(backfill.close_out_absence(connection, 'GONE'))
        self.assertFalse(backfill.close_out_absence(connection, 'OPEN'))
        self.assertFalse(backfill.close_out_absence(connection, 'NOENTRY'))
        self.assertEqual([r[0] for r in self.connection.execute(
            "select symbol from source_absence_acceptance")], ['GONE'])
        # 结案的整条裁决必须逐列落进 sql/127 要求的形状：缺席原因写成自由文本，
        # 台账就退化成一句日志，事后无从区分「404」与「起点晚于窗口」。
        self.assertEqual(self.connection.execute(
            "select accepted_basis, reason, http_status, membership_end, runner"
            " from source_absence_acceptance where symbol='GONE'").fetchone(),
            ('price_only', 'provider_not_found', 404, '2020-06-30', 'fill_yahoo_gaps'))

    def test_closed_out_absence_stops_retrying_without_shielding_a_live_member(self):
        cursor = self.cursor()
        self.connection.execute("insert into members values('GONE','1996-01-01','2020-06-30')")
        self.connection.execute("insert into series_pick values('GONE','price_only',0)")
        self.assertIn('GONE', backfill.targets(cursor, 100))
        backfill.close_out_absence(Connection(self.connection), 'GONE')
        self.assertNotIn('GONE', backfill.targets(cursor, 100))
        # 结案不删任何东西：OPEN 若被误插一条缺席记录，因为退市并不成立，
        # 它必须照样出现在补采集合里（记录不需要撤销）。
        self.connection.execute(
            "insert into source_absence_acceptance(symbol, source, basis, reason, http_status,"
            " membership_end, runner) values"
            "('OPEN','yahoo_relay','total_return','provider_not_found',404,'2099-12-31','test')")
        self.assertIn('OPEN', backfill.targets(cursor, 100))
        # 成员重新上市（end_date 被复核改期）同样自动回到补采集合。
        self.connection.execute("update members set end_date='2030-12-31' where symbol='GONE'")
        self.assertIn('GONE', backfill.targets(cursor, 100))

    def test_request_rechecks_protection_and_never_fetches_after_database_failure(self):
        for result in ([], RuntimeError('database unavailable')):
            with self.subTest(result=result), patch('sys.argv', ['fill_yahoo_gaps.py']), \
                 patch.object(backfill, 'db', return_value=MagicMock()), \
                 patch.object(backfill, 'targets', side_effect=[['OPEN'], result]), \
                 patch.object(yahoo_fetch, 'fetch') as fetch, contextlib.redirect_stdout(io.StringIO()):
                if isinstance(result, Exception):
                    with self.assertRaisesRegex(RuntimeError, 'database unavailable'):
                        backfill.main()
                else:
                    backfill.main()
                fetch.assert_not_called()

    def test_genuine_failure_is_fatal_but_the_time_budget_is_not(self):
        with patch('sys.argv', ['fill_yahoo_gaps.py']), \
             patch.object(backfill, 'db', return_value=MagicMock()), \
             patch.object(backfill, 'targets', return_value=['OPEN']), \
             patch.object(yahoo_fetch, 'fetch', side_effect=RuntimeError('offline')), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, '1 只真故障'):
                backfill.main()
        # 预算用完是设计内的续跑，不是故障。过去它和真故障共用同一句
        # RuntimeError，174 只 × 每只十几秒必超 900s，于是这条门禁永远红着。
        with patch('sys.argv', ['fill_yahoo_gaps.py', '--max-seconds', '1']), \
             patch.object(backfill, 'db', return_value=MagicMock()), \
             patch.object(backfill, 'targets', return_value=['OPEN']), \
             patch.object(backfill.time, 'time', side_effect=[0, 2, 3]), \
             patch.object(yahoo_fetch, 'fetch') as fetch, \
             contextlib.redirect_stdout(io.StringIO()) as out:
            backfill.main()
            fetch.assert_not_called()
        self.assertIn('剩余 1 只未跑', out.getvalue())
        self.assertNotIn('真故障', out.getvalue())

    def test_closed_out_absence_does_not_keep_the_backfill_red(self):
        # 结案的全部意义：退市票的权威缺席不是故障，作业必须能变绿，
        # 否则天天红着的门禁和天天绿着一样没有信息量。
        with patch('sys.argv', ['fill_yahoo_gaps.py']), \
             patch.object(backfill, 'db', return_value=MagicMock()), \
             patch.object(backfill, 'targets', return_value=['GONE']), \
             patch.object(backfill, 'close_out_absence', return_value=True) as closed, \
             patch.object(yahoo_fetch, 'fetch', side_effect=yahoo_fetch.SymbolNotFound('GONE')), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            backfill.main()
        closed.assert_called_once()
        self.assertIn('结案缺席 1', out.getvalue())

    def test_targets_exclude_membership_too_short_to_be_fillable(self):
        # 需要窗口（2012-01..2025-12）内成员月份不足 12 个月的，任何源都补不出
        # 12 个月的窗口内历史：2026-11-22 实测这类票占 Yahoo 队列的 11/117、
        # stockanalysis 队列的 9/53，过去每轮白打一次。
        cursor = self.cursor()
        self.connection.execute("insert into members values('PRE2012','1996-01-01','2011-06-30')")
        self.connection.execute("insert into series_pick values('PRE2012','price_only',0)")
        self.assertNotIn('PRE2012', backfill.targets(cursor, 100))
        # 身份复核把离场时间推进窗口后，它自己回到补采集合，不靠删记录。
        self.connection.execute("update members set end_date='2013-06-30' where symbol='PRE2012'")
        self.assertIn('PRE2012', backfill.targets(cursor, 100))

    def _collect_one(self, symbol, months):
        """用真实 sqlite 台账跑一遍主循环，返回 (stdout, 异常文案)。"""
        self.connection.execute("insert into members values(?,?,?)",
                                (symbol, '1996-01-01', '2019-08-09'))
        self.connection.execute("insert into series_pick values(?, 'price_only', 0)", (symbol,))
        bars = dict.fromkeys(months, (10.0, 10.0))
        with patch('sys.argv', ['fill_yahoo_gaps.py']), \
             patch.object(backfill, 'db', return_value=Connection(self.connection)), \
             patch.object(backfill, 'targets', return_value=[symbol]), \
             patch.object(backfill.time, 'sleep'), \
             patch.object(yahoo_fetch, 'fetch', return_value=bars), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            try:
                backfill.main()
                return out.getvalue(), ''
            except RuntimeError as exc:
                return out.getvalue(), str(exc)

    def test_source_only_after_relisting_is_closed_out_as_absent(self):
        # 2026-11-22 实测 APC(2019-08 退市) 只回 [2026-02..2026-11]：代码已被
        # 重新上市占用，本标的那段历史不在这个源里，重试多少次都不会多一根。
        printed, error = self._collect_one('RELIST', ['2026-0%d' % i for i in range(2, 7)])
        self.assertEqual(error, '', printed)
        self.assertIn('结案身份不匹配 1', printed)
        self.assertIn('全部晚于最后交易日 2019-08-09', printed)
        # 这条裁决不写台账：证据每轮重取，成本只是几只票的几秒；写台账要放宽
        # sql/127 的 reason 约束，而那里的 append-only 承诺不该为省几次请求让路。
        self.assertEqual(self.connection.execute(
            "select count(*) from source_absence_acceptance").fetchone()[0], 0)

    def test_truncation_inside_the_identity_lifetime_stays_fatal(self):
        # 同一只票若回到的月份落在它自己的生命周期内却仍然不足，那是通道截断，
        # 结案等于永久放弃一次本可以补上的历史——必须继续报红。
        printed, error = self._collect_one('CHOPPED', ['2016-0%d' % i for i in range(1, 6)])
        self.assertIn('1 只真故障', error, printed)
        self.assertIn('✗ CHOPPED 月份不足（5）', printed)

    def test_absence_404_on_a_still_listed_member_stays_fatal(self):
        with patch('sys.argv', ['fill_yahoo_gaps.py']), \
             patch.object(backfill, 'db', return_value=MagicMock()), \
             patch.object(backfill, 'targets', return_value=['OPEN']), \
             patch.object(backfill, 'close_out_absence', return_value=False), \
             patch.object(yahoo_fetch, 'fetch', side_effect=yahoo_fetch.SymbolNotFound('OPEN')), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, '1 只真故障'):
                backfill.main()

    def _fill_window(self, symbol, start='1996-01-01', end='2019-08-09'):
        """把「窗口 ∩ 在册区间」的月份铺进 series_selected，返回铺出的月份。"""
        first, last = max('2012-01', start[:7]), min('2025-12', end[:7])
        months, y, m = [], int(first[:4]), int(first[5:7])
        while '%04d-%02d' % (y, m) <= last:
            months.append('%04d-%02d' % (y, m))
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        self.connection.executemany('insert into series_selected values(?,?)',
                                    [(symbol, x) for x in months])
        self.connection.execute('insert into members values(?,?,?)', (symbol, start, end))
        self.connection.execute("insert into series_pick values(?, 'price_only', 0)", (symbol,))
        return months

    def test_covered_window_closes_without_a_provider_verdict(self):
        # 2026-11-22 现场：补采集合 43 只全部已退市，其中 41 只窗口内一个月都不缺。
        # CA/CTRA/FI 是整群通道回空、EQR 只回重新上市后的两根——它们缺的从来不是
        # 数据，而是「再试一次也拿不到的口径升级」。
        connection = Connection(self.connection)
        self._fill_window('COVERED')
        self.assertIn('COVERED', backfill.targets(self.cursor(), 100))
        self.assertTrue(backfill.absence.close_covered_absence(
            connection, 'COVERED', 'yahoo_relay', 'fill_yahoo_gaps'))
        self.assertEqual(self.connection.execute(
            "select accepted_basis, reason, http_status, membership_end, runner"
            " from source_absence_acceptance where symbol='COVERED'").fetchone(),
            ('price_only', 'provider_no_window_history', 200, '2019-08-09', 'fill_yahoo_gaps'))
        # 结案必须真的停止重试，否则这条判据只是把红换成白烧流量。
        self.assertNotIn('COVERED', backfill.targets(self.cursor(), 100))

    def test_a_real_hole_or_a_live_member_refuses_the_coverage_closure(self):
        # LIFE 窗口内 0 个月、TGNA 66 个月只有 25 个：真有缺月的票绝不能顺着这条
        # 判据结案，否则门禁最该响的那一类被永久消音。现役成分股同理。
        connection = Connection(self.connection)
        self._fill_window('HOLEY')
        self.connection.execute("delete from series_selected where symbol='HOLEY' and month='2015-06'")
        self.assertFalse(backfill.absence.close_covered_absence(
            connection, 'HOLEY', 'yahoo_relay', 'fill_yahoo_gaps'))
        self._fill_window('LIVE', end='2099-12-31')
        self.assertFalse(backfill.absence.close_covered_absence(
            connection, 'LIVE', 'yahoo_relay', 'fill_yahoo_gaps'))
        self.assertEqual(self.connection.execute(
            "select count(*) from source_absence_acceptance").fetchone()[0], 0)

    def test_the_ledger_still_refuses_to_launder_a_relay_outage(self):
        # 结案形状由 sql/128 钉死：200 只能配「窗口已完整」，4xx 只能配 Not Found。
        # 通道 403/429 想借结案躲开重试，会先把约束撞断。
        for reason, status in (('provider_no_window_history', 403),
                               ('provider_not_found', 200),
                               ('我懒得再看第二眼', 200)):
            with self.subTest(reason=reason, status=status), self.assertRaises(sqlite3.IntegrityError):
                self.connection.execute(
                    "insert into source_absence_acceptance(symbol, source, basis, reason,"
                    " http_status, membership_end, runner) values"
                    "('X','yahoo_relay','total_return',?,?, '2020-06-30','test')",
                    (reason, status))

    def _empty_relay_group(self, symbol):
        """整群通道回空（Yahoo 没回答过、也没给出一根）跑一遍主循环。"""
        with patch('sys.argv', ['fill_yahoo_gaps.py']), \
             patch.object(backfill, 'db', return_value=Connection(self.connection)), \
             patch.object(backfill, 'targets', return_value=[symbol]), \
             patch.object(backfill.time, 'sleep'), \
             patch.object(yahoo_fetch, 'fetch', return_value={}), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            try:
                backfill.main()
                return out.getvalue(), ''
            except RuntimeError as exc:
                return out.getvalue(), str(exc)

    def test_empty_relay_group_on_a_complete_window_does_not_keep_the_job_red(self):
        self._fill_window('COVERED')
        printed, error = self._empty_relay_group('COVERED')
        self.assertEqual(error, '', printed)
        self.assertIn('结案窗口已完整 1', printed)

    def test_empty_relay_group_with_a_real_hole_stays_fatal(self):
        self._fill_window('HOLEY')
        self.connection.execute("delete from series_selected where symbol='HOLEY' and month='2015-06'")
        printed, error = self._empty_relay_group('HOLEY')
        self.assertIn('1 只真故障', error, printed)
        self.assertIn('✗ HOLEY', printed)

    def test_truncated_source_on_a_complete_window_does_not_keep_the_job_red(self):
        # EQR 的形状：源回 2026-07..08（晚于窗口末月、又不晚于最后交易日），
        # 校验按「月份不足」拒绝——窗口内一个月都不缺，所以是结案而不是故障。
        self._fill_window('COVERED')
        bars = {m: (10.0, 10.0) for m in ('2026-07', '2026-08')}
        with patch('sys.argv', ['fill_yahoo_gaps.py']), \
             patch.object(backfill, 'db', return_value=Connection(self.connection)), \
             patch.object(backfill, 'targets', return_value=['COVERED']), \
             patch.object(backfill.time, 'sleep'), \
             patch.object(yahoo_fetch, 'fetch', return_value=bars), \
             patch.object(backfill.absence, 'belongs_to_later_identity', return_value=False), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            backfill.main()
        printed = out.getvalue()
        self.assertIn('结案窗口已完整 1', printed)
        self.assertNotIn('真故障', printed)

    def test_stockanalysis_400_closes_only_a_confirmed_delisting(self):
        # stockanalysis 对退市代码回 400，那就是它的 Not Found。这个步骤过去挂着
        # continue-on-error：2026-11-22 成功 0 / 失败 67 仍然报绿，门禁等于不存在。
        # 现在 400 走同一张结案台账（能结案就绿），现役标的的 400 必须报红。
        error = urllib.error.HTTPError('https://stockanalysis.com/api/x', 400,
                                       'Bad Request', {}, None)
        self.connection.execute("insert into members values('GONE','1996-01-01','2020-06-30')")
        self.connection.execute("insert into series_pick values('GONE','price_only',0)")
        printed = io.StringIO()
        for symbol, closable in (('GONE', True), ('OPEN', False)):
            with self.subTest(symbol=symbol), \
                 patch('sys.argv', ['fetch_stockanalysis.py', '--ticker', symbol]), \
                 patch.object(stockanalysis, 'db', return_value=Connection(self.connection)), \
                 patch.object(stockanalysis, 'fetch', side_effect=error), \
                 patch.object(db_prices, 'log_run'), \
                 contextlib.redirect_stdout(printed):
                if closable:
                    stockanalysis.main()
                else:
                    with self.assertRaisesRegex(RuntimeError, '1 只真故障'):
                        stockanalysis.main()
        text = printed.getvalue()
        self.assertIn('∅ GONE', text)
        self.assertIn('✗ OPEN', text)
        self.assertEqual(self.connection.execute(
            "select source, reason, http_status, membership_end, runner"
            " from source_absence_acceptance where symbol='GONE'").fetchone(),
            ('stockanalysis', 'provider_not_found', 400, '2020-06-30', 'fetch_stockanalysis'))
        self.assertEqual(self.connection.execute(
            "select count(*) from source_absence_acceptance where symbol='OPEN'").fetchone()[0], 0)

    def test_rate_limiting_is_never_mistaken_for_a_not_found_verdict(self):
        self.assertEqual(stockanalysis._not_found_status(
            urllib.error.HTTPError('u', 404, 'Not Found', {}, None)), 404)
        self.assertEqual(stockanalysis._not_found_status(
            urllib.error.HTTPError('u', 429, 'Too Many Requests', {}, None)), None)
        self.assertEqual(stockanalysis._not_found_status(
            urllib.error.HTTPError('u', 403, 'Forbidden', {}, None)), None)
        self.assertEqual(stockanalysis._not_found_status(RuntimeError('timed out')), None)

    def test_legacy_coverage_lock_refuses_before_any_database_access(self):
        with patch('sys.argv', ['lock_symbols.py', '--lock']), \
             patch.object(lock_symbols, 'db') as database, \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                lock_symbols.main()
            self.assertEqual(error.exception.code, 2)
            database.assert_not_called()

    def test_even_a_passing_coverage_check_cannot_create_a_historical_lock(self):
        with patch.object(lock_stage, 'db') as database, \
             patch.object(lock_stage, 'check', return_value=(0, {'hash': 'unreviewed'})) as check:
            cursor = database.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = None
            for force in (False, True):
                with self.subTest(force=force), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(lock_stage.lock(2010, 2025, force=force), 1)
            check.assert_not_called()
            self.assertEqual(cursor.execute.call_count, 2)
            self.assertTrue(all(call.args[0].lstrip().startswith('select') for call in cursor.execute.call_args_list))

    def test_read_only_lock_check_never_creates_missing_schema(self):
        with patch.object(lock_stage, 'db') as database:
            cursor = database.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = (False,)
            with self.assertRaisesRegex(RuntimeError, '只读诊断'):
                lock_stage.ensure_table()
            cursor.execute.assert_called_once()
            self.assertTrue(cursor.execute.call_args.args[0].startswith('select '))

    def test_empty_lock_list_cannot_claim_all_history_was_verified(self):
        output = io.StringIO()
        with patch('sys.argv', ['verify_locks.py']), \
             patch.object(verify_locks, 'db') as database, contextlib.redirect_stdout(output):
            cursor = database.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchall.return_value = []
            self.assertEqual(verify_locks.main(), 0)
        self.assertIn('未验证历史数据', output.getvalue())
        self.assertNotIn('所有锁有效', output.getvalue())


class PriceAcceptanceGuards(unittest.TestCase):
    """validate_prices 的 5 条验收改读库内 series_selected 后的回归。

    放在本文件而不是新建模块：CI 用显式模块列表跑测试（collect-full.yml:46、
    identity-generation.yml:70），新文件不会被任何作业执行，等于没有测试。
    """

    SCHEMA = '''
            create table series_selected(symbol text, month text, adj_close real,
                                         primary key(symbol, month));
            create table series_pick(symbol text, source text);
            create table members(symbol text, end_date text);
    '''

    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.addCleanup(self.connection.close)
        self.connection.executescript(self.SCHEMA)
        # 10 只 2009 年后仍在场的成分 + 1 只 2008 年就退出的（不该计入覆盖率）
        for symbol in ['NVDA', 'DUK', 'AMCR'] + ['S%d' % i for i in range(7)]:
            self.connection.execute("insert into members values(?, '2099-12-31')", (symbol,))
        self.connection.execute("insert into members values('GONE','2008-06-30')")
        self.connection.execute("insert into series_pick values('NVDA','alphavantage')")
        for symbol in ['S%d' % i for i in range(7)]:
            self.connection.execute('insert into series_selected values(?,?,?)',
                                    (symbol, '2024-12', 10.0))
        self.pick_healthy()

    def cursor(self):
        return self.connection.cursor()

    def ramp(self, symbol, start, end, first, last):
        """按月几何插值：验收数据要真实到能同时通过连续性，又不能靠少插行糊过去。"""
        y0, m0, y1, m1 = int(start[:4]), int(start[5:]), int(end[:4]), int(end[5:])
        steps = (y1 - y0) * 12 + (m1 - m0)
        for i in range(steps + 1):
            index = m0 - 1 + i
            month = '%04d-%02d' % (y0 + index // 12, index % 12 + 1)
            self.connection.execute('insert or ignore into series_selected values(?,?,?)',
                                    (symbol, month, first * (last / first) ** (i / steps)))

    def pick_healthy(self):
        # NVDA：2021 年 +125%、2024 年 +171%（年度之间用月线爬升衔接）
        self.ramp('NVDA', '2020-12', '2021-12', 100.0, 225.0)
        self.ramp('NVDA', '2021-12', '2023-12', 225.0, 100.0)
        self.ramp('NVDA', '2023-12', '2024-12', 100.0, 271.0)
        # DUK：公用事业单月小幅波动；AMCR：2025-12→最新 +15%
        self.ramp('DUK', '2012-06', '2012-07', 100.0, 101.0)
        self.ramp('AMCR', '2025-12', '2026-10', 100.0, 115.0)

    def run_accept(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            bad = validate.accept(self.cursor())
        return bad, output.getvalue()

    def test_selected_series_passes_all_five_criteria(self):
        bad, printed = self.run_accept()
        self.assertEqual(bad, 0, printed)
        self.assertIn('覆盖率 100.0%（10/10）', printed)   # 2008 年退出的不计入需要集
        self.assertIn('验收：通过 6 · 失败 0', printed)

    def test_no_rows_can_never_pass_as_continuous(self):
        # 这条是改库内口径的动机本身：空样本过去打印「占比 0.000%」看着像通过。
        self.connection.execute('delete from series_selected')
        bad, printed = self.run_accept()
        self.assertEqual(bad, 6, printed)
        self.assertIn('样本为空（0 段）', printed)
        self.assertNotIn('✅ 连续性', printed)

    def test_unadjusted_split_breakpoint_still_fails_duk(self):
        self.connection.execute("delete from series_selected where symbol='DUK'")
        self.connection.executemany('insert into series_selected values(?,?,?)',
                                    [('DUK', '2012-06', 100.0), ('DUK', '2012-07', 50.0)])
        bad, printed = self.run_accept()
        self.assertEqual(bad, 1, printed)
        self.assertIn('❌ DUK 2012-07 单月 -50.00%', printed)

    def test_reverse_split_inflated_gain_still_fails_amcr(self):
        self.connection.execute("delete from series_selected where symbol='AMCR'")
        self.ramp('AMCR', '2025-12', '2026-10', 100.0, 568.0)
        bad, printed = self.run_accept()
        self.assertEqual(bad, 1, printed)
        self.assertIn('疑似反向拆股未处理', printed)


class DecimalSpotRegression(unittest.TestCase):
    """生产列类型是 numeric 时验收必须照样算得完。

    2026-11-22 拿生产库真跑 validate_prices 才暴露：psycopg2 把 adj_close 回成
    Decimal，与 float 阈值相减直接 TypeError，验收连一条判定都没打印就崩。
    sqlite 夹具的列是 real，永远复现不出来——所以这里不接 sqlite，直接用假游标
    按 psycopg2 的形状喂 Decimal，把「值类型」这条边界钉住。
    """

    D = decimal.Decimal

    class Cur:
        def __init__(self, answers):
            self.answers = answers
            self.rows = None

        def execute(self, sql):
            self.rows = self.answers[sql]

        def fetchall(self):
            return self.rows

        def fetchone(self):
            return self.rows

    def answers(self, spot, have=10, need=10, auth=1, total=1000, jumps=0):
        return {validate.SPOT_SQL: spot, validate.COVERAGE_SQL: (have, need),
                validate.AUTHORITATIVE_SQL: (auth,), validate.CONTINUITY_SQL: (total, jumps)}

    def healthy_spot(self):
        return [('NVDA', '2020-12', self.D('10.00')), ('NVDA', '2021-12', self.D('22.50')),
                ('NVDA', '2023-12', self.D('10.00')), ('NVDA', '2024-12', self.D('27.10')),
                ('DUK', '2012-06', self.D('10.00')), ('DUK', '2012-07', self.D('10.02')),
                ('AMCR', '2025-12', self.D('10.00')), ('AMCR', '2026-10', self.D('12.00'))]

    def test_acceptance_completes_when_driver_returns_decimal(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bad = validate.accept(self.Cur(self.answers(self.healthy_spot())))
        printed = out.getvalue()
        self.assertEqual(bad, 0, printed)
        self.assertIn('+125%', printed)

    def test_decimal_thresholds_still_reject_a_bad_gain(self):
        spot = self.healthy_spot()
        spot[1] = ('NVDA', '2021-12', self.D('90.00'))      # +800%，远超 1.25±0.12
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bad = validate.accept(self.Cur(self.answers(spot)))
        self.assertEqual(bad, 1, out.getvalue())
        self.assertIn('偏差过大', out.getvalue())


if __name__ == '__main__':
    unittest.main()
