import sqlite3
import unittest
from unittest.mock import MagicMock, patch

import db_select


class SourceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.addCleanup(self.connection.close)
        self.connection.execute('create table prices_monthly(symbol text, source text, basis text, month text)')
        self.connection.execute('create table series_quarantine(symbol text)')

    def add(self, symbol, source, basis, count):
        self.connection.executemany('insert into prices_monthly values(?,?,?,?)',
            [(symbol, source, basis, '%d-%02d' % (2012 + n // 12, n % 12 + 1))
             for n in range(count)])

    def test_archive_coverage_cannot_override_reviewed_source(self):
        self.add('ARNC', 'kaggle_yahoo2020', 'total_return', 168)
        self.add('ARNC', 'stockanalysis', 'total_return', 12)
        self.add('REUSED', 'kaggle_cc0', 'total_return', 168)
        self.add('REUSED', 'eodhd', 'total_return', 12)
        self.assertEqual(sorted(db_select.compute_picks(self.connection.cursor())),
            [('ARNC', 'stockanalysis', 'total_return'), ('REUSED', 'eodhd', 'total_return')])

    def test_archive_only_and_quarantined_symbols_have_no_automatic_pick(self):
        self.add('ARCHIVE', 'kaggle_yahoo2020', 'total_return', 168)
        self.add('UNKNOWN', 'stockanalysis', 'total_return', 168)
        self.connection.execute("insert into series_quarantine values('UNKNOWN')")
        self.assertEqual(db_select.compute_picks(self.connection.cursor()), [])

    def test_selected_price_only_series_is_not_relabelled_total_return(self):
        self.add('PRICE', 'hf_ayushkhaire', 'price_only', 168)
        self.add('MIXED', 'eodhd', 'total_return', 100)
        self.add('MIXED', 'eodhd', 'price_only', 1)
        self.assertEqual(sorted(db_select.compute_picks(self.connection.cursor())),
            [('MIXED', 'eodhd', 'price_only'), ('PRICE', 'hf_ayushkhaire', 'price_only')])

    def test_refresh_corrects_basis_without_bypassing_locks_or_dry_run(self):
        for locked, frozen, dry_run in [(False, False, False), (True, False, False),
                                         (False, True, False), (False, False, True)]:
            with self.subTest(locked=locked, frozen=frozen, dry_run=dry_run):
                connection = MagicMock()
                cursor = connection.cursor.return_value.__enter__.return_value
                cursor.fetchall.side_effect = [[('PRICE', 'eodhd', 'total_return')],
                    [('PRICE',)] if locked else [], [('PRICE',)] if frozen else []]
                cursor.fetchone.return_value = (1, 1)
                with patch.object(db_select, 'db', return_value=connection), \
                     patch.object(db_select, 'compute_picks', return_value=[('PRICE', 'eodhd', 'price_only')]), \
                     patch.object(db_select.psycopg2.extras, 'execute_values') as write:
                    db_select.refresh(dry_run)
                if locked or frozen or dry_run:
                    write.assert_not_called()
                else:
                    self.assertEqual(write.call_args.args[2][0][:3], ('PRICE', 'eodhd', 'price_only'))
                    self.assertIn('basis = excluded.basis', write.call_args.args[1])


if __name__ == '__main__':
    unittest.main()
