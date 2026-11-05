import calendar
import copy
import datetime as dt
import unittest

from research import review_scg_original_equity as scg


class ScanaOriginalEquityTests(unittest.TestCase):
    def fixture(self):
        months={};rows=[]
        for year in range(2008,2011):
            for month in range(1,13):
                date=dt.date(year,month,calendar.monthrange(year,month)[1])
                while date.weekday()>4:date-=dt.timedelta(days=1)
                months[date.isoformat()[:7]]={'date':date.isoformat(),'close':10.,'adj_close':10.}
                rows.append({'d':date.isoformat(),'c':'10'})
        months['2013-12']={'date':'2013-12-31','close':46.93,'adj_close':40.}
        rows.append({'d':'2013-12-31','c':'46.93'})
        events=[{'date':'2017-%02d-08'%m,'dividend':.613,'split_ratio':1} for m in (3,6,9)]
        archive={'archive_sha256':scg.ARCHIVE_SHA,'monthly':{'SCG':months},'events':{'SCG':events}}
        daily={'endpoint':'https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/x/US_MinKService.getDailyK?symbol=SCG','rows':rows}
        comparison={m:10. for m in months if m<'2011'}
        comparison['2009-03']=10.1
        return archive,daily,{'rows':copy.deepcopy(rows)},comparison

    def test_coarse_pass_cannot_authorize_incomplete_returns_or_quarantine_release(self):
        proof=scg.review(*self.fixture())
        self.assertTrue(proof['coarse_identity_threshold_pass'])
        self.assertTrue(proof['individual_return_disagreements'])
        self.assertEqual(proof['dividend_review']['archive_2017_action_count'],3)
        self.assertEqual(proof['dividend_review']['issuer_2017_declared_quarters'],4)
        self.assertFalse(proof['return_history_accepted'])
        self.assertFalse(proof['quarantine_release_accepted'])
        self.assertEqual(proof['nominal_source'],'issuer_native')

    def test_exact_issuer_price_requires_independent_date_price_and_identity(self):
        for target in ('wiki','daily','daily_date','calendar','identity','archive'):
            args=copy.deepcopy(self.fixture());archive,daily,benchmark,_=args
            if target=='wiki':archive['monthly']['SCG']['2013-12']['close']=47
            elif target=='daily':daily['rows'][-1]['c']='47'
            elif target=='daily_date':daily['rows'][-1]['d']='2013-12-30'
            elif target=='calendar':benchmark['rows'][-1]['d']='2013-12-30'
            elif target=='identity':daily['endpoint']+='OTHER'
            else:archive['archive_sha256']='changed'
            with self.subTest(target=target),self.assertRaises(ValueError):scg.review(*args)

    def test_missing_overlap_does_not_create_a_unit_receipt(self):
        args=list(self.fixture());args[-1]={'2008-01':10.}
        with self.assertRaisesRegex(ValueError,'Insufficient'):scg.review(*args)


if __name__=='__main__':unittest.main()
