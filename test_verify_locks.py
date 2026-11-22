# -*- coding: utf-8 -*-
"""verify_locks 的判据回归：一次合规隔离不得把门禁永久钉红。

2026-11-05 SUN 被人工复核整票隔离（指数成员是 2012 年被收购的 Sunoco Inc，今天的
SUN 是 2014 年改名的 Sunoco LP，血统未复核）。隔离按设计把它从 v_series 里整票
摘掉，而 verify_locks 正是读 v_series 的——于是「序列缺失」每晚成立、作业每晚变红，
一直到有人记得把隔离判据补进来为止。原始数据和旧锁都要保留（用户口径），所以
红也不能靠删锁或改锁来消，只能让校验器自己分清「复核结论」和「真丢数据」。

这里同时钉住接线：classify 算得再对，main() 不去查 series_quarantine 也照样恒红。
"""
import contextlib
import io
import sys
import unittest
from unittest.mock import patch

import verify_locks as VL


def rows(months):
    return [(m, 10.0 + i) for i, m in enumerate(months)]


class ClassifyTests(unittest.TestCase):
    MONTHS = ["2012-01", "2012-02"]

    def lock_of(self, symbols, months=None):
        fingerprint = VL.hash_series(rows(months or self.MONTHS))
        return [(s, fingerprint) for s in symbols]

    def test_matching_fingerprint_is_ok(self):
        locks = self.lock_of(["A"])
        result = VL.classify(locks, lambda s: rows(self.MONTHS), set())
        self.assertEqual((result["ok"], result["drift"], result["missing"], result["held"]),
                         (1, 0, 0, 0))
        self.assertEqual(result["bad"], [])

    def test_reselected_series_is_drift_not_missing(self):
        locks = self.lock_of(["A"])
        result = VL.classify(locks, lambda s: rows(["2012-01", "2012-03"]), set())
        self.assertEqual(result["drift"], 1)
        self.assertEqual(result["missing"], 0)

    def test_quarantined_symbol_without_rows_is_held(self):
        locks = self.lock_of(["SUN"])
        result = VL.classify(locks, lambda s: [], {"SUN"})
        self.assertEqual((result["missing"], result["held"]), (0, 1))
        self.assertEqual(result["bad"], [])
        self.assertEqual(result["quarantined_locked"], ["SUN"])

    def test_missing_rows_for_a_live_symbol_is_still_a_failure(self):
        # 隔离名单只剩 SUN，别的票读不到序列就是真丢数据，不许跟着一起豁免。
        locks = self.lock_of(["SUN", "OTHER"])
        result = VL.classify(locks, lambda s: [], {"SUN"})
        self.assertEqual(result["held"], 1)
        self.assertEqual(result["missing"], 1)
        self.assertEqual(result["bad"], [("OTHER", "序列缺失")])

    def test_empty_quarantine_keeps_the_old_strict_behavior(self):
        locks = self.lock_of(["SUN"])
        result = VL.classify(locks, lambda s: [], set())
        self.assertEqual((result["missing"], result["held"]), (1, 0))
        self.assertEqual(result["bad"], [("SUN", "序列缺失")])


class FakeConnection:
    """只回答 verify_locks 真正发的那三条查询，并记录发过哪些。"""

    def __init__(self, locks, series, quarantine):
        self.locks, self.series, self.quarantine = locks, series, quarantine
        self.sent = []

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        pass


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, args=None):
        self.conn.sent.append(sql)
        if "from data_lock" in sql:
            self.rows = self.conn.locks
        elif "series_quarantine" in sql:
            self.rows = [(s,) for s in sorted(self.conn.quarantine)]
        elif "from v_series" in sql:
            self.rows = self.conn.series.get(args[0], [])
        else:
            raise AssertionError("verify_locks 发出了预期外的查询：%s" % sql[:120])

    def fetchall(self):
        return self.rows


class MainWiringTests(unittest.TestCase):
    def run_main(self, symbols, quarantine):
        locks = [(s, VL.hash_series(rows(["2012-01"]))) for s in symbols]
        conn = FakeConnection(locks, {"SUN": [], "OTHER": []}, quarantine)
        out = io.StringIO()
        with patch.object(VL, "db", lambda: conn), \
             patch.object(sys, "argv", ["verify_locks.py"]), \
             contextlib.redirect_stdout(out):
            code = VL.main()
        return out.getvalue(), code, conn

    def test_main_consults_the_quarantine_and_passes_on_a_held_lock(self):
        printed, code, conn = self.run_main(["SUN"], {"SUN"})
        self.assertTrue(any("series_quarantine" in sql for sql in conn.sent),
                        "main() 没有查隔离名单，隔离一旦落地就永久变红")
        self.assertIn("复核隔离 1", printed)
        self.assertIn("SUN", printed)
        self.assertEqual(code, 0, printed)

    def test_main_still_fails_for_a_live_symbol_without_series(self):
        printed, code, _conn = self.run_main(["SUN", "OTHER"], {"SUN"})
        self.assertIn("缺失 1", printed)
        self.assertEqual(code, 1, printed)


if __name__ == "__main__":
    unittest.main()
