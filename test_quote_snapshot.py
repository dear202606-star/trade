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
            fields=['name',price,change,'time','0','0','0','0','0','0',volume]+['']*15+['1']
            self.assertIsNone(quote_snapshot.parse_us(fields))

    def test_derived_previous_close_cannot_overflow_or_round_to_zero(self):
        for price,change in [('1e308','-99'),('0.00000001','0'),('1','1e308')]:
            with self.subTest(price=price,change=change):
                self.assertIsNone(quote_snapshot.parse_us(['name',price,change,'time','0','0','0','0','0','0','10']+['']*15+['1']))
        fields=['name','10','25','time','2','0','0','0','0','0','10']+['']*15+['8']
        result=quote_snapshot.parse_us(fields)
        self.assertEqual((result['p'],result['pc'],result['chg']),(10,8,.25))

    def test_saved_quotes_match_independent_previous_closes(self):
        base=Path(__file__).resolve().parent/'output'
        quotes=json.loads((base/'quote_previous_close_probe.json').read_text())['rows']
        independent=json.loads((base/'quote_previous_close_independent_review.json').read_text())['rows']
        expected={row['symbol']:row['candidate_previous_close'] for row in independent}
        for row in quotes:
            symbol=row['symbol'].removeprefix('gb_').upper()
            with self.subTest(symbol=symbol):
                parsed=quote_snapshot.parse_us(row['raw_fields'])
                self.assertIsNotNone(parsed)
                self.assertEqual(parsed['pc'],expected[symbol])
                self.assertAlmostEqual(parsed['chg'],parsed['p']/parsed['pc']-1,places=6)

    def test_source_close_missing_invalid_or_conflicting_is_rejected(self):
        fields=['name','752.1000','-0.48','time','-3.6200','0','0','0','0','0','10']+['']*15+['755.7200']
        self.assertEqual(quote_snapshot.parse_us(fields)['pc'],755.72)
        self.assertIsNone(quote_snapshot.parse_us(fields[:11]))
        for index,value in [(26,'0'),(26,'nan'),(26,'700'),(4,'-4'),(2,'0.48')]:
            bad=list(fields);bad[index]=value
            with self.subTest(index=index,value=value):
                self.assertIsNone(quote_snapshot.parse_us(bad))


if __name__=="__main__":unittest.main()
