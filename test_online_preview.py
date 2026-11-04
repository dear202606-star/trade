import datetime as dt
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock,patch

import pandas as pd
import live_quotes
import preview
import quotes
from market_calendar import fingerprint
from research.install_exchange_calendar import CALENDAR


class OnlineQuoteReaderTests(unittest.TestCase):
    def test_share_class_quote_identifiers_keep_dollar_and_http_failures_raise(self):
        response=MagicMock();response.text='var hq_str_gb_brk$b="Berkshire,500";var hq_str_gb_bf$b="Brown,30";'
        with patch.object(quotes.requests,'get',return_value=response):
            values=quotes.sina_batch(['gb_brk$b','gb_bf$b'])
        self.assertEqual(set(values),{'gb_brk$b','gb_bf$b'})
        response.raise_for_status.side_effect=RuntimeError('HTTP failure')
        with patch.object(quotes.requests,'get',return_value=response):
            with self.assertRaisesRegex(RuntimeError,'HTTP failure'):quotes.sina_batch(['gb_brk$b'])

    def fixture(self):
        payload={'ts':'2026-11-02T21:00:00Z','session':'2026-11-02','previous_session':'2026-10-30',
                 'fresh_until':'2026-11-03T14:50:00+00:00',
                 'calendar_sha256':fingerprint(CALENDAR),'ok':True,'count':1,'universe':1,'quotes':{'SPY':{'p':100,'as_of':'2026-11-02T21:00:00Z'}}}
        sha=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        payload['version']=sha
        row=(payload['ts'],'2026-11-03T12:00:00Z',sha,payload)
        heartbeat=('2026-11-03T12:00:00Z',{'stored_evidence_sha256':sha})
        connection=MagicMock();cursor=connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value=[(CALENDAR,fingerprint(CALENDAR))]
        cursor.fetchone.side_effect=[row,heartbeat]
        return connection,payload

    def test_reader_binds_calendar_quote_and_worker_in_one_read_only_snapshot(self):
        connection,payload=self.fixture()
        with patch.object(live_quotes.db_sources,'db',return_value=connection):
            self.assertEqual(live_quotes.load_snapshot(dt.datetime(2026,11,3,12,tzinfo=dt.timezone.utc)),payload)
        connection.set_session.assert_called_once_with(isolation_level='REPEATABLE READ',readonly=True)
        connection.close.assert_called_once()

    def test_stale_or_corrupt_quote_and_database_failure_never_use_a_file(self):
        for corrupt in (False,True):
            connection,payload=self.fixture()
            if corrupt:payload['quotes']['SPY']['p']=999
            now=dt.datetime(2026,11,3,16,tzinfo=dt.timezone.utc)
            with patch.object(live_quotes.db_sources,'db',return_value=connection):
                with self.assertRaises((RuntimeError,ValueError)):live_quotes.load_snapshot(now)
            connection.close.assert_called_once()
        with patch.object(live_quotes.db_sources,'db',side_effect=RuntimeError('offline')):
            with self.assertRaisesRegex(RuntimeError,'offline'):live_quotes.load_snapshot()


class OnlinePreviewInputTests(unittest.TestCase):
    def run_preview(self,quotes,bars=None,sectors=None,session='2026-11-03',members=None):
        with tempfile.TemporaryDirectory() as directory,ExitStack() as stack:
            output=Path(directory)/'preview.json';output.write_bytes(b'previous accepted preview')
            members=members if members is not None else pd.DataFrame([
                {'ticker':t,'start_date':'2020-01-01','end_date':'2099-12-31'} for t in ('A','B','C')])
            base='%d-12'%(int(session[:4])-1)
            stack.enter_context(patch.object(preview,'OUT',str(output)))
            stack.enter_context(patch.object(preview.live_quotes,'load_snapshot',return_value={
                'quotes':quotes,'session':session,'version':'online-version','ts':'2026-11-03T21:00:00Z'}))
            guard=stack.enter_context(patch.object(preview.db_ref,'require_unquarantined_candidates'))
            live=set(members[(members.start_date<=session)&(members.end_date>=session)].ticker)
            stack.enter_context(patch.object(preview.db_ref,'load_market_reference',return_value=(
                live,sectors if sectors is not None else {'A':'One','B':'Two','C':'Three'},'reference-version')))
            prefetch=stack.enter_context(patch.object(preview.datafeed,'prefetch'))
            load_bars=stack.enter_context(patch.object(preview.datafeed,'load_bars',return_value=bars if bars is not None else {base:(100,100)}))
            stack.enter_context(patch.object(preview.datafeed,'_splits',return_value={}))
            if hasattr(preview,'nominal_prices'):
                stack.enter_context(patch.object(preview.holding_paths,'load_all',return_value={}))
                stack.enter_context(patch.object(preview.nominal_prices,'price',return_value=100,
                    side_effect=RuntimeError('缺少基准价格') if bars=={} else None))
            def export_trap(path,mode='r',**kwargs):
                if 'w' in mode:return open(path,mode,**kwargs)
                if str(path).endswith('signal.json'):
                    return io.StringIO(json.dumps({'official_month':session[:4]+'-12',
                        'official':[{'ticker':'FOREIGN','ret12':9,'weight':1}]}))
                raise AssertionError('Preview must not read local decision exports')
            stack.enter_context(patch.object(preview,'open',side_effect=export_trap,create=True))
            try:
                preview.main()
            except RuntimeError as exc:
                self.assertEqual(output.read_bytes(),b'previous accepted preview')
                return str(exc),guard.call_args.args[0]
            if hasattr(preview,'nominal_prices'):
                prefetch.assert_not_called()
                load_bars.assert_not_called()
            return json.loads(output.read_text()),guard.call_args.args[0]

    def test_missing_quote_base_or_sector_stops_without_overwriting_output(self):
        full={t:{'p':110} for t in ('A','B','C')}
        for quotes,bars,sectors,term in [({'A':{'p':110}},None,None,'报价'),
                (full,{},None,'基准'),(full,None,{'A':'One'},'行业'),
                ({t:{'p':4} for t in full},None,None,'完整三只')]:
            with self.subTest(term=term):
                result,_=self.run_preview(quotes,bars,sectors)
                self.assertIsInstance(result,str);self.assertIn(term,result)

    def test_invalid_price_cannot_silently_remove_candidate(self):
        for value in (0,float('nan'),float('inf'),True):
            quotes={t:{'p':110} for t in ('A','B','C')};quotes['A']['p']=value
            result,_=self.run_preview(quotes)
            self.assertIn('报价无效',result)

    def test_membership_and_base_year_follow_quote_session_instead_of_wall_clock(self):
        members=pd.DataFrame([{'ticker':t,'start_date':'2020-01-01','end_date':'2099-12-31'} for t in ('A','B','C')]
                            +[{'ticker':'FUTURE','start_date':'2026-01-01','end_date':'2099-12-31'}])
        result,checked=self.run_preview({t:{'p':110} for t in ('A','B','C')},session='2025-12-31',members=members)
        self.assertEqual(checked,{'A','B','C'})
        self.assertEqual(result['base_month'],'2024-12')
        self.assertEqual(result['quote_version'],'online-version')
        self.assertEqual(result['reference_version'],'reference-version')
        self.assertEqual(len(result['top']),3)
        self.assertEqual({r['ticker'] for r in result['top']},{'A','B','C'})
        self.assertFalse(result['final'])

    def test_unavailable_database_stops_before_reference_data_or_file_reads(self):
        with patch.object(preview.live_quotes,'load_snapshot',side_effect=RuntimeError('offline')), \
             patch.object(preview.db_ref,'load_market_reference') as members:
            with self.assertRaisesRegex(RuntimeError,'offline'):preview.main()
        members.assert_not_called()


if __name__=='__main__':
    unittest.main()
