"""Exercise exact reviewed boundary SQL, including repeat application."""
import sqlite3
import unittest
from pathlib import Path

BASE=Path(__file__).resolve().parent


class MergerBoundaryTests(unittest.TestCase):
    def test_partial_and_post_merger_months_excluded_without_touching_originals(self):
        connection=sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.executescript('''
            create table prices_monthly(symbol text,month text,adj_close numeric,
                raw_close numeric,source text,basis text);
            create table series_pick(symbol text primary key,source text);
            create table series_quarantine(symbol text primary key);
            create table security_history_window(symbol text primary key,month_to text,
                evidence_url text,reason text);
        ''')
        rows=[('BCR','2017-11',335.94,335.94,'test','price_only'),
              ('BCR','2017-12',331.24,331.24,'test','price_only'),
              ('BCR','2018-01',331.24,331.24,'test','price_only'),
              ('CAM','2016-03',67.05,67.05,'test','price_only'),
              ('CAM','2016-04',66.01,66.01,'test','price_only'),
              ('CAM','2026-10',123.81,123.81,'test','price_only'),
              ('OTHER','2026-10',100,100,'test','total_return')]
        connection.executemany('insert into prices_monthly values(?,?,?,?,?,?)',rows)
        connection.executemany('insert into series_pick values(?,?)',[(s,'test') for s in ('BCR','CAM','OTHER')])
        predicate=(BASE/'sql/095_security_history_windows.sql').read_text().split(
            'create or replace view v_series as\n',1)[1].split('commit;',1)[0]
        connection.executescript('create view v_series as\n'+predicate)
        sql=(BASE/'sql/112_bcr_cam_merger_boundaries.sql').read_text().replace('public.','')
        for _ in range(2):
            connection.executescript(sql)
            self.assertEqual(connection.execute('select symbol,month from v_series order by symbol').fetchall(),
                             [('BCR','2017-11'),('CAM','2016-03'),('OTHER','2026-10')])
            self.assertEqual(connection.execute('select * from prices_monthly').fetchall(),rows)
            self.assertEqual(connection.execute('select count(*) from series_pick').fetchone()[0],3)
            self.assertEqual(connection.execute('select count(*) from security_history_window').fetchone()[0],2)


if __name__=='__main__':
    unittest.main()
