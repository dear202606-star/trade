import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock,patch

import publish_pipeline_status as status
import runner

ENV={'GITHUB_RUN_ID':'1234','GITHUB_RUN_ATTEMPT':'2','GITHUB_JOB':'pick',
     'GITHUB_SHA':'a'*40,'PIPELINE_JOB_STATUS':'failure'}


def data(rc=0):
    return {'runs':[{'run_id':'1234','attempt':'2','job':'pick','commit':'a'*40,
        'ts':'2026-11-04T03:00:00Z','step':'example.py','rc':rc,'ok':rc==0,
        'dur':1.5,'tail':'private console output'}]}


class PipelineStatusTests(unittest.TestCase):
    def test_only_current_attempt_and_job_records_are_published_without_console_tails(self):
        records=data(7)
        records['runs'] += [dict(records['runs'][0],attempt='1'),{'step':'legacy.py','rc':0}]
        receipt=status.snapshot(records,ENV)
        self.assertEqual(len(receipt['runs']),1)
        self.assertEqual(receipt['runs'][0]['rc'],7)
        self.assertNotIn('tail',receipt['runs'][0])
        self.assertEqual(receipt['job_status_at_capture'],'failure')
        self.assertEqual(len(receipt['evidence_sha256']),64)

    def test_missing_current_receipt_or_mixed_commit_or_invalid_outcome_is_rejected(self):
        for value in ({'runs':[]}, {'runs':[dict(data()['runs'][0],commit='b'*40)]},
                      {'runs':[dict(data()['runs'][0],ok=False)]},
                      {'runs':[dict(data()['runs'][0],dur=float('nan'))]}):
            with self.subTest(value=value),self.assertRaises(ValueError):status.snapshot(value,ENV)
        with self.assertRaises(ValueError):status.snapshot(data(),{})

    def test_replay_skips_identical_receipt_but_never_overwrites_conflicting_receipt(self):
        receipt=status.snapshot(data(7),ENV)
        connection=MagicMock();cursor=connection.cursor.return_value.__enter__.return_value
        with patch('db_sources.db',return_value=connection):
            cursor.fetchall.return_value=[]
            self.assertTrue(status.publish(receipt))
            self.assertTrue(any('insert into sys_event' in c.args[0] for c in cursor.execute.call_args_list))
            cursor.reset_mock();cursor.fetchall.return_value=[(receipt,'fail')]
            self.assertFalse(status.publish(receipt))
            self.assertFalse(any('insert into sys_event' in c.args[0] for c in cursor.execute.call_args_list))
            other=copy.deepcopy(receipt);other['runs'][0]['rc']=8
            cursor.fetchall.return_value=[(other,'fail')]
            with self.assertRaises(RuntimeError):status.publish(receipt)

    def test_database_failure_is_not_reported_as_stored(self):
        with patch('db_sources.db',side_effect=RuntimeError('offline')):
            with self.assertRaisesRegex(RuntimeError,'offline'):status.publish(status.snapshot(data(),ENV))

    def test_real_child_exit_code_is_preserved_and_status_write_failure_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,ENV):
            script=Path(directory)/'child.py';script.write_text('import sys\nsys.exit(int(sys.argv[1]))\n')
            path=Path(directory)/'receipt.json'
            with patch.object(runner,'STATUS',str(path)):
                for rc in (0,7):
                    with patch.object(sys,'argv',['runner.py',str(script),str(rc)]):
                        self.assertEqual(runner.main(),rc)
                rows=json.loads(path.read_text())['runs']
                self.assertEqual([r['rc'] for r in rows],[0,7])
                self.assertTrue(all(r['run_id']=='1234' for r in rows))
                path.write_text('not JSON')
                for rc in (0,7):
                    with patch.object(sys,'argv',['runner.py',str(script),str(rc)]):
                        self.assertEqual(runner.main(),rc or 1)


if __name__=='__main__':
    unittest.main()
