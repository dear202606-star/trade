import contextlib
import io
import sqlite3
import unittest
from unittest.mock import MagicMock, patch

import fill_yahoo_gaps as backfill
import lock_symbols
import lock_stage
import verify_locks
import yahoo_fetch


class Cursor:
    def __init__(self, connection):
        self.cursor = connection.cursor()

    def execute(self, sql, params):
        self.cursor.execute(sql.replace('%s', '?'), params)

    def fetchall(self):
        return self.cursor.fetchall()


class HistoricalBackfillGuards(unittest.TestCase):
    def test_actual_target_query_excludes_protected_and_quarantined_history(self):
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.executescript('''
            create table series_pick(symbol text, basis text, frozen boolean);
            create table members(symbol text, end_date text);
            create table prices_monthly(symbol text, source text, basis text, month text);
            create table locked_symbols(symbol text);
            create table series_quarantine(symbol text);
            create table data_lock(id integer, symbol text, scope text, status text, supersedes_id integer);
        ''')
        symbols = ['OPEN', 'TR', 'FROZEN', 'LEGACY', 'LOCKED', 'VOIDED', 'QUARANTINED', 'EXISTING']
        connection.executemany('insert into series_pick values(?,?,?)',
            [(s, 'total_return' if s == 'TR' else 'price_only', s == 'FROZEN') for s in symbols])
        connection.executemany('insert into members values(?,?)', [(s, '2099-12-31') for s in symbols])
        connection.execute("insert into locked_symbols values('LEGACY')")
        connection.execute("insert into series_quarantine values('QUARANTINED')")
        connection.execute("insert into prices_monthly values('EXISTING','yahoo_relay','total_return','2025-12')")
        connection.executemany('insert into data_lock values(?,?,?,?,?)',
            [(1, 'LOCKED', 'symbol', 'locked', None), (2, 'VOIDED', 'symbol_month', 'locked', None),
             (3, 'VOIDED', 'symbol_month', 'void', 2)])
        cursor = Cursor(connection)
        self.assertEqual(backfill.targets(cursor, 100), ['OPEN', 'VOIDED'])
        self.assertEqual(backfill.targets(cursor, 1, 'OPEN'), ['OPEN'])
        connection.execute("insert into data_lock values(4,null,'month_range','locked',null)")
        self.assertEqual(backfill.targets(cursor, 100), [])
        self.assertEqual(backfill.targets(cursor, 1, 'OPEN'), [])
        connection.execute("insert into data_lock values(5,null,'month_range','void',4)")
        self.assertEqual(backfill.targets(cursor, 100), ['OPEN', 'VOIDED'])

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

    def test_failed_collection_and_time_budget_do_not_report_success(self):
        with patch('sys.argv', ['fill_yahoo_gaps.py']), \
             patch.object(backfill, 'db', return_value=MagicMock()), \
             patch.object(backfill, 'targets', return_value=['OPEN']), \
             patch.object(yahoo_fetch, 'fetch', side_effect=RuntimeError('offline')), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, '1 failed, 0 unattempted'):
                backfill.main()
        with patch('sys.argv', ['fill_yahoo_gaps.py', '--max-seconds', '1']), \
             patch.object(backfill, 'db', return_value=MagicMock()), \
             patch.object(backfill, 'targets', return_value=['OPEN']), \
             patch.object(backfill.time, 'time', side_effect=[0, 2, 3]), \
             patch.object(yahoo_fetch, 'fetch') as fetch, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, '0 failed, 1 unattempted'):
                backfill.main()
            fetch.assert_not_called()

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


if __name__ == '__main__':
    unittest.main()
