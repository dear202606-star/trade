import copy
import unittest
from unittest.mock import MagicMock,patch

from live_strategy import calculate_equity,parse_daily,validate_anchor,store,online_prices,sha,fetch_daily
import requests
from market_calendar import Calendar,fingerprint
from research.install_exchange_calendar import CALENDAR


class IndependentEquityTests(unittest.TestCase):
    def setUp(self):
        self.calendar=Calendar(CALENDAR,fingerprint(CALENDAR))
        self.anchor={'equity':{'base_date':'20251231','last_session':'20261102',
            'position':[{'ticker':'X','weight':1.,'entry':50.,'entry_raw':100.,
                         'last':60.,'last_raw':120.,'last_date':'20261102'}],
            'stats':{'strategy':.2,'benchmark':.1},'points':[{'d':'20261102','s':1.2,'b':1.1}]},
            'signal':{'official':[{'ticker':'X','weight':1.}]}}
        self.quotes={'session':'2026-11-04','previous_session':'2026-11-03','intraday':False,
            'fresh_until':'2026-11-05T14:50:00Z',
            'ts_local':'2026-11-05 05:00','ts':'2026-11-04T21:00:00Z','version':'v',
            'quotes':{'X':{'p':144.,'pc':132.},'SPY':{'p':110.,'pc':105.}}}
        self.prices={s:{'raw':dict(zip(['2026-11-02','2026-11-03','2026-11-04'],v)),
                        'adjusted':dict(zip(['2026-11-02','2026-11-03','2026-11-04'],v))}
            for s,v in [('X',[120.,132.,144.]),('SPY',[100.,105.,110.])]}

    def test_multi_day_catchup_and_repeat_never_double_count(self):
        original=copy.deepcopy(self.anchor)
        result=calculate_equity(self.anchor,self.quotes,self.prices,self.calendar)
        self.assertAlmostEqual(result['stats']['strategy'],.44)
        self.assertAlmostEqual(result['today']['pct'],144/132-1)
        self.assertAlmostEqual(result['today']['per_dollar'],.12)
        self.assertEqual(result['position'][0]['last_raw'],144)
        self.assertEqual(result['last_session'],'20261104')
        repeated=calculate_equity({**self.anchor,'equity':result},self.quotes,self.prices,self.calendar)
        self.assertAlmostEqual(repeated['stats']['strategy'],.44)
        self.assertEqual(len(repeated['points']),3)
        self.assertEqual(self.anchor,original)

    def test_source_gap_wrong_units_or_previous_close_refuse_publication(self):
        for kind in ('missing','pc','anchor','adjustment'):
            prices=copy.deepcopy(self.prices);quotes=copy.deepcopy(self.quotes)
            if kind=='missing':del prices['X']['raw']['2026-11-03']
            if kind=='pc':quotes['quotes']['X']['pc']=131
            if kind=='anchor':prices['X']['raw']['2026-11-02']=121
            if kind=='adjustment':prices['X']['adjusted']['2026-11-04']=72
            with self.subTest(kind=kind),self.assertRaises((ValueError,KeyError)):
                calculate_equity(self.anchor,quotes,prices,self.calendar)

    def test_intraday_result_retains_settled_anchor_and_uses_current_quote_once(self):
        self.quotes['intraday']=True
        result=calculate_equity(self.anchor,self.quotes,self.prices,self.calendar)
        self.assertEqual(result['last_session'],'20261103')
        self.assertAlmostEqual(result['stats']['strategy'],.32)
        self.assertAlmostEqual(result['today']['pct'],144/132-1)

    def test_mismatched_official_or_curve_cannot_seed_anchor(self):
        invalid=copy.deepcopy(self.anchor);invalid['equity']['stats']['strategy']=float('nan')
        with self.assertRaises(ValueError):validate_anchor(invalid)
        self.anchor['signal']['official'][0]['weight']=.9
        with self.assertRaises(ValueError):validate_anchor(self.anchor)

    def test_verified_cash_distribution_updates_intraday_asset_units(self):
        self.quotes['intraday']=True
        self.prices['X']['adjusted'].update({'2026-11-02':120*131/132,'2026-11-03':131})
        proofs={'X':{'2026-11-04':{'amount':1.}}}
        result=calculate_equity(self.anchor,self.quotes,self.prices,self.calendar,proofs)
        expected=1.2*144/(120*131/132)
        self.assertAlmostEqual(result['today']['per_dollar'],expected-1.32)
        live=result['live_position'][0]
        self.assertAlmostEqual(live['last']/live['entry'],expected)
        self.assertEqual(result['last_session'],'20261103')

    def test_rebuilt_current_year_accounts_include_ordinary_dividends_without_mutating_old_version(self):
        from research.install_live_account_basis import rebuild
        old=copy.deepcopy(self.anchor)
        old['equity']['base_date']='20261102'
        old['equity']['position'][0].update(entry=120.,entry_raw=120.)
        self.prices['X']['adjusted']['2026-11-02']=119.
        result=rebuild(old,self.prices,self.calendar,'2026-11-04')
        self.assertAlmostEqual(result['equity']['stats']['strategy'],144/119-1)
        self.assertEqual(old['equity']['position'][0]['entry'],120)

    def test_source_identity_and_duplicate_dates(self):
        body={'rc':0,'data':{'code':'X','market':105,'klines':['2026-11-04,1,2,3,1,1,1,1,1,1,1']}}
        self.assertEqual(parse_daily(body,'X',105),{'2026-11-04':2})
        with self.assertRaises(ValueError):parse_daily(body,'Y',105)
        body['data']['klines']*=2
        with self.assertRaises(ValueError):parse_daily(body,'X',105)


class ResultPublicationTests(unittest.TestCase):
    def test_existing_online_paired_prices_need_no_external_fetch(self):
        body={'raw':{'2026-11-02':120.,'2026-11-03':132.,'2026-11-04':144.},
              'adjusted':{'2026-11-02':120.,'2026-11-03':132.,'2026-11-04':144.}}
        connection=MagicMock();cursor=connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value=[('X',body,sha(body))]
        q={'session':'2026-11-04','previous_session':'2026-11-03','intraday':False}
        with patch('live_strategy.db_sources.db',return_value=connection),patch('live_strategy.requests.get') as get:
            result=online_prices(['X'],'2026-11-02',q)
        self.assertEqual(result['X']['raw']['2026-11-04'],144)
        self.assertAlmostEqual(result['X']['adjusted']['2026-11-04'],1.2)
        get.assert_not_called()

    def test_transient_source_disconnect_retries_but_does_not_swallow_permanent_failure(self):
        response=MagicMock();response.json.return_value={'rc':0,'data':{'code':'X','market':105,
            'klines':['2026-11-04,1,2,3,1,1,1,1,1,1,1']}}
        with patch('live_strategy.requests.get',side_effect=[requests.ConnectionError('disconnect'),response,response]) as get, \
                patch('live_strategy.time.sleep'):
            self.assertEqual(fetch_daily('X',105,'2026-11-04','2026-11-04')['raw']['2026-11-04'],2)
            self.assertEqual(get.call_count,3)
        with patch('live_strategy.requests.get',side_effect=requests.ConnectionError('offline')), \
                patch('live_strategy.time.sleep'),self.assertRaises(requests.ConnectionError):
            fetch_daily('X',105,'2026-11-04','2026-11-04')

    def test_quote_race_cannot_commit_any_financial_result(self):
        connection=MagicMock();cursor=connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value=('newer',)
        with patch('live_strategy.db_sources.db',return_value=connection),self.assertRaises(RuntimeError):
            store('A','equity',{'version':'expected'}, {}, {})
        connection.commit.assert_not_called()
        self.assertEqual(cursor.execute.call_count,1)
        connection.close.assert_called_once()

    def test_intraday_updates_current_but_only_closing_results_are_archived(self):
        for intraday,expected_writes in ((True,1),(False,2)):
            connection=MagicMock();cursor=connection.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value=('current',)
            with patch('live_strategy.db_sources.db',return_value=connection):
                store('A','preview',{'version':'current','ts':'2026-11-04T21:00:00Z','intraday':intraday},
                      {'top':[]},{'calculator_sha256':'test'})
            self.assertEqual(cursor.execute.call_count,1+expected_writes)
            connection.commit.assert_called_once()


if __name__=='__main__':unittest.main()
