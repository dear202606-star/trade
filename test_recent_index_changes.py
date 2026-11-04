import copy
import datetime as dt
import sqlite3
import unittest
from unittest.mock import MagicMock,patch

import db_ref
from research.apply_recent_index_changes import CHANGES,SOURCE,apply_change,clipped,digest


class SQLiteCursor:
    def __init__(self,cursor):self.cursor=cursor
    def execute(self,sql,args):self.cursor.execute(sql.replace('%s','?'),args)
    def fetchall(self):return self.cursor.fetchall()
    @property
    def rowcount(self):return self.cursor.rowcount


class RecentIndexChangesTests(unittest.TestCase):
    def test_actual_change_sql_preserves_prior_intervals_and_honors_effective_dates(self):
        connection=sqlite3.connect(':memory:',detect_types=sqlite3.PARSE_DECLTYPES)
        self.addCleanup(connection.close)
        connection.execute('create table members(symbol text,start_date date,end_date date,source text,primary key(symbol,start_date))')
        old=[(c['symbol'],'2000-01-01','2099-12-31','old') for c in CHANGES if c['action']=='remove']
        old += [('ILMN','2015-11-19','2024-06-24','old'),('OTHER','1996-01-01','2099-12-31','old')]
        connection.executemany('insert into members values(?,?,?,?)',old)
        before=connection.execute('select * from members order by symbol,start_date').fetchall()
        cursor=SQLiteCursor(connection.cursor())
        for change in CHANGES:apply_change(cursor,change)
        after=connection.execute('select * from members order by symbol,start_date').fetchall()
        self.assertEqual(clipped(before,'2026-09-20'),clipped(after,'2026-09-20'))
        def pool(day):return {s for s,a,b,_ in after if str(a)<=day<=str(b)}
        self.assertIn('TAP',pool('2026-09-20'));self.assertNotIn('TAP',pool('2026-09-21'))
        self.assertIn('VYLR',pool('2026-10-01'));self.assertIn('CTVA',pool('2026-10-05'))
        self.assertNotIn('CTVA',pool('2026-10-06'));self.assertIn('TWLO',pool('2026-10-06'))
        self.assertIn('PSKY',pool('2026-10-05'));self.assertNotIn('PSKY',pool('2026-10-06'))
        self.assertIn('SKYD',pool('2026-10-06'))
        self.assertIn('KVUE',pool('2026-11-03'));self.assertNotIn('OKTA',pool('2026-11-03'))
        self.assertNotIn('KVUE',pool('2026-11-04'));self.assertIn('OKTA',pool('2026-11-04'))
        self.assertIn(('ILMN',dt.date(2015,11,19),dt.date(2024,6,24),'old'),after)
        self.assertIn(('ILMN',dt.date(2026,9,21),dt.date(2099,12,31),SOURCE),after)
        with self.assertRaises(RuntimeError):apply_change(cursor,CHANGES[0])
        self.assertEqual(after,connection.execute('select * from members order by symbol,start_date').fetchall())

    def reference(self,as_of,corrupt=False,unreviewed=False):
        change=next(c for c in CHANGES if c['symbol']=='VYLR')
        members=[('VYLR',dt.date(2026,10,1),dt.date(2099,12,31),SOURCE)]
        proof={'index':'S&P 500','change':change,'price_history_accepted':False,
               'membership_after_sha256':digest(members)}
        metrics={'proof':proof,'evidence_sha256':digest(proof)}
        if corrupt:
            metrics=copy.deepcopy(metrics);metrics['proof']['change']['sector']='Wrong'
        if unreviewed:members=members+[('UNREVIEWED',dt.date(2000,1,1),dt.date(2099,12,31),'old import')]
        connection=MagicMock();cursor=connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.side_effect=[members,[],[(1,metrics)]]
        with patch.object(db_ref,'db',return_value=connection):
            result=db_ref.load_market_reference(as_of)
        connection.set_session.assert_called_once_with(isolation_level='REPEATABLE READ',readonly=True)
        connection.close.assert_called_once()
        return result

    def test_sector_overlay_waits_for_its_own_effective_date(self):
        live,sectors,version=self.reference('2026-10-02')
        self.assertEqual(live,{'VYLR'});self.assertNotIn('VYLR',sectors)
        _,sectors,later=self.reference('2026-10-06')
        self.assertEqual(sectors['VYLR'],'Consumer Staples')
        self.assertNotEqual(version,later)

    def test_corrupt_review_or_unavailable_online_reference_cannot_fall_back(self):
        with self.assertRaisesRegex(RuntimeError,'证明无效'):self.reference('2026-10-06',True)
        with self.assertRaisesRegex(RuntimeError,'未审查'):self.reference('2026-10-06',unreviewed=True)
        with patch.object(db_ref,'db',side_effect=RuntimeError('offline')),patch.object(db_ref,'_rest') as fallback:
            with self.assertRaisesRegex(RuntimeError,'offline'):db_ref.load_market_reference('2026-11-03')
        fallback.assert_not_called()


if __name__=='__main__':
    unittest.main()
