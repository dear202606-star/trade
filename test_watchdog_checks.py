import unittest

from watchdog_checks import quality_problems


class QualityReportTests(unittest.TestCase):
    def report(self, statuses=('pass',), degraded=False):
        return {'summary': {'fail': statuses.count('fail')},
                'degraded': degraded,
                'checks': [{'status': status} for status in statuses]}

    def test_valid_report(self):
        self.assertEqual(quality_problems(self.report(('pass', 'warn'))), [])

    def test_published_failure_and_degradation(self):
        self.assertEqual(len(quality_problems(self.report(('pass', 'fail'), True))), 2)

    def test_missing_or_malformed_evidence(self):
        for report in (None, [], {}, {'summary': {}},
                       dict(self.report(), degraded='false'),
                       dict(self.report(), checks=[]),
                       dict(self.report(), checks=[{'status': 'unknown'}]),
                       dict(self.report(), summary={'fail': True}),
                       dict(self.report(), summary={'fail': -1}),
                       dict(self.report(), summary={'fail': 1})):
            with self.subTest(report=report), self.assertRaises(ValueError):
                quality_problems(report)


if __name__ == '__main__':
    unittest.main()
