import copy
import datetime as dt
import unittest
from unittest.mock import MagicMock, patch

import live_quotes as live
from live_strategy import sha
from market_calendar import Calendar, fingerprint
from research.install_exchange_calendar import CALENDAR


class QuoteRepairTests(unittest.TestCase):
    def setUp(self):
        self.calendar=Calendar(CALENDAR,fingerprint(CALENDAR))
        self.now=dt.datetime(2026,11,5,9,tzinfo=dt.timezone.utc)
        self.envelope={'rc':0,'data':{'f57':'HLT','f59':3,'f43':376530,
                                     'f60':363830,'f86':1793826000}}
        self.body={'symbol':'HLT','source_end':'2026-11-04',
                   'raw':{'2026-11-03':363.83,'2026-11-04':376.53}}
        self.fields=['']*30
        self.fields[1]='376.5300';self.fields[26]='363.8300'

    def test_secondary_uses_explicit_precision_and_real_source_time(self):
        quote,stamp=live.parse_secondary('HLT',self.envelope,363.83,self.now,self.calendar)
        self.assertEqual(quote['p'],376.53)
        self.assertEqual(stamp.isoformat(),'2026-11-04T21:00:00+00:00')
        self.assertNotIn('vol',quote)

    def test_secondary_rejects_identity_units_prices_and_times(self):
        for key,value in [('f57','OTHER'),('f59',None),('f59',True),('f59',7),
                          ('f43',True),('f43',-1),('f60',363000),
                          ('f86',1793739600),('f86',1793829600)]:
            envelope=copy.deepcopy(self.envelope);envelope['data'][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                live.parse_secondary('HLT',envelope,363.83,self.now,self.calendar)

    def repair(self,body=None,now=None,digest=None,fields=None):
        body=self.body if body is None else body
        connection=MagicMock()
        connection.cursor.return_value.__enter__.return_value.fetchall.return_value=[
            ('HLT',body,digest or sha(body))]
        with patch('live_strategy.provider_markets',return_value={'HLT':106}), \
                patch.object(live.db_sources,'db',return_value=connection), \
                patch.object(live.quotes.requests,'get',side_effect=live.quotes.requests.RequestException('unavailable')):
            return live.repair_quotes(['SPY','HLT'],{'gb_hlt':self.fields if fields is None else fields},
                                      now or self.now,self.calendar)

    def test_closed_fact_requires_matching_independent_price_pair(self):
        result=self.repair()
        self.assertEqual(result['HLT'][0]['source'],'corroborated_daily_close')
        self.assertEqual(result['HLT'][1].isoformat(),'2026-11-04T21:00:00+00:00')
        self.assertNotIn('SPY',result)
        fields=self.fields.copy();fields[26]='360'
        self.assertEqual(self.repair(fields=fields),{})
        fields=self.fields.copy();fields[1]='377'
        self.assertEqual(self.repair(fields=fields),{})

    def test_no_closed_fallback_for_intraday_old_session_or_tampered_proof(self):
        self.assertEqual(self.repair(now=dt.datetime(2026,11,5,16,tzinfo=dt.timezone.utc)),{})
        body=copy.deepcopy(self.body);body['source_end']='2026-11-03'
        self.assertEqual(self.repair(body=body),{})
        with self.assertRaisesRegex(ValueError,'hash/identity'):
            self.repair(digest='tampered')


if __name__=='__main__':unittest.main()
