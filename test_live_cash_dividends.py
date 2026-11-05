import io
import unittest

import openpyxl

from live_cash_dividends import spy_distributions,check_adjustment


class CashDistributionTests(unittest.TestCase):
    def workbook(self,cusip='78462F103',capital=''):
        workbook=openpyxl.Workbook();sheet=workbook.active;sheet.title='dividend'
        sheet.append(['FUND NAME','TICKER','CUSIP','EX-DATE','RECORD DATE','PAYABLE DATE','DIVIDEND ($)',
                      'SHORT TERM CAPITAL GAIN ($)','LONG TERM CAPITAL GAIN ($)','FREQUENCY'])
        sheet.append(['SPDR S&P 500','SPY',cusip,'09/18/2026','09/18/2026','10/30/2026','1.888834',capital,'','Quarterly'])
        output=io.BytesIO();workbook.save(output);return output.getvalue()

    def test_official_workbook_checks_benchmark_identity_and_distribution_type(self):
        rows=spy_distributions(self.workbook(),{'2026-09-18'})
        self.assertEqual(rows['2026-09-18']['amount'],1.888834)
        self.assertEqual(len(rows['2026-09-18']['workbook_sha256']),64)
        with self.assertRaises(ValueError):spy_distributions(self.workbook(cusip='wrong'))
        with self.assertRaises(ValueError):spy_distributions(self.workbook(capital='1'))
        self.assertEqual(spy_distributions(self.workbook(capital='1'),{'2026-12-18'}),{})

    def test_new_cash_amount_must_reconcile_to_source_factor(self):
        proofs={'X':{'2026-11-04':{'amount':1.}}}
        check_adjustment('X','2026-11-04',132,144,132/131,proofs)
        with self.assertRaises(ValueError):check_adjustment('X','2026-11-04',132,144,2,proofs)
        with self.assertRaises(ValueError):check_adjustment('X','2026-11-04',132,144,132/131,{})


if __name__=='__main__':unittest.main()
