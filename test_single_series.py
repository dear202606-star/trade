import sqlite3
import unittest
from unittest.mock import patch

import db_prices


class Cursor:
    def __init__(self, connection):self.cursor=connection.cursor()
    def __enter__(self):return self
    def __exit__(self,*args):self.cursor.close()
    def execute(self,sql,params):self.cursor.execute(sql.replace('%s','?'),params)
    def fetchall(self):return self.cursor.fetchall()


class Connection:
    def __init__(self,connection):self.connection=connection
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def cursor(self):return Cursor(self.connection)


class SingleSeriesTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.executescript('''
            create table prices_monthly(symbol text,month text,adj_close real,raw_close real,source text);
            create table series_pick(symbol text,source text);
            create table series_quarantine(symbol text);
            create view series_selected as select p.* from prices_monthly p
              join series_pick k using(symbol,source)
              where not exists(select 1 from series_quarantine q where q.symbol=p.symbol);
            insert into prices_monthly values
              ('SUN','2012-09',7.65,24,'yahoo_relay'),
              ('OPEN','2025-12',100,null,'reviewed'),
              ('OPEN','2025-12',999,999,'alphavantage'),
              ('OPEN','2025-11',888,888,'alphavantage');
            insert into series_pick values('SUN','yahoo_relay'),('OPEN','reviewed');
            insert into series_quarantine values('SUN');
        ''')
        p=patch.object(db_prices,'connect',return_value=Connection(self.db));p.start();self.addCleanup(p.stop)

    def test_quarantine_and_reviewed_source_are_not_bypassed(self):
        self.assertEqual(db_prices.series('SUN'),{})
        self.assertEqual(db_prices.series('OPEN'),{'2025-12':(100,None)})
        self.assertEqual(db_prices.series('OPEN',prefer_basis='price_only'),{'2025-12':(100,None)})

    def test_repeated_read_sees_new_quarantine_without_process_restart(self):
        self.assertTrue(db_prices.series('OPEN'))
        self.db.execute("insert into series_quarantine values('OPEN')")
        self.assertEqual(db_prices.series('OPEN'),{})

    def test_database_failure_never_returns_a_previous_read(self):
        self.assertTrue(db_prices.series('OPEN'))
        with patch.object(db_prices,'connect',side_effect=RuntimeError('offline')):
            with self.assertRaisesRegex(RuntimeError,'offline'):db_prices.series('OPEN')

    def test_strategy_fingerprint_failure_is_not_a_reusable_input_version(self):
        import variant_b
        with patch.object(db_prices,'connect',side_effect=RuntimeError('offline')):
            with self.assertRaisesRegex(RuntimeError,'Cannot verify online'):
                variant_b._db_sha()


if __name__=='__main__':unittest.main()
