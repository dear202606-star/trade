"""Price-unit regressions: nominal screening must not affect return units."""
import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import nominal_prices
import variant_b
import strategy_history
import reconstruct_nominal
import collect_av_nominal
import holding_paths
import security_membership
from research.audit_nominal_prices import parse_daily
from research.collect_yfinance_actions2023 import review as review_actions2023
from research.collect_tech_actions2026 import adjusted_rows


class NominalPricesTests(unittest.TestCase):
    def setUp(self):
        event_paths=patch.object(holding_paths,'load_all',return_value={})
        event_paths.start();self.addCleanup(event_paths.stop)
        aliases=patch.object(security_membership,'load_aliases',return_value=[])
        aliases.start();self.addCleanup(aliases.stop)

    def test_event_archive_uses_adjusted_close_and_requires_terminal_factor_one(self):
        rows = [{'Close':'20', 'Adj Close':'18'}, {'Close':'30', 'Adj Close':'30'}]
        self.assertEqual(adjusted_rows(rows)[0]['Close'], '18')
        self.assertEqual(rows[0]['Close'], '20')
        for value in ('29', 'NaN', '0'):
            rows[-1]['Adj Close'] = value
            with self.assertRaisesRegex(ValueError, 'terminal adjustment factor'):
                adjusted_rows(rows)

    def test_new_event_archive_requires_independent_native_units_and_fixed_vintage(self):
        rows = []
        selected, anchors = {}, {}
        for year in range(2019, 2022):
            for month in range(1, 13):
                date = '%d-%02d-28' % (year, month)
                price = 20 + len(rows)
                rows.append({'Date':date, 'Close':str(price), 'Stock Splits':'0', 'Dividends':'0'})
                selected[date[:7]] = (price, price)
                anchors[date[:7]] = {'close':price, 'date':date}
        rows.append({'Date':'2023-11-29', 'Close':'100', 'Stock Splits':'0', 'Dividends':'0'})
        self.assertEqual(review_actions2023(rows, selected, anchors)['status'], 'crosscheck_pass')
        self.assertEqual(review_actions2023(rows, selected, {})['status'], 'review_required')
        wrong_units = {m:dict(r, close=r['close']/2) for m,r in anchors.items()}
        self.assertEqual(review_actions2023(rows, selected, wrong_units)['status'], 'review_required')
        uncertain = {m:dict(r) for m,r in anchors.items()}
        uncertain['2019-01']['close'] *= .991
        rows.insert(-1, {'Date':'2022-01-28', 'Close':'5.04', 'Stock Splits':'0', 'Dividends':'0'})
        result = review_actions2023(rows, selected, uncertain)
        self.assertEqual(result['status'], 'crosscheck_pass')
        self.assertIn('2022-01', result['ambiguous_threshold_months'])
        self.assertNotIn('2022-01', result['months'])
        rows[-1]['Date'] = '2023-11-28'
        with self.assertRaisesRegex(ValueError, 'terminal quote'):
            review_actions2023(rows, selected, anchors)

    def test_missing_held_month_is_not_a_flat_return_after_delisting(self):
        bars={'2020-12':(100,100)}
        bars.update({'2021-%02d'%m:(110,110) for m in range(1,8)})
        picks=[{'ticker':'LB','weight':1.0}]
        with patch.object(holding_paths,'load_all',return_value={}),patch.object(variant_b,'load_bars',return_value=bars):
            with self.assertRaisesRegex(RuntimeError,'LB 缺 2021-08'):
                variant_b.year_perf(picks,2021)
        with patch.object(holding_paths,'load_all',return_value={}),patch.object(strategy_history,'load_bars',return_value=bars):
            self.assertEqual(strategy_history.year_perf(2021,picks),(None,None,None,None))

    def test_native_monthly_parser_enforces_symbol_and_security_end(self):
        series={}
        for year in (2008,2009,2010):
            for month in range(1,13):
                import calendar
                day='%d-%02d-%02d'%(year,month,calendar.monthrange(year,month)[1])
                series[day]={'4. close':'18.60'}
        payload={'Meta Data':{'2. Symbol':'BJS'},'Monthly Time Series':series}
        observations,bars,adjusted=collect_av_nominal.parse('BJS',payload)
        self.assertEqual(max(observations),'2010-03')
        self.assertEqual(observations['2009-12']['close'],18.6)
        self.assertFalse(adjusted)
        with self.assertRaisesRegex(ValueError,'symbol mismatch'):
            collect_av_nominal.parse('OTHER',payload)

    def test_zero_native_budget_does_not_load_keys_plan_or_request(self):
        with patch.object(collect_av_nominal,'targets') as targets, \
             patch.object(collect_av_nominal.relay_source,'keys') as keys, \
             patch.object(collect_av_nominal.requests,'get') as request:
            collect_av_nominal.run(0,write=True)
        targets.assert_not_called()
        keys.assert_not_called()
        request.assert_not_called()

    def test_reused_lb_cannot_fill_old_lbrands_prices(self):
        series={}
        for year in (2024,2025):
            for month in range(1,13):
                import calendar
                day='%d-%02d-%02d'%(year,month,calendar.monthrange(year,month)[1])
                series[day]={'4. close':'20','5. adjusted close':'20'}
        payload={'Meta Data':{'2. Symbol':'LB'},'Monthly Adjusted Time Series':series}
        with self.assertRaisesRegex(ValueError,'24 completed'):
            collect_av_nominal.parse('LB',payload)

    def test_inverts_cash_dividend_and_same_day_split(self):
        rows=[{'Date':'2020-01-02','Close':'49','Dividends':'0','Stock Splits':'0'},
              {'Date':'2020-01-03','Close':'49','Dividends':'1','Stock Splits':'2'}]
        restored=reconstruct_nominal.restore(rows)
        self.assertAlmostEqual(restored[0]['close'],100)
        self.assertAlmostEqual(restored[1]['close'],49)
        self.assertEqual(restored[0]['adjusted'],49)

    def test_reverse_split_and_action_only_market_closure(self):
        reverse=[{'Date':'2020-01-02','Close':'20','Dividends':'0','Stock Splits':'0'},
                 {'Date':'2020-01-03','Close':'20','Dividends':'0','Stock Splits':'.1'}]
        self.assertAlmostEqual(reconstruct_nominal.restore(reverse)[0]['close'],2)
        closure=[{'Date':'2001-09-10','Close':'50','Dividends':'0','Stock Splits':'0'},
                 {'Date':'2001-09-12','Close':'','Dividends':'0','Stock Splits':'2'},
                 {'Date':'2001-09-17','Close':'50','Dividends':'0','Stock Splits':'0'}]
        result=reconstruct_nominal.restore(closure)
        self.assertEqual([r['date'] for r in result],['2001-09-10','2001-09-17'])
        self.assertAlmostEqual(result[0]['close'],100)
        self.assertAlmostEqual(result[1]['close'],50)

    def test_first_observation_dividend_does_not_adjust_its_own_close(self):
        rows=[{'Date':'2020-01-02','Close':'10','Dividends':'1','Stock Splits':'0'}]
        self.assertEqual(reconstruct_nominal.restore(rows)[0]['close'],10)

    def test_reconstruction_rejects_ambiguous_or_malformed_snapshot(self):
        quote={'Date':'2020-01-02','Close':'10','Dividends':'0','Stock Splits':'0'}
        for rows in ([quote,quote], [dict(quote,Close='NaN')],
                     [dict(quote,Close='')], [dict(quote,Dividends='-1')],
                     [dict(quote,Close='',**{'Stock Splits':'2'})]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                reconstruct_nominal.restore(rows)

    def test_old_frozen_history_is_not_published_as_nominal_screening(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'output'
            output.mkdir()
            (output/'strategy_history.json').write_text(json.dumps({'years':[]}))
            (output/'curve.json').write_text('{}')
            with patch.object(strategy_history,'BASE',directory):
                ok, reason = strategy_history.locked_ok()
            self.assertFalse(ok)
            self.assertIn('旧筛选口径',reason)

    def test_a_uses_nominal_price_without_changing_return_series(self):
        bars = {'2015-12':(1,1)}
        bars.update({'2016-%02d'%m:(1+m/12,2.6685) for m in range(1,13)})
        with patch.object(strategy_history,'load_bars',return_value=bars), \
             patch.object(nominal_prices,'price',return_value=106.74):
            picks = strategy_history.pick_for_year(2017,['NVDA'],{'NVDA':'Technology'})
        self.assertEqual([p['ticker'] for p in picks],['NVDA'])
        self.assertEqual(picks[0]['ret12'],1.0)

    def test_last_trading_day_is_order_independent(self):
        rows = [{'d': '2016-12-30', 'c': '106.74'},
                {'d': '2016-12-28', 'c': '109.25'}]
        self.assertEqual(parse_daily(rows)['2016-12'],
                         {'date': '2016-12-30', 'close': 106.74})

    def test_duplicate_and_nonfinite_daily_prices_are_rejected(self):
        row = {'d': '2016-12-30', 'c': '106.74'}
        with self.assertRaises(ValueError):
            parse_daily([row, row])
        with self.assertRaises(ValueError):
            parse_daily([dict(row, c='NaN')])

    def test_delisting_tombstone_invalidates_month_not_prior_history(self):
        rows = [{'d': '2018-10-31', 'c': '100'},
                {'d': '2018-11-01', 'c': '101'},
                {'d': '2018-11-30', 'o': '0.00', 'h': '0.00',
                 'l': '0.00', 'c': '0.00', 'v': '0'}]
        self.assertEqual(set(parse_daily(rows)), {'2018-10'})

    def test_unknown_nominal_price_cannot_use_adjusted_close(self):
        with patch.object(nominal_prices, 'load_all', return_value={('NVDA', '2016-12'): 106.74}):
            with self.assertRaisesRegex(RuntimeError, '缺少已核验'):
                nominal_prices.price('OTHER', '2016-12')

    def test_split_adjusted_price_below_five_does_not_disqualify_nvda(self):
        frame = pd.DataFrame([{'ticker': 'NVDA', 'start_date': '2000-01-01',
                               'end_date': '2099-12-31'}])
        # Return prices remain split adjusted; screen price is independently observed.
        bars = {'2015-12': (1.0, 1.0), '2016-12': (2.0, 2.6685)}
        with patch.object(variant_b, 'load_bars', return_value=bars), \
             patch.object(nominal_prices, 'price', return_value=106.74):
            picks, _ = variant_b.pick_year(frame, {'NVDA': 'Technology'}, 2017)
        self.assertEqual([p['ticker'] for p in picks], ['NVDA'])
        self.assertEqual(picks[0]['ret12'], 1.0)
        self.assertEqual(bars['2016-12'], (2.0, 2.6685))
        for price, expected in [(4.999, []), (5.0, ['NVDA'])]:
            with patch.object(variant_b, 'load_bars', return_value=bars), \
                 patch.object(nominal_prices, 'price', return_value=price):
                picks, _ = variant_b.pick_year(frame, {'NVDA': 'Technology'}, 2017)
                self.assertEqual([p['ticker'] for p in picks], expected)


if __name__ == '__main__':
    unittest.main()
