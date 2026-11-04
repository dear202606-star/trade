import unittest
from unittest.mock import MagicMock,patch
from research import apply_atvi_twtr_cash_exits as importer


class CashExitBoundaryTests(unittest.TestCase):
    def fixture(self):
        windows=sorted((f['symbol'],f['month_to'],f['url']) for f in importer.FACTS)
        receipts=[(f['symbol']+':'+f['event_date'],{'proof':importer.evidence(f),
                  'evidence_sha256':importer.digest(importer.evidence(f))}) for f in importer.FACTS]
        connection=MagicMock();cursor=connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value=(0,)
        return connection,cursor,windows,receipts

    def test_identical_review_does_not_rewrite_existing_history(self):
        connection,cursor,windows,receipts=self.fixture();cursor.fetchall.side_effect=[windows,receipts]
        with patch.object(importer.db_sources,'db',return_value=connection):importer.run(True)
        connection.commit.assert_not_called();connection.close.assert_called_once()
        self.assertFalse(any('insert into' in c.args[0].lower() for c in cursor.execute.call_args_list))

    def test_partial_or_changed_review_cannot_be_automatically_repaired(self):
        for mode in ('partial','changed'):
            connection,cursor,windows,receipts=self.fixture()
            if mode=='partial':receipts=receipts[:1]
            else:
                receipts[0][1]['proof']['holding_valuation_accepted']=True
                receipts[0][1]['evidence_sha256']=importer.digest(receipts[0][1]['proof'])
            cursor.fetchall.side_effect=[windows,receipts]
            with self.subTest(mode=mode),patch.object(importer.db_sources,'db',return_value=connection):
                with self.assertRaises(RuntimeError):importer.run(True)
            connection.commit.assert_not_called();connection.close.assert_called_once()


if __name__=='__main__':unittest.main()
