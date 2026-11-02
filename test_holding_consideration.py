import unittest

from holding_consideration import distribution_value
from research.audit_lb_distribution import child_monthly_evidence


class HoldingConsiderationTests(unittest.TestCase):
    def test_child_monthly_source_requires_all_held_months_and_known_schema(self):
        header='<table><tr><th>Date</th><th>Adjusted price</th><th>Real price</th></tr>'
        rows=['<tr><td>%s 2021</td><td>$55.54</td><td>$55.54</td></tr>'%m
              for m in ['August','September','October','November','December']]
        self.assertEqual(len(child_monthly_evidence(header+''.join(rows)+'</table>')),5)
        with self.assertRaises(ValueError):child_monthly_evidence(header+''.join(rows[:-1])+'</table>')
        with self.assertRaises(ValueError):child_monthly_evidence(header+''.join(rows+rows[:1])+'</table>')

    def test_cash_and_every_successor_share_are_retained(self):
        # 1.2 dollars before the event / $60 per share = .02 old shares.
        # Cash .2 + parent 1.2 + half-share child .16 = 1.56 dollars.
        terms = [{'shares':1, 'native_entry':40, 'growth':1.5},
                 {'shares':.5, 'native_entry':20, 'growth':.8}]
        self.assertAlmostEqual(distribution_value(1.2,60,10,terms),1.56)

    def test_cash_only_merger_is_not_reinvested_or_carried_at_last_quote(self):
        self.assertAlmostEqual(distribution_value(1.2,60,70,[]),1.4)

    def test_child_evidence_cannot_be_omitted_and_remaining_weight_normalized(self):
        terms = [{'shares':1, 'native_entry':40, 'growth':1.5},
                 {'shares':.5, 'native_entry':20, 'growth':None}]
        with self.assertRaises(ValueError):
            distribution_value(1.2,60,10,terms)

    def test_successor_growth_can_include_a_later_distribution(self):
        second = distribution_value(1,20,5,[{'shares':2,'native_entry':10,'growth':1.5}])
        self.assertAlmostEqual(second,1.75)
        self.assertAlmostEqual(distribution_value(1,100,10,[{'shares':3,'native_entry':20,'growth':second}]),1.15)

    def test_explicit_zero_recovery_is_distinct_from_missing_evidence(self):
        self.assertEqual(distribution_value(1,20,0,[]),0)
        self.assertEqual(distribution_value(1,20,0,[{'shares':1,'native_entry':20,'growth':0}]),0)

    def test_invalid_unit_inputs_and_non_finite_values_fail(self):
        for growth, native, cash in [(None,20,0),(True,20,0),(1,0,0),(1,20,-1),(1,20,float('nan'))]:
            with self.subTest(growth=growth,native=native,cash=cash),self.assertRaises(ValueError):
                distribution_value(growth,native,cash,[])


if __name__=='__main__':
    unittest.main()
