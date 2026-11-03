"""Focused regressions for free archive isolation and API quota accounting."""
import unittest
from unittest.mock import MagicMock, patch
import urllib.request
import tempfile
import json
import time
import io
import zipfile
from contextlib import ExitStack
from pathlib import Path

import db_select
import db_validate
import db_prices
import import_kaggle_cc0 as archive
import import_kaggle_yahoo2020 as yahoo_archive
import import_verified_renames as renames
import relay_source as relay
import site_build
import ingest_validate
import datafeed
import db_ref
import variant_b
import monthly_pick
import lock_stage
import quotes as quote_snapshot


class PipelineTests(unittest.TestCase):
    def test_quote_write_failures_preserve_last_snapshot_and_remove_temporary_files(self):
        def partial_write(payload,stream,**kwargs):
            stream.write('{partial')
            raise OSError('serialization interrupted')
        for stage in ('serialize','replace','nonfinite'):
            with self.subTest(stage=stage),tempfile.TemporaryDirectory() as directory,ExitStack() as stack:
                output=Path(directory)/'quotes.json';output.write_bytes(b'accepted bytes')
                stack.enter_context(patch.object(quote_snapshot,'OUT',str(output)))
                stack.enter_context(patch.object(quote_snapshot,'universe',return_value=['SPY']))
                stack.enter_context(patch.object(quote_snapshot,'fetch_quotes',return_value=({'SPY':{'p':100}},[])))
                stack.enter_context(patch.object(quote_snapshot,'fetch_fx',return_value=float('nan') if stage=='nonfinite' else 7))
                if stage=='serialize':stack.enter_context(patch.object(quote_snapshot.json,'dump',side_effect=partial_write))
                if stage=='replace':stack.enter_context(patch.object(quote_snapshot.os,'replace',side_effect=OSError('replace failed')))
                with self.assertRaises((OSError,ValueError)):quote_snapshot.main()
                self.assertEqual(output.read_bytes(),b'accepted bytes')
                self.assertEqual(list(Path(directory).iterdir()),[output])

    def test_incomplete_quote_snapshot_preserves_previous_file_and_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'quotes.json';output.write_text('previous accepted snapshot')
            for rows in ({'SPY':{}},{'OTHER':{}}):
                with self.subTest(rows=rows),patch.object(quote_snapshot,'OUT',str(output)), \
                     patch.object(quote_snapshot,'universe',return_value=['SPY','OTHER']), \
                     patch.object(quote_snapshot,'fetch_quotes',return_value=(rows,[])), \
                     patch.object(quote_snapshot,'fetch_fx') as fx:
                    with self.assertRaisesRegex(RuntimeError,'保留上一有效版本'):quote_snapshot.main()
                    self.assertEqual(output.read_text(),'previous accepted snapshot')
                    fx.assert_not_called()

    def test_complete_quote_snapshot_can_replace_previous_file(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(quote_snapshot,'OUT',str(Path(directory)/'quotes.json')), \
             patch.object(quote_snapshot,'universe',return_value=['SPY']), \
             patch.object(quote_snapshot,'fetch_quotes',return_value=({'SPY':{'p':100}},[])), \
             patch.object(quote_snapshot,'fetch_fx',return_value=7):
            quote_snapshot.main()
            result=json.loads(Path(quote_snapshot.OUT).read_text())
            self.assertTrue(result['ok'])
            self.assertEqual(result['quotes']['SPY']['p'],100)

    def test_nonfinite_and_impossible_quotes_are_rejected(self):
        for price,change,volume in [('nan','1','10'),('1','inf','10'),('1','1','-1'),('1','-100','10')]:
            fields=['name',price,change,'time','0','0','0','0','0','0',volume]
            self.assertIsNone(quote_snapshot.parse_us(fields))

    def test_derived_previous_close_cannot_overflow_or_round_to_zero(self):
        for price,change in [('1e308','-99'),('0.00000001','0'),('1','1e308')]:
            with self.subTest(price=price,change=change):
                self.assertIsNone(quote_snapshot.parse_us(['name',price,change,'time','0','0','0','0','0','0','10']))
        result=quote_snapshot.parse_us(['name','10','25','time','0','0','0','0','0','0','10'])
        self.assertEqual((result['p'],result['pc'],result['chg']),(10,8,.25))

    def test_failed_history_validation_cannot_be_forced_into_a_lock(self):
        with patch.object(lock_stage,'check',return_value=(1,{})), \
             patch.object(lock_stage,'db') as database:
            cursor=database.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value=None
            for force in (False,True):
                with self.subTest(force=force):
                    self.assertEqual(lock_stage.lock(2010,2025,force=force),1)
            self.assertEqual(cursor.execute.call_count,2)
            self.assertTrue(all(call.args[0].lstrip().startswith('select') for call in cursor.execute.call_args_list))

    def test_existing_history_lock_skips_measurement_and_writes(self):
        with patch.object(lock_stage,'db') as database,patch.object(lock_stage,'check') as check:
            cursor=database.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value=('accepted-original-hash',)
            self.assertEqual(lock_stage.lock(2010,2025),0)
            check.assert_not_called()
            cursor.execute.assert_called_once()
            self.assertTrue(cursor.execute.call_args.args[0].lstrip().startswith('select'))

    def test_monthly_signal_reads_prices_members_and_sectors_from_database(self):
        with patch.object(db_prices,'load_all',return_value={'TEST':{'2025-12':(12,13)}}), \
             patch.object(db_ref,'load_members',return_value={'TEST':[('2020-01-01','2099-12-31')]}), \
             patch.object(db_ref,'load_sectors',return_value={'TEST':'Tech'}):
            self.assertEqual(monthly_pick.fetch_history('TEST'),[['2025-12',12,13,None]])
            self.assertIsNone(monthly_pick.fetch_history('MISSING'))
            self.assertEqual(monthly_pick.fetch_sp500().to_dict('records'),
                             [{'ticker':'TEST','name':'TEST','sector':'Tech'}])
        with patch.object(db_prices,'load_all',side_effect=RuntimeError('offline')),self.assertRaises(RuntimeError):
            monthly_pick.fetch_history('TEST')

    def test_database_read_never_uses_disk_cache_even_when_requested(self):
        expected={'TEST':{'2025-12':(10,10)}}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(db_prices,'CACHE_FILE',str(Path(directory)/'snapshot.json')), \
             patch.object(db_prices,'_db_stamp',return_value=(1,'stamp',1,1,1,'10')), \
             patch.object(db_prices,'_cache_fresh',side_effect=AssertionError('disk cache consulted')), \
             patch.object(db_select,'load_all',return_value=expected) as online:
            self.assertEqual(db_prices.load_all(use_cache=True),expected)
            self.assertEqual(db_prices._load_all_legacy(use_cache=True),expected)
            self.assertEqual(online.call_count,2)

    def test_legacy_flag_cannot_enable_offline_prices(self):
        with patch.dict('os.environ',{'ALLOW_FILE_FALLBACK':'1'}), \
             patch.object(datafeed,'_db_all',return_value={}), \
             self.assertRaises(RuntimeError):
            datafeed.load_bars('TEST')
        with patch.dict('os.environ',{'ALLOW_FILE_FALLBACK':'1'}), \
             patch.object(variant_b,'_warm',side_effect=RuntimeError('offline')), \
             self.assertRaises(RuntimeError):
            variant_b.load_bars('TEST')

    def test_members_and_sector_constraints_are_online_only(self):
        with patch.object(db_ref,'load_members',return_value={'TEST':[('2020-01-01','2099-12-31')]}), \
             patch.object(db_ref,'load_sectors',return_value={'TEST':'Tech'}):
            self.assertEqual(datafeed.load_members().iloc[0]['ticker'],'TEST')
            self.assertEqual(datafeed.load_sector(),({'TEST':'Tech'},{'TEST':'TEST'}))
        for name,loader in [('load_members',datafeed.load_members),('load_sectors',datafeed.load_sector)]:
            with patch.object(db_ref,name,side_effect=RuntimeError('offline')),self.assertRaises(RuntimeError):loader()

    def test_selected_change_during_read_does_not_stamp_old_data_as_new(self):
        before=(100,'old',2,50,2,'1000')
        after=(101,'new',2,50,2,'1001')
        with tempfile.TemporaryDirectory() as directory:
            cache=Path(directory)/'cache.json'
            cache.write_text('preserved')
            with patch.object(db_prices,'CACHE_FILE',str(cache)), \
                 patch.object(db_prices,'_db_stamp',side_effect=[before,after]), \
                 patch.object(db_select,'load_all',return_value={'TEST':{'2020-12':(100,100)}}):
                with self.assertRaisesRegex(RuntimeError,'读取期间变化'):
                    db_prices.load_all(use_cache=False)
            self.assertEqual(cache.read_text(),'preserved')

    def test_ingest_rejects_nonfinite_prices_including_first_month(self):
        bars = {'2020-%02d' % month: (100, None) for month in range(1, 13)}
        for values in [(float('inf'), None), (float('nan'), None),
                       (100, float('nan')), (100, float('inf')), (100, 0),
                       ('100', None), (True, None), (100,)]:
            with self.subTest(values=values):
                bad = dict(bars, **{'2020-01': values})
                self.assertFalse(ingest_validate.check_payload(
                    'TEST', bad, 'total_return', allow_stale=True)[0])
        self.assertTrue(ingest_validate.check_payload(
            'TEST', bars, 'total_return', allow_stale=True)[0])

    def test_ingest_rejects_invalid_month_and_basis_without_exception(self):
        bars = {'2020-%02d' % month: (100, 100) for month in range(1, 13)}
        for month in ['2020-13', '0000-12', '2020-xx', None]:
            with self.subTest(month=month):
                bad = dict(bars)
                bad[month] = (100, 100)
                self.assertFalse(ingest_validate.check_payload(
                    'TEST', bad, 'total_return', allow_stale=True)[0])
        self.assertFalse(ingest_validate.check_payload(
            'TEST', bars, 'unknown', allow_stale=True)[0])

    def connection(self, module, result):
        connection = MagicMock()
        cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = result
        return patch.object(module, "db", return_value=connection), cursor

    def test_quota_reservation_at_limit_does_not_insert(self):
        context, cursor = self.connection(relay, (25,))
        with context:
            self.assertFalse(relay.reserve_av_call("TEST"))
        self.assertEqual(cursor.execute.call_count, 2)

    def test_quota_reserves_before_request(self):
        context, cursor = self.connection(relay, (24,))
        with context:
            self.assertTrue(relay.reserve_av_call("TEST"))
        self.assertIn("pg_advisory_xact_lock", cursor.execute.call_args_list[0].args[0])
        self.assertIn("insert into sys_event", cursor.execute.call_args.args[0])

    def test_quota_database_failure_is_closed(self):
        with patch.object(relay, "db", side_effect=RuntimeError("unavailable")):
            self.assertFalse(relay.reserve_av_call("TEST"))
            self.assertIsNone(relay.av_used_last_24h())

    def test_failed_upstream_still_consumes_reservation(self):
        with patch.object(relay, "reserve_av_call", return_value=True), \
             patch.object(relay, "fetch_av", side_effect=RuntimeError("upstream")), \
             patch.object(relay, "fetch_yahoo_relay", return_value={}), \
             patch.object(relay, "fetch_eodhd", return_value={}), \
             patch.object(relay, "fetch_hf", return_value={}), \
             patch.object(relay, "sina_local", return_value={}):
            rows, used, count = relay.relay("TEST", {"alphavantage_key": "test"}, 1)
        self.assertEqual((rows, used, count), ([], True, 0))

    def test_archive_registration_requires_database_guard(self):
        context, cursor = self.connection(archive.db_sources, None)
        with context, self.assertRaisesRegex(RuntimeError, "090_archive_selection_guard"):
            archive._register_source()
        self.assertEqual(cursor.execute.call_count, 1)

    def test_archive_excluded_from_candidate_query(self):
        cursor = MagicMock()
        db_select.compute_picks(cursor)
        self.assertIn("and source <> 'kaggle_cc0'", cursor.execute.call_args.args[0])
        self.assertIn('from series_quarantine', cursor.execute.call_args.args[0])
        self.assertIn("and source <> 'kaggle_yahoo2020'", cursor.execute.call_args.args[0])

    def test_yahoo_archive_uses_month_end_and_excludes_partial_april(self):
        raw = b'Date,Close,Adj Close\n2020-03-30,5,4\n2020-03-31,7,6\n2020-04-01,999,998\n'
        self.assertEqual(yahoo_archive.parse_monthly(raw), {'2020-03': (6.0, 7.0)})

    def test_yahoo_archive_rejects_nonfinite_price(self):
        with self.assertRaisesRegex(ValueError, 'nonfinite'):
            yahoo_archive.parse_monthly(b'Date,Close,Adj Close\n2019-12-31,50,nan\n')

    def test_yahoo_archive_rejects_duplicate_dates(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            yahoo_archive.parse_monthly(b'Date,Close,Adj Close\n2019-12-31,50,40\n2019-12-31,60,45\n')

    def test_second_archive_requires_its_own_database_guard(self):
        context, cursor = self.connection(archive.db_sources, None)
        with context, self.assertRaisesRegex(RuntimeError, '092_yahoo2020_archive_guard'):
            archive._register_source('kaggle_yahoo2020')
        self.assertEqual(cursor.execute.call_count, 1)

    def test_rename_rejects_unreviewed_canonical_source(self):
        cursor=MagicMock()
        cursor.fetchall.return_value=[('2010-01',100,100,'price_only','sina_repaired')]
        with self.assertRaisesRegex(RuntimeError,'reviewed Yahoo'):
            renames.check_prices(cursor,'OLD','NEW','2023-12')

    def test_rename_rejects_independent_return_mismatch(self):
        cursor=MagicMock()
        cursor.fetchone.return_value=(0,)
        months=['%d-%02d' % (2010+i//12,i%12+1) for i in range(120)]
        cursor.fetchall.side_effect=[
            [(m,100+i,100+i,'total_return','yahoo_relay') for i,m in enumerate(months)],
            [(m,100+3*i) for i,m in enumerate(months)]]
        with self.assertRaisesRegex(RuntimeError,'Independent return match failed'):
            renames.check_prices(cursor,'OLD','NEW','2023-12')

    def test_rename_rejects_nonfinite_canonical_close(self):
        cursor=MagicMock()
        cursor.fetchone.return_value=(0,)
        cursor.fetchall.return_value=[('2010-01',100,float('inf'),'total_return','yahoo_relay')]
        with self.assertRaisesRegex(RuntimeError,'invalid'):
            renames.check_prices(cursor,'OLD','NEW','2023-12')

    def test_rename_preserves_unapproved_quality_flags(self):
        cursor=MagicMock()
        cursor.fetchall.return_value=[('2010-01',100,100,'total_return','yahoo_relay')]
        cursor.fetchone.return_value=(1,)
        with self.assertRaisesRegex(RuntimeError,'unapproved quality'):
            renames.check_prices(cursor,'OLD','NEW','2023-12')

    def test_download_redirect_strips_credential(self):
        request = urllib.request.Request("https://www.kaggle.com/api/download", data=b"{}",
                                         headers={"Authorization": "Bearer test"})
        redirected = archive._SafeRedirect().redirect_request(
            request, None, 302, "Found", {}, "https://storage.googleapis.com/signed")
        self.assertFalse(redirected.has_header("Authorization"))

    def test_scheme_change_does_not_forward_kaggle_credential(self):
        request=urllib.request.Request('https://www.kaggle.com/download',headers={'Authorization':'Bearer test'})
        redirected=archive._SafeRedirect().redirect_request(request,None,302,'Found',{},'http://www.kaggle.com/file')
        self.assertFalse(redirected.has_header('Authorization'))

    def test_single_file_zip_response_is_read_without_extraction(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as zipped:
            zipped.writestr('BK.csv', 'Date,Close\n2019-12-31,50\n')
        self.assertEqual(archive.unpack_file(buffer.getvalue(), 'stocks/BK.csv'),
                         b'Date,Close\n2019-12-31,50\n')

    def test_unexpected_zip_members_are_rejected(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as zipped:
            zipped.writestr('OTHER.csv', 'unexpected')
        with self.assertRaises(RuntimeError):
            archive.unpack_file(buffer.getvalue(), 'stocks/BK.csv')

    def test_kaggle_rate_limit_stops_without_retrying(self):
        opener = MagicMock()
        opener.open.side_effect = archive.urllib.error.HTTPError('https://www.kaggle.com', 429, 'rate limit', {}, None)
        with patch.object(archive.urllib.request, 'build_opener', return_value=opener), \
             self.assertRaises(archive.KaggleRateLimit):
            archive.download_file('test', 'owner', 'dataset', 'BK.csv')
        opener.open.assert_called_once()

    def test_selected_reader_uses_selected_view_and_closes(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.__iter__.return_value = iter([('TEST', '2016-12', 12, None)])
        with patch.object(db_select, 'db', return_value=connection):
            self.assertEqual(db_select.load_all(), {'TEST': {'2016-12': (12.0, None)}})
        self.assertIn('from series_selected', cursor.execute.call_args.args[0])
        connection.close.assert_called_once()

    def test_selected_reader_rejects_empty_view(self):
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.fetchall.return_value = []
        with patch.object(db_select, 'db', return_value=connection), self.assertRaises(RuntimeError):
            db_select.load_all()
        connection.close.assert_called_once()

    def test_refresh_preserves_price_only_basis(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.side_effect = [[], [], []]
        cursor.fetchone.return_value = (12, 1)
        with patch.object(db_select, 'db', return_value=connection), \
             patch.object(db_select, 'compute_picks', return_value=[('TEST', 'hf_ayushkhaire', 'price_only')]), \
             patch.object(db_select.psycopg2.extras, 'execute_values') as write:
            db_select.refresh()
        self.assertEqual(write.call_args.args[2][0][2], 'price_only')
        self.assertIn('basis = excluded.basis', write.call_args.args[1])

    def test_refresh_corrects_basis_even_when_source_is_unchanged(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        def fetch():
            sql = cursor.execute.call_args.args[0]
            if sql == 'select symbol, source from series_pick':
                return [('TEST', 'hf_ayushkhaire')]
            if sql == 'select symbol, source, basis from series_pick':
                return [('TEST', 'hf_ayushkhaire', 'total_return')]
            return []
        cursor.fetchall.side_effect = fetch
        cursor.fetchone.return_value = (12, 1)
        with patch.object(db_select, 'db', return_value=connection), \
             patch.object(db_select, 'compute_picks', return_value=[('TEST', 'hf_ayushkhaire', 'price_only')]), \
             patch.object(db_select.psycopg2.extras, 'execute_values') as write:
            db_select.refresh()
        self.assertEqual(write.call_args.args[2][0][:3], ('TEST', 'hf_ayushkhaire', 'price_only'))

    def test_refresh_preserves_locked_or_frozen_basis_on_same_source(self):
        for locked, frozen in [([('TEST',)], []), ([], [('TEST',)])]:
            with self.subTest(locked=bool(locked), frozen=bool(frozen)):
                connection = MagicMock()
                cursor = connection.cursor.return_value.__enter__.return_value
                cursor.fetchall.side_effect = [[('TEST', 'hf_ayushkhaire', 'total_return')], locked, frozen]
                cursor.fetchone.return_value = (12, 1)
                with patch.object(db_select, 'db', return_value=connection), \
                     patch.object(db_select, 'compute_picks', return_value=[('TEST', 'hf_ayushkhaire', 'price_only')]), \
                     patch.object(db_select.psycopg2.extras, 'execute_values') as write:
                    db_select.refresh()
                write.assert_not_called()

    def test_database_validation_cannot_use_fixed_ratio(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(db_validate, 'BASE', tmp), \
             patch.object(db_validate, 'ROWS', []), \
             patch.object(db_validate, 'db', side_effect=RuntimeError('unavailable')), \
             patch.object(db_prices, 'load_all', return_value={}) as read, \
             patch('sys.argv', ['db_validate.py', '--data-only']):
            self.assertEqual(db_validate.main(), 1)
            self.assertTrue(any(status == 'fail' and name == '全收益口径读取'
                                for name, status, detail in db_validate.ROWS))
            self.assertFalse(any(name == '产物读取' for name, status, detail in db_validate.ROWS))
            read.assert_called_once_with(use_cache=False)

    def test_quality_reason_does_not_claim_only_one_historical_provider(self):
        reason=db_validate._reason_text(.8,{'need':100,'full':80,'missing':20,'delisted_gap':15})
        self.assertIn('历史数据仍待核验',reason)
        self.assertNotIn('仅 Alpha Vantage',reason)

    def test_offline_cache_cannot_bypass_new_quarantine(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'cache.json'
            path.write_text(json.dumps({'__stamp__': [1], '__built_at__': time.time()}))
            with patch.object(db_prices, 'CACHE_FILE', str(path)), \
                 patch.object(db_prices, '_db_stamp', return_value=None):
                self.assertFalse(db_prices._cache_fresh())

    def test_cache_requires_matching_live_stamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'cache.json'
            path.write_text(json.dumps({'__stamp__': [1]}))
            with patch.object(db_prices, 'CACHE_FILE', str(path)), \
                 patch.object(db_prices, '_db_stamp', return_value=(1,)):
                self.assertTrue(db_prices._cache_fresh())

    def test_build_writes_history_when_health_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / 'data'
            data.mkdir()
            for name in ('quotes', 'equity', 'signal'):
                (data / (name + '.json')).write_text(json.dumps({}), encoding='utf-8')
            with patch.object(site_build, 'DOCS', tmp), patch.object(site_build, 'DATA', str(data)), \
                 patch.object(site_build, 'HIST', str(root / 'missing.json')), \
                 patch.object(site_build, 'copy_site', return_value=0), \
                 patch.object(site_build, 'inject_nav'), patch.object(site_build, 'bust_cache'), \
                 patch.object(site_build.dashboard, 'build') as build:
                site_build.main()
            self.assertEqual(json.loads((data / 'history.json').read_text()), [])
            build.assert_called_once_with([], str(root / 'signals.html'))


if __name__ == "__main__":
    unittest.main()
