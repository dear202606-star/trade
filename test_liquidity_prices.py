import unittest
from unittest.mock import patch

import liquidity_prices as liquidity
import monthly_pick
import db_prices
import nominal_prices
import holding_paths
import pandas as pd
from collect_liquidity_windows import daily_records


class LiquidityTests(unittest.TestCase):
    def test_monthly_rank_uses_verified_daily_window_not_month_volume_proxy(self):
        months=['2024-12']+['2025-%02d'%m for m in range(1,13)]
        bars={m:(10+i*.1,10+i*.1) for i,m in enumerate(months)}
        frame=pd.DataFrame({'adj':[v[0] for v in bars.values()],
                            'raw':[v[1] for v in bars.values()],'vol':[1]*13},index=months)
        sector=pd.DataFrame([{'ticker':'TEST','name':'Test','sector':'Tech'}])
        key=('TEST','2024-12','2025-12')
        verified={key:{'trading_days':250,'primary_amount':25e6*250,
                       'independent_lower':24e6*250,'independent_upper':26e6*250}}
        with patch.object(monthly_pick,'members_at',return_value={'TEST'}), \
             patch.object(db_prices,'load_all',return_value={'TEST':bars}), \
             patch.object(holding_paths,'load_all',return_value={}), \
             patch.object(nominal_prices,'price',return_value=11.2), \
             patch.object(liquidity,'load_all',return_value=verified) as loaded:
            ranked,pool=monthly_pick.ranking_at({'TEST':frame},months,sector,'2025-12')
            self.assertEqual(pool,1);self.assertEqual(ranked.iloc[0]['dv'],25e6)
            loaded.return_value={}
            frame['vol']=1e12
            with self.assertRaisesRegex(RuntimeError,'缺少已核验'):
                monthly_pick.ranking_at({'TEST':frame},months,sector,'2025-12')

    def data(self):
        dates={'2025-%02d-02'%m for m in range(1,13)}|{'2025-01-03'}
        primary={d:{'amount':30e6,'volume':3e6,'low':9,'high':11} for d in dates}
        other={d:{'volume':3e6,'low':8,'high':12} for d in dates}
        primary['2025-01-03']['amount']=33e6
        return primary,other,dates

    def test_daily_sum_over_actual_days_and_excluded_base_month(self):
        primary,other,dates=self.data()
        primary['2024-12-31']={'amount':1e12,'volume':1e12,'low':1,'high':2}
        result=liquidity.summarize_window(primary,other,dates,'2024-12','2025-12')
        self.assertEqual(result['trading_days'],13)
        self.assertEqual(result['primary_amount'],393e6)
        self.assertAlmostEqual(result['primary_amount']/result['trading_days'],393e6/13)
        self.assertNotAlmostEqual(result['primary_amount']/result['trading_days'],(31.5e6+11*30e6)/12)

    def test_missing_same_day_in_both_stock_sources_does_not_shrink_calendar(self):
        primary,other,dates=self.data()
        primary.pop('2025-01-03');other.pop('2025-01-03')
        with self.assertRaisesRegex(ValueError,'complete market calendar'):
            liquidity.summarize_window(primary,other,dates,'2024-12','2025-12')

    def test_calendar_missing_an_entire_middle_month_fails(self):
        primary,other,dates=self.data();dates.remove('2025-06-02')
        with self.assertRaisesRegex(ValueError,'absent month'):
            liquidity.summarize_window(primary,other,dates,'2024-12','2025-12')

    def test_independent_close_times_volume_is_not_treated_as_actual_amount(self):
        primary,other,dates=self.data()
        for row in other.values():row['amount']=1.0
        self.assertEqual(liquidity.summarize_window(primary,other,dates,'2024-12','2025-12')['primary_amount'],393e6)
        for row in other.values():row['volume']*=100
        with self.assertRaisesRegex(ValueError,'independent daily OHLCV bounds'):
            liquidity.summarize_window(primary,other,dates,'2024-12','2025-12')

    def test_bad_units_nonfinite_and_zero_activity_are_rejected(self):
        for key,value in [('amount',float('nan')),('amount',3e9),('volume',0),('amount',True)]:
            primary,other,dates=self.data();primary['2025-01-02'][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                liquidity.summarize_window(primary,other,dates,'2024-12','2025-12')

    def test_ambiguous_or_unverified_liquidity_cannot_silently_pass(self):
        key=('TEST','2024-12','2025-12')
        for entries in [{},{key:None}]:
            with patch.object(liquidity,'load_all',return_value=entries),self.assertRaisesRegex(RuntimeError,'缺少已核验'):
                liquidity.average_daily(*key,20e6)
        row={'trading_days':250,'primary_amount':21e6*250,
             'independent_lower':18e6*250,'independent_upper':22e6*250}
        with patch.object(liquidity,'load_all',return_value={key:row}):
            with self.assertRaisesRegex(RuntimeError,'跨越筛选门槛'):liquidity.average_daily(*key,20e6)
            self.assertEqual(liquidity.average_daily(*key,10e6),21e6)
            self.assertEqual(liquidity.average_daily(*key,30e6),21e6)

    def test_window_must_cover_exactly_subsequent_twelve_complete_months(self):
        self.assertEqual(liquidity.window_dates('2024-12','2025-12'),('2025-01-01','2025-12-31'))
        with self.assertRaises(ValueError):liquidity.window_dates('2025-01','2025-12')
        with self.assertRaises(ValueError):liquidity.window_dates('2098-12','2099-12')

    def test_daily_parser_rejects_duplicates_wrong_units_and_nonfinite(self):
        line='2025-01-02,10,10,11,9,3000000,30000000,0,0,0,0'
        self.assertEqual(daily_records([line],'eastmoney','2025-01-01','2025-12-31')['2025-01-02']['amount'],30e6)
        for rows in [[line,line],[line.replace('30000000','NaN')],[line.replace('10,10,11,9','10,10,8,9')]]:
            with self.assertRaises(ValueError):daily_records(rows,'eastmoney','2025-01-01','2025-12-31')


if __name__=='__main__':unittest.main()
