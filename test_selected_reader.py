import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock,patch

import db_prices
import db_rest
import db_select


class SelectedReaderTests(unittest.TestCase):
    def test_direct_reader_uses_one_guarded_cursor_and_keeps_missing_raw(self):
        connection=MagicMock();cursor=connection.cursor.return_value.__enter__.return_value
        cursor.__iter__.return_value=iter([('OPEN','2025-12',12,None)])
        with patch.object(db_select,'db',return_value=connection):
            self.assertEqual(db_select.load_all(),{'OPEN':{'2025-12':(12,None)}})
        connection.cursor.assert_called_once_with(name='selected_series')
        self.assertEqual(cursor.itersize,2000)
        cursor.execute.assert_called_once_with('select symbol, month, adj_close, raw_close from series_selected')
        connection.close.assert_called_once()

    def test_empty_or_failed_direct_read_closes_connection_and_stops(self):
        for failure in (None,RuntimeError('broken transfer')):
            connection=MagicMock();cursor=connection.cursor.return_value.__enter__.return_value
            cursor.__iter__.return_value=iter([])
            cursor.execute.side_effect=failure
            with self.subTest(failure=failure),patch.object(db_select,'db',return_value=connection):
                with self.assertRaises(RuntimeError):db_select.load_all()
            connection.close.assert_called_once()

    def test_only_transport_failure_can_switch_to_guarded_rest(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(db_prices,'CACHE_FILE',str(Path(directory)/'cache.json')), \
                patch.object(db_prices,'_db_stamp',return_value=None):
            for failure in (AttributeError('missing code'),ValueError('invalid price'),RuntimeError('empty view')):
                with self.subTest(failure=failure),patch.object(db_select,'load_all',side_effect=failure), \
                        patch.object(db_rest,'load_selected') as rest:
                    with self.assertRaises(type(failure)):db_prices.load_all()
                    rest.assert_not_called()
            with patch.object(db_select,'load_all',side_effect=db_prices.psycopg2.OperationalError('offline')), \
                    patch.object(db_rest,'load_selected',return_value={'OPEN':{'2025-12':(12,None)}}) as rest:
                self.assertEqual(db_prices.load_all(),{'OPEN':{'2025-12':(12,None)}})
                rest.assert_called_once_with()


if __name__=='__main__':unittest.main()
