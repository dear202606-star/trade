"""Reviewed identity resolutions may never silently unlock a quarantined series."""
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import db_ref
from research import security_identity_resolutions as resolutions

BASE = Path(__file__).resolve().parent


def proof(symbol, **overrides):
    p = {
        "symbol": symbol,
        "resolution": "exclude_reviewed",
        "scope": resolutions.SCOPE,
        "full_history_accepted": False,
        "membership_end": "2013-09-11",
        "external_evidence_status": "confirmed",
        "evidence_urls": [],
        "extreme_jump_count": 1,
    }
    p.update(overrides)
    return p


def row(symbol, p=None, **overrides):
    sha = overrides.pop("sha", resolutions.digest(p if p is not None else proof(symbol)))
    res = overrides.pop("resolution", "exclude_reviewed")
    return (symbol, res, p if p is not None else proof(symbol), sha)


def connection(quarantine, decision_rows):
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchall.side_effect = [quarantine, decision_rows]
    return conn


class TestResolutionDigest(unittest.TestCase):
    def test_reader_digest_matches_installer(self):
        p = proof("BMC")
        self.assertEqual(db_ref._resolution_digest(p), resolutions.digest(p))

    def test_digest_is_canonical_and_key_order_independent(self):
        a = {"a": 1, "b": {"x": "中文", "y": 2}}
        b = {"b": {"y": 2, "x": "中文"}, "a": 1}
        self.assertEqual(db_ref._resolution_digest(a), db_ref._resolution_digest(b))


class TestFailClosed(unittest.TestCase):
    def test_unreviewed_quarantine_still_blocks_with_original_message(self):
        with patch.object(db_ref, "db", return_value=connection(
                [("LSI", "Different issuer")], [])) as read:
            with self.assertRaisesRegex(RuntimeError, "LSI"):
                db_ref.require_unquarantined_candidates(["LSI", "ABC"])
        read.assert_called_once()

    def test_missing_resolution_table_behaves_exactly_like_before(self):
        conn = connection([("BMC", "Unreviewed jump")], [])

        def execute(sql, params=None):
            if "security_identity_resolution" in sql:
                raise RuntimeError("relation does not exist")
        conn.cursor.return_value.__enter__.return_value.execute.side_effect = execute
        with patch.object(db_ref, "db", return_value=conn):
            with self.assertRaisesRegex(RuntimeError, "BMC"):
                db_ref.require_unquarantined_candidates(["BMC"])

    def test_empty_candidate_set_never_opens_a_connection(self):
        with patch.object(db_ref, "db") as read:
            self.assertEqual(db_ref.require_unquarantined_candidates([]), [])
        read.assert_not_called()

    def test_database_failure_is_fail_closed(self):
        with patch.object(db_ref, "db", side_effect=RuntimeError("offline")):
            with self.assertRaisesRegex(RuntimeError, "身份隔离检查失败"):
                db_ref.require_unquarantined_candidates(["ABC"])

    def test_no_quarantine_rows_needs_no_resolution_lookup(self):
        conn = connection([], [])
        with patch.object(db_ref, "db", return_value=conn):
            self.assertEqual(
                db_ref.require_unquarantined_candidates(["ABC"]), [])
        self.assertEqual(
            conn.cursor.return_value.__enter__.return_value.fetchall.call_count, 1)


class TestTamperRejection(unittest.TestCase):
    def _assert_blocked(self, decision_row, symbol="LSI"):
        with patch.object(db_ref, "db", return_value=connection(
                [(symbol, "reason")], [decision_row])):
            with self.assertRaisesRegex(RuntimeError, symbol):
                db_ref.require_unquarantined_candidates([symbol])

    def test_tampered_digest_is_rejected(self):
        self._assert_blocked(row("LSI", sha="0" * 64))

    def test_pending_evidence_is_not_a_release(self):
        self._assert_blocked(row("LSI", resolution="pending_evidence"))

    def test_unknown_resolution_is_not_a_release(self):
        self._assert_blocked(row("LSI", resolution="auto_release"))

    def test_proof_claiming_complete_history_is_rejected(self):
        self._assert_blocked(row("LSI", p=proof("LSI", full_history_accepted=True)))

    def test_proof_for_another_symbol_is_rejected(self):
        self._assert_blocked(row("LSI", p=proof("BMC")))

    def test_malformed_row_is_rejected(self):
        self._assert_blocked(("LSI", "exclude_reviewed"))

    def test_non_dict_proof_is_rejected(self):
        self._assert_blocked(("LSI", "exclude_reviewed", "text", "abc"))

    def test_partial_coverage_blocks_only_unreviewed_symbols(self):
        rows = [("LSI", "reason"), ("BMC", "reason")]
        decisions = [row("LSI")]
        with patch.object(db_ref, "db", return_value=connection(rows, decisions)):
            with self.assertRaises(RuntimeError) as ctx:
                db_ref.require_unquarantined_candidates(["LSI", "BMC"])
        self.assertIn("BMC", str(ctx.exception))
        self.assertNotIn("LSI", str(ctx.exception))


class TestReviewedRelease(unittest.TestCase):
    def test_valid_resolution_returns_reviewed_symbols_without_unlocking(self):
        conn = connection([("LSI", "reason"), ("BMC", "reason")],
                          [row("LSI"), row("BMC")])
        with patch.object(db_ref, "db", return_value=conn):
            got = db_ref.require_unquarantined_candidates(["LSI", "BMC", "ABC"])
        self.assertEqual(got, ["BMC", "LSI"])
        conn.close.assert_called_once()


class TestWriteGuards(unittest.TestCase):
    def test_write_refuses_to_touch_neon_cold_backup(self):
        with patch.object(resolutions.db_sources, "current_target",
                          return_value="neon"):
            with self.assertRaisesRegex(RuntimeError, "只能作用于主库"):
                resolutions.run(write=True, decided_by="user-2026-11-08")

    def test_write_requires_an_explicit_approver(self):
        with patch.object(resolutions.db_sources, "current_target",
                          return_value="supabase"):
            with self.assertRaisesRegex(RuntimeError, "--decided-by"):
                resolutions.run(write=True)


class TestLedgerIntegrity(unittest.TestCase):
    def test_ledger_excludes_current_member_p(self):
        self.assertEqual(len(resolutions.SYMBOLS), 13)
        self.assertNotIn("P", resolutions.SYMBOLS)
        self.assertEqual(len(set(resolutions.SYMBOLS)), len(resolutions.SYMBOLS))

    def test_every_symbol_has_external_evidence_entry(self):
        self.assertEqual(set(resolutions.EXTERNAL), set(resolutions.SYMBOLS))
        for symbol, (issuer, status, urls) in resolutions.EXTERNAL.items():
            self.assertTrue(issuer.strip(), symbol)
            self.assertIn(status, ("confirmed", "partial"), symbol)
            self.assertIsInstance(urls, list, symbol)
            for url in urls:
                self.assertTrue(url.startswith("https://"), (symbol, url))

    def test_suspended_scope_never_claims_complete_history(self):
        self.assertIn("不解锁", resolutions.SCOPE)
        self.assertIn("不代表 full_history_accepted", resolutions.SCOPE)

    def test_sql_is_append_only_and_constrained(self):
        sql = (BASE / "sql" / "125_security_identity_resolutions.sql").read_text(
            encoding="utf-8")
        self.assertIn("check (resolution in ('exclude_reviewed','pending_evidence'))", sql)
        self.assertIn("before insert or update or delete", sql)
        self.assertIn("before truncate", sql)
        self.assertIn("append-only", sql)
        self.assertNotIn("update public.prices_monthly", sql)
        self.assertNotIn("delete from public.series_quarantine", sql)


if __name__ == "__main__":
    unittest.main()
