import unittest
import tempfile
import json
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch
import quotes as quote_snapshot


class QuoteSnapshotTests(unittest.TestCase):
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


if __name__=="__main__":unittest.main()
