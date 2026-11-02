import unittest
from import_verified_closing_facts import verify_exact_close
from unittest.mock import patch

import collect_eastmoney_nominal as collector


class EastmoneyNominalTests(unittest.TestCase):
    def test_issuer_fact_approves_only_exact_security_date_and_finite_close(self):
        fact={'symbol':'TEST','date':'2016-12-30','close':106.74}
        data=self.payload()['data']
        verify_exact_close(data,fact,105)
        for change in [{'code':'OTHER'},{'market':106},
                       {'klines':['2016-12-29,92,106.74,120,84,1000,0,0,0,0,0']},
                       {'klines':[data['klines'][0]]*2},
                       {'klines':['2016-12-30,92,107,120,84,1000,0,0,0,0,0']},
                       {'klines':['2016-12-30,92,NaN,120,84,1000,0,0,0,0,0']},
                       {'klines':['2016-12-30,92,106.74']}]:
            with self.subTest(change=change),self.assertRaises(ValueError):
                verify_exact_close(dict(data,**change),fact,105)

    def payload(self):
        return {'rc':0,'data':{'code':'TEST','market':105,'klines':[
            '2016-12-30,92.1,106.74,119.93,84.77,1000,0,0,0,0,0',
            '2017-01-31,100,110,120,90,1000,0,0,0,0,0']}}

    def test_parser_preserves_native_units_and_exact_security(self):
        payload=self.payload()
        self.assertEqual(collector.parse('TEST',105,payload)['2016-12']['close'],106.74)
        with self.assertRaisesRegex(ValueError,'identity'):
            collector.parse('OTHER',105,payload)
        with self.assertRaisesRegex(ValueError,'identity'):
            collector.parse('TEST',106,payload)
        self.assertEqual(set(collector.parse('TEST',105,payload,'2016-12')),{'2016-12'})

    def test_partial_month_and_duplicate_or_invalid_quotes(self):
        payload=self.payload()
        payload['data']['klines'][1]='2017-01-10,100,110,120,90,1000,0,0,0,0,0'
        self.assertNotIn('2017-01',collector.parse('TEST',105,payload))
        payload['data']['klines']=[payload['data']['klines'][0]]*2
        with self.assertRaisesRegex(ValueError,'duplicate'):
            collector.parse('TEST',105,payload)
        payload['data']['klines']=['2016-12-30,92.1,NaN,119,84,1000,0,0,0,0,0']
        with self.assertRaisesRegex(ValueError,'invalid'):
            collector.parse('TEST',105,payload)

    def test_return_similarity_without_native_unit_proof_is_rejected(self):
        monthly={};anchors={};selected={}
        for i in range(30):
            year,month=2015+i//12,i%12+1;m='%d-%02d'%(year,month)
            price=100+i
            monthly[m]={'date':m+'-28','close':price}
            anchors[m]={'date':m+'-28','close':price}
            selected[m]=(price,price)
        self.assertEqual(collector.review(monthly,anchors,selected)['native_pairs'],30)
        with self.assertRaisesRegex(ValueError,'native-price units'):
            collector.review(monthly,{},selected)
        wrong={m:dict(r,close=r['close']/2) for m,r in anchors.items()}
        with self.assertRaisesRegex(ValueError,'native-price units'):
            collector.review(monthly,wrong,selected)

    def test_zero_limit_never_reads_database_or_requests(self):
        with patch.object(collector.db_select,'db') as database,patch.object(collector.requests,'Session') as session:
            collector.run(0,write=True)
        database.assert_not_called();session.assert_not_called()


if __name__=='__main__':unittest.main()
