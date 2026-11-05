import copy
import datetime as dt
import unittest

from live_ranking import select,joined_interval,continuous_member,calculate
from prepare_live_ranking import normalized


class IndependentRankingTests(unittest.TestCase):
    def test_sector_cap_and_deterministic_ties(self):
        rows=[{'ticker':s,'sector':sector,'ytd':v} for s,sector,v in
              [('A','IT',9),('B','IT',8),('C','IT',7),('D','Health',6)]]
        picks=select(rows)
        self.assertEqual([r['ticker'] for r in picks],['A','B','D'])
        self.assertEqual([r['weight'] for r in picks],[.3,.3,.4])
        self.assertEqual([r['ticker'] for r in select(list(reversed(rows)))],['A','B','D'])

    def test_reused_bny_fund_history_cannot_set_bank_cost(self):
        initial={'raw':{'2025-12-31':10,'2026-05-21':140,'2026-11-04':150},
                 'adjusted':{'2025-12-31':10,'2026-05-21':140,'2026-11-04':150}}
        review={'raw':{'2025-12-31':116,'2026-05-20':137},
                'effective_date':'2026-05-21','pre_boundary_cash_distributions':[.53,.53]}
        lower,upper=joined_interval(review,initial,initial,150)
        self.assertAlmostEqual(lower,150/116)
        self.assertLess(upper,1.31)

    def test_year_to_date_continuity_merges_verified_alias_but_not_prior_gaps(self):
        members=[('OLD',dt.date(2025,1,1),dt.date(2026,5,21)),
                 ('NEW',dt.date(2026,5,21),dt.date(2099,1,1))]
        reviews={'NEW':{'old_symbol':'OLD','effective_date':'2026-05-21'}}
        self.assertTrue(continuous_member('NEW','2026-11-04',members,reviews))
        members[0]=('OLD',dt.date(2026,3,1),dt.date(2026,5,21))
        self.assertFalse(continuous_member('NEW','2026-11-04',members,reviews))
        self.assertTrue(continuous_member('FITB','2026-11-04',
            [('FITB',dt.date(2000,1,1),dt.date(2099,1,1))],
            {'FITB':{'old_symbol':'FITB','effective_date':'2026-06-12'}}))

    def fixture(self):
        pool={'A','B','C','D'};sectors={'A':'IT','B':'IT','C':'Health','D':'Energy'}
        bodies={s:{'raw':{'2025-12-31':10.,'2026-11-04':v},
                   'adjusted':{'2025-12-31':10.,'2026-11-04':v}} for s,v in [('A',80.),('B',40.),('C',30.),('D',20.)]}
        q={'session':'2026-11-04','previous_session':'2026-11-03','intraday':False,
           'fresh_until':'2026-11-05T14:50:00Z',
           'ts_local':'2026-11-05 05:00','ts':'2026-11-04T21:00:00Z','version':'v',
           'quotes':{s:{'p':b['raw']['2026-11-04']} for s,b in bodies.items()}}
        return q,pool,sectors,bodies

    def test_complete_pool_and_independent_quote_check(self):
        q,pool,sectors,bodies=self.fixture()
        payload,evidence=calculate('A',q,pool,sectors,[],{},bodies,bodies,{},'ref')
        self.assertEqual([r['ticker'] for r in payload['top']],['A','B','C'])
        self.assertEqual(len(evidence['candidate_intervals']),len(pool))
        broken=copy.deepcopy(bodies);del broken['D']['raw']['2025-12-31']
        with self.assertRaises(ValueError):calculate('A',q,pool,sectors,[],{},broken,broken,{},'ref')
        q['quotes']['D']['p']=21
        with self.assertRaises(ValueError):calculate('A',q,pool,sectors,[],{},bodies,bodies,{},'ref')

    def test_incremental_normalization_uses_sealed_scalar_instead_of_replaying_year(self):
        body={'raw':{'2026-11-03':100.,'2026-11-04':110.},
              'adjusted':{'2026-11-03':100.,'2026-11-04':110.},
              'normalized':{'2026-11-03':2.,'2026-11-04':2.2}}
        self.assertEqual(normalized(body)['2026-11-04'],2.2)

    def test_prior_year_renames_listings_and_distributions_are_not_applied_again(self):
        q,pool,sectors,bodies=self.fixture()
        for body in bodies.values():
            value=body['raw']['2026-11-04']
            body['raw']={'2026-12-31':10.,'2027-11-04':value};body['adjusted']=dict(body['raw'])
        q.update(session='2027-11-04',previous_session='2027-11-03')
        reviews={'A':{'effective_date':'2026-05-21','raw':{'2025-12-31':5.,'2026-05-20':8.},
                       'pre_boundary_cash_distributions':[]},
                 'D':{'kind':'new_listing','first_regular_way_date':'2026-06-01','sources':['issuer']}}
        terms={'D':{'event_date':'2026-06-01','kind':'spinoff','distributed_symbol':'OLD_CHILD'}}
        payload,_=calculate('A',q,pool,sectors,[],reviews,bodies,bodies,terms,'ref')
        self.assertEqual(payload['ranked'],4)
        self.assertEqual(payload['top'][0]['ytd'],7)


if __name__=='__main__':unittest.main()
