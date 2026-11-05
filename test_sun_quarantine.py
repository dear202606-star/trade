import sqlite3
import unittest
from pathlib import Path


BASE = Path(__file__).resolve().parent


class SunQuarantineTests(unittest.TestCase):
    def test_exact_sql_excludes_sun_without_rewriting_raw_prices_or_locks(self):
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.executescript('''
            create table prices_monthly(symbol text,month text,adj_close numeric,
                raw_close numeric,source text,basis text);
            create table series_pick(symbol text primary key,source text);
            create table series_quarantine(symbol text primary key,reason text);
            create table security_history_window(symbol text,month_to text);
            create table data_lock(id integer,symbol text,fingerprint text);
            insert into prices_monthly values
                ('SUN','2012-09',7.65,24,'yahoo_relay','total_return'),
                ('SUN','2025-12',40,50,'yahoo_relay','total_return'),
                ('OTHER','2025-12',100,100,'yahoo_relay','total_return');
            insert into series_pick values('SUN','yahoo_relay'),('OTHER','yahoo_relay');
            insert into data_lock values(359,'SUN','original'),(1310,'SUN','original');
        ''')
        view = (BASE/'sql/095_security_history_windows.sql').read_text().split(
            'create or replace view v_series as\n',1)[1].split('commit;',1)[0]
        connection.executescript('create view v_series as\n'+view)
        preserved = {table:connection.execute('select * from '+table).fetchall()
                     for table in ('prices_monthly','series_pick','data_lock')}
        sql = (BASE/'sql/119_sun_identity_quarantine.sql').read_text().replace('public.','')
        for _ in range(2):
            connection.executescript(sql)
            self.assertEqual(connection.execute('select symbol from v_series').fetchall(), [('OTHER',)])
            self.assertEqual(connection.execute('select count(*) from series_quarantine').fetchone(), (1,))
            for table, rows in preserved.items():
                self.assertEqual(connection.execute('select * from '+table).fetchall(), rows)


if __name__ == '__main__':
    unittest.main()
