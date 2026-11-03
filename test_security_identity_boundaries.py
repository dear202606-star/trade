"""Exercise the actual exclusion statements and selected-view predicate offline."""
import sqlite3
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch


from research import audit_nominal_impact as impact


BASE = Path(__file__).resolve().parent


class SecurityIdentityBoundaryTests(unittest.TestCase):
    def test_quarantined_member_cannot_disappear_into_a_complete_screen(self):
        picks = [{'ticker':'ABC','weight':1}]
        prices = {'ABC':{'2024-12':(100,100),'2025-12':(110,110)}}
        for member_end,expected in (('2099-12-31',False),('2020-12-31',True)):
            with self.subTest(member_end=member_end), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); (root/'output').mkdir()
                (root/'output/baseline_b.json').write_text('{"years":{}}')
                patches = [patch.object(impact,'BASE',root),
                     patch.object(impact,'range',return_value=[2026],create=True),
                     patch.object(impact.db_prices,'load_all',return_value=prices),
                     patch.object(impact.db_prices,'_db_stamp',return_value=('stamp',)),
                     patch.object(impact,'nominal_snapshot',return_value=({('ABC','2025-12'):110},'native')),
                     patch.object(impact,'quarantine_snapshot',return_value=({'Q':'Wrong issuer'},'quarantine')),
                     patch.object(impact.holding_paths,'load_all',return_value={}),
                     patch.object(impact.holding_paths,'input_sha',return_value='events'),
                     patch.object(impact.holding_paths,'_PATHS',None),
                     patch.object(impact.datafeed,'_ALL_BARS',None),
                     patch.object(impact.variant_b,'_ALL_BARS',None),
                     patch.object(impact.holding_paths,'momentum_bars',side_effect=lambda s,b,a,z:b),
                     patch.object(impact.db_ref,'load_members',return_value={
                         'ABC':[('2008-01-01','2099-12-31')], 'Q':[('2008-01-01',member_end)]}),
                     patch.object(impact.db_ref,'load_sectors',return_value={'ABC':'Tech','Q':'Tech'}),
                     patch.object(impact.variant_b,'members_full_year',side_effect=lambda f,y:set(f[f.end_date>='%d-12-31'%y].ticker)),
                     patch.object(impact.variant_b,'_series_ok',return_value=True),
                     patch.object(impact.variant_b,'ret12',return_value=.1),
                     patch.object(impact.variant_b,'pick_year',return_value=(picks,1)),
                     patch.object(impact.variant_a,'ret_12m',return_value=(.1,True)),
                     patch.object(impact.variant_a,'pick_for_year',return_value=picks)]
                with ExitStack() as stack:
                    for replacement in patches:
                        stack.enter_context(replacement)
                    impact.run()
                result = json.loads((root/'output/nominal_price_impact.json').read_text())
                self.assertEqual(result['fully_verified'],expected)
                for year in result['years']:
                    self.assertEqual(year['price_screen_complete'],expected)
                    self.assertEqual(year['quarantined_candidate_symbols'],[] if expected else ['Q'])

    def test_reused_lsi_history_is_excluded_before_and_after_original_exit(self):
        connection = sqlite3.connect(':memory:')
        try:
            connection.executescript('''
                create table prices_monthly(symbol text,month text,adj_close numeric,
                    raw_close numeric,source text,basis text);
                create table series_pick(symbol text primary key,source text);
                create table series_quarantine(symbol text primary key,reason text);
                create table security_history_window(symbol text primary key,month_to text,
                    evidence_url text,reason text);
            ''')
            rows = [('LSI','2013-12',65.17,65.17,'test','price_only'),
                    ('LSI','2017-04',82.85,82.85,'test','price_only'),
                    ('DTV','2015-06',93.7,93.7,'test','price_only'),
                    ('DTV','2015-07',95,95,'test','price_only'),
                    ('OTHER','2025-12',12,12,'test','total_return')]
            connection.executemany('insert into prices_monthly values (?,?,?,?,?,?)',rows)
            connection.executemany('insert into series_pick values (?,?)',
                                   [(s,'test') for s in ('LSI','DTV','OTHER')])
            view = (BASE/'sql/095_security_history_windows.sql').read_text().split(
                'create or replace view v_series as\n',1)[1].split('commit;',1)[0]
            connection.executescript('create view v_series as\n'+view)
            self.assertEqual(connection.execute('select count(*) from v_series').fetchone()[0],5)
            sql = (BASE/'sql/109_lsi_dtv_identity_boundaries.sql').read_text().replace('public.','')
            connection.executescript(sql)
            self.assertEqual(connection.execute('select symbol,month from v_series order by symbol').fetchall(),
                             [('DTV','2015-06'),('OTHER','2025-12')])
            self.assertEqual(connection.execute('select * from prices_monthly').fetchall(),rows)
            self.assertEqual(connection.execute('select count(*) from series_pick').fetchone()[0],3)
            connection.executescript(sql)
            self.assertEqual(connection.execute('select count(*) from series_quarantine').fetchone()[0],1)
            self.assertEqual(connection.execute('select count(*) from security_history_window').fetchone()[0],2)
        finally:
            connection.close()


if __name__ == '__main__':
    unittest.main()
