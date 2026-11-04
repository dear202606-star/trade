import copy
from decimal import Decimal
import unittest
from unittest.mock import MagicMock,patch

from research import apply_2026_spinoff_terms as importer


class SpinoffTermsTests(unittest.TestCase):
    def rows(self):
        return [(t['symbol']+':'+t['event_date'],
                 {'proof':importer.proof(t),'evidence_sha256':importer.digest(importer.proof(t))})
                for t in importer.TERMS]

    def test_hon_distribution_and_reverse_split_use_original_share_units(self):
        hon=next(t for t in importer.TERMS if t['symbol']=='HON')
        # Two pre-event shares become one retained and one distributed share.
        self.assertEqual(Decimal(hon['retained_parent_units_per_original_share'])*2,1)
        self.assertEqual(Decimal(hon['distributed_units_per_original_share'])*2,1)
        self.assertFalse(importer.proof(hon)['holding_valuation_accepted'])

    def test_partial_duplicate_corrupt_or_changed_terms_are_not_overwritten(self):
        rows=self.rows();importer.verify_existing(rows)
        bad=copy.deepcopy(rows);bad[0][1]['evidence_sha256']='0'*64
        changed=copy.deepcopy(rows);changed[0][1]['proof']['terms']['distributed_units_per_original_share']='1'
        changed[0][1]['evidence_sha256']=importer.digest(changed[0][1]['proof'])
        for values in (rows[:1],rows+rows[:1],bad,changed):
            with self.subTest(values=values),self.assertRaises(RuntimeError):
                importer.verify_existing(values)

    def test_identical_repeat_writes_nothing_and_closes_transaction(self):
        connection=MagicMock();cursor=connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value=self.rows()
        with patch.object(importer.db_sources,'db',return_value=connection):
            importer.run(True)
        connection.commit.assert_not_called()
        connection.close.assert_called_once()
        self.assertTrue(connection.rollback.called)
        self.assertFalse(any('insert into' in c.args[0].lower() for c in cursor.execute.call_args_list))


if __name__=='__main__':unittest.main()
