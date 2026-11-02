import copy
import unittest
from unittest.mock import patch

import holding_paths
import monthly_pick
import liquidity_prices
import strategy_history
import variant_b
from holding_consideration import POLICY


class HoldingPathTests(unittest.TestCase):
    def test_legacy_or_stale_locked_signals_cannot_bypass_new_ranking_policy(self):
        with patch.object(holding_paths,'input_sha',return_value='current'), \
             patch.object(liquidity_prices,'input_sha',return_value='turnover-proof'):
            current={'price_screen_basis':'as_traded','momentum_event_policy':POLICY,
                     'holding_event_input_sha':'current','liquidity_basis':liquidity_prices.POLICY,
                     'liquidity_input_sha':'turnover-proof'}
            self.assertTrue(monthly_pick.signal_policy_matches(current))
            self.assertFalse(monthly_pick.signal_policy_matches({}))
            for field,value in [('price_screen_basis','adjusted'),
                                ('momentum_event_policy','parent_reinvestment'),
                                ('holding_event_input_sha','old'),('liquidity_basis','monthly_proxy'),
                                ('liquidity_input_sha','old-turnover-proof')]:
                with self.subTest(field=field):
                    self.assertFalse(monthly_pick.signal_policy_matches(dict(current,**{field:value})))

    def record(self):
        monthly={'2021-%02d'%m:1+m/12 for m in range(1,13)}
        evidence={'symbol':'LB','hold_year':2021,'event_date':'2021-08-03','producer':'lb_distribution',
                  'holding_event_policy':POLICY,'retained_monthly_values':monthly}
        return {'symbol':'LB','hold_year':2021,'event_date':'2021-08-03','policy':POLICY,
                'monthly_values':list(monthly.values()),'evidence':evidence,
                'evidence_sha256':holding_paths.evidence_sha(evidence),
                'calculator_sha256':holding_paths.calculator_sha('lb_distribution')}

    def test_event_path_wins_even_when_original_ticker_has_misleading_prices(self):
        event=self.record()['monthly_values']
        bars={'2020-12':(100,100),**{'2021-%02d'%m:(100,100) for m in range(1,13)}}
        with patch.object(holding_paths,'load_all',return_value={('LB',2021):event}), \
             patch.object(variant_b,'load_bars',return_value=bars), \
             patch.object(strategy_history,'load_bars',return_value=bars):
            self.assertEqual(variant_b.year_perf([{'ticker':'LB','weight':1}],2021)[0],1)
            picks=[{'ticker':'LB','weight':1}]
            self.assertEqual(strategy_history.year_perf(2021,picks)[0],1)
            self.assertEqual(picks[0]['ret_year'],1)
            self.assertEqual(variant_b.year_return(picks,2021),1)

    def test_a_and_b_share_the_same_mixed_holding_values(self):
        event=self.record()['monthly_values']
        ordinary={'2020-12':(100,100),**{'2021-%02d'%m:(112,112) for m in range(1,13)}}
        def bars(symbol):return None if symbol=='LB' else ordinary
        picks=[{'ticker':'LB','weight':.3},{'ticker':'OTHER','weight':.7}]
        with patch.object(holding_paths,'load_all',return_value={('LB',2021):event}), \
             patch.object(variant_b,'load_bars',side_effect=bars), \
             patch.object(strategy_history,'load_bars',side_effect=bars):
            self.assertAlmostEqual(variant_b.year_perf(picks,2021)[0],.384)
            self.assertEqual(strategy_history.year_perf(2021,copy.deepcopy(picks))[0],.384)

    def test_tampered_incomplete_or_old_version_evidence_fails(self):
        row=self.record()
        self.assertEqual(holding_paths.validate(row),row['monthly_values'])
        for field,value in [('evidence_sha256','0'*64),('calculator_sha256','0'*64),
                            ('hold_year',2022),('event_date','2021-08-04'),('monthly_values',[1]*11)]:
            with self.subTest(field=field),self.assertRaises(RuntimeError):
                holding_paths.validate(dict(row,**{field:value}))

    def test_missing_unreviewed_month_still_fails_without_reweighting(self):
        with patch.object(holding_paths,'load_all',return_value={}):
            with self.assertRaisesRegex(RuntimeError,'UNKNOWN 缺 2021-01'):
                holding_paths.values('UNKNOWN',2021,lambda symbol:{'2020-12':(100,100)})

    def test_unverified_latest_review_cannot_fall_back_to_parent_quotes(self):
        with patch.object(holding_paths,'load_all',return_value={('LB',2021):None}), \
             self.assertRaisesRegex(RuntimeError,'企业行动证据待核验或已撤回'):
            holding_paths.values('LB',2021,lambda symbol:{})

    def test_calendar_momentum_uses_the_same_actual_share_path(self):
        event=self.record()['monthly_values']
        misleading={'2020-12':(100,100),**{'2021-%02d'%m:(100,100) for m in range(1,13)}}
        with patch.object(holding_paths,'load_all',return_value={('LB',2021):event}):
            bars=holding_paths.momentum_bars('LB',misleading,'2020-12','2021-12')
            self.assertEqual(variant_b.ret12(bars,'2021-12','2020-12'),1)
            self.assertEqual(strategy_history.ret_12m(bars,'2021-12'),(1,True))

    def test_rolling_event_window_cannot_silently_use_provider_adjustments(self):
        with patch.object(holding_paths,'load_all',return_value={('LB',2021):self.record()['monthly_values']}), \
             patch.object(holding_paths,'_EVENT_MONTHS',{('LB',2021):'2021-08'}):
            with self.assertRaisesRegex(RuntimeError,'滚动企业行动收益尚未核验'):
                holding_paths.momentum_bars('LB',{},'2020-10','2021-10')
            self.assertEqual(holding_paths.momentum_bars('LB',{'2022-10':(1,1)},'2021-10','2022-10'),{'2022-10':(1,1)})


if __name__=='__main__':unittest.main()
