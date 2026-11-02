"""Focused regressions for free archive isolation and API quota accounting."""
import unittest
from unittest.mock import MagicMock, patch
import urllib.request
import tempfile
import json
import time
import io
import zipfile
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


class PipelineTests(unittest.TestCase):
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
