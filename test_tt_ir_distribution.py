import unittest
from unittest.mock import patch

import holding_paths
from research import audit_tt_ir_distribution as audit


class TraneDistributionTests(unittest.TestCase):
    def inputs(self):
        dates=['2020-01-31','2020-02-28','2020-03-31','2020-04-30','2020-05-29','2020-06-30',
               '2020-07-31','2020-08-31','2020-09-30','2020-10-30','2020-11-30','2020-12-31']
        month_ends={'2020-%02d'%(i+1):d for i,d in enumerate(dates)}
        parent={d:200. for d in dates[2:]};parent['2020-03-02']=100.55
        child={d:40. for d in dates[2:]};child['2020-03-02']=32.80
        adjusted={d:20. for d in dates[2:]};adjusted['2020-03-02']=10.
        return {'original_native':{'2019-12-31':132.92,'2020-01-31':140.,'2020-02-28':129.04},
                'parent_native':parent,'parent_adjusted':adjusted,'child_native':child,'month_ends':month_ends}

    def test_real_parent_and_child_units_replace_the_synthetic_split(self):
        values=audit.retained_values(self.inputs())
        self.assertEqual(len(values),12)
        self.assertAlmostEqual(values['2020-01'],140/132.92)
        self.assertAlmostEqual(values['2020-02'],129.04/132.92)
        expected=(100.55*2+.8824*40)/132.92
        self.assertAlmostEqual(values['2020-12'],expected)
        self.assertNotAlmostEqual(values['2020-12'],1.289*100.55*2/132.92)
        # Parent ordinary dividends are already in the component growth ratio.
        self.assertNotAlmostEqual(values['2020-12'],expected+2.12/132.92)

    def test_missing_child_or_original_cost_cannot_use_a_parent_only_fallback(self):
        inputs=self.inputs();del inputs['child_native']['2020-12-31']
        with self.assertRaises(KeyError):audit.retained_values(inputs)
        inputs=self.inputs();inputs['original_native']['2019-12-31']=100.55
        with self.assertRaisesRegex(ValueError,'cost'):audit.retained_values(inputs)
        inputs=self.inputs();inputs['child_native']['2020-12-31']=float('nan')
        with self.assertRaises(ValueError):audit.retained_values(inputs)

    def test_daily_adjustments_must_reconcile_to_the_reviewed_cash_event(self):
        raw={'a':100.,'b':90.};adjusted={'a':100.,'b':100.}
        self.assertEqual(audit.factor_errors(adjusted,raw,{'b':10.})['max_log_deviation'],0)
        with self.assertRaises(ValueError):audit.factor_errors(adjusted,raw,{})
        with self.assertRaises(ValueError):audit.factor_errors({'a':100.},raw,{})

    def test_annual_momentum_and_holding_share_the_same_path_without_new_ir_alias(self):
        values=list(audit.retained_values(self.inputs()).values())
        with patch.object(holding_paths,'load_all',return_value={('TT',2020):values}), \
                patch.object(holding_paths.historical_lineage,'load',return_value={'IR':{'comparison_from':'2020-03'}}):
            trap=lambda symbol:self.fail('Quoted-price fallback used')
            self.assertEqual(holding_paths.values('TT',2020,trap),values)
            momentum=holding_paths.momentum_bars('TT',{},'2019-12','2020-12')
            self.assertEqual(momentum['2019-12'],(1.,None))
            self.assertEqual(momentum['2020-12'],(values[-1],None))
            with self.assertRaises(RuntimeError):holding_paths.values('IR',2020,trap)
            with self.assertRaises(RuntimeError):holding_paths.momentum_bars('IR',{},'2019-12','2020-12')


if __name__=='__main__':unittest.main()
