"""
test_ledger.py — Unit tests for telegraph.ledger state machine.

Tests:
  8.  approve before valid ingest → reject
  9.  READY → APPROVED
  10. APPROVED → first claim: CLAIM_STATUS=NEW
  11. duplicate claim (same packet/stage): CLAIM_STATUS=EXISTING, NEW_LAUNCH=NO,
      claims row count remains 1
  12. claim before approval → reject
  13. Receipt sequence monotonic
  14. Receipt previous hash chain valid
  15. Receipt tamper → verify failure
  16. HOLD → claim rejected
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from telegraph import packet as packet_mod
from telegraph.ledger import (
    Ledger,
    LedgerError,
    ReceiptChainError,
    StateTransitionError,
)


def _make_packet(**overrides):
    pkt = {
        "schema_version": 1,
        "mission_id": "TEST-LEDGER-001",
        "revision": 1,
        "project_id": "ledger-test",
        "objective": "Test the ledger state machine.",
        "action": "START_BUILDER",
        "profile_id": "builder-v1",
        "target": {
            "repo_root": "/tmp/test",
            "base_commit_oid": "b" * 40,
        },
        "retry_policy": "NEVER",
        "on_failure": "HOLD"
    }
    pkt.update(overrides)
    return packet_mod.validate(pkt)


class TestLedger(unittest.TestCase):

    def setUp(self):
        """Create a fresh temporary state directory and ledger for each test."""
        self._tmpdir = tempfile.mkdtemp()
        self.ledger = Ledger(self._tmpdir)
        self._pkt = _make_packet()
        self._sha = packet_mod.compute_sha256(self._pkt)

    def tearDown(self):
        self.ledger.close()

    def _ingest(self):
        return self.ledger.ingest(self._pkt, self._sha)

    def _approve(self):
        return self.ledger.approve(self._sha, "human-operator")

    def _claim(self, stage="BUILD"):
        return self.ledger.claim(self._sha, stage)

    # ------------------------------------------------------------------
    # Test 8 — approve before ingest → reject
    # ------------------------------------------------------------------
    def test_08_approve_before_ingest(self):
        """Approving an unknown packet raises LedgerError."""
        with self.assertRaises(LedgerError):
            self.ledger.approve("dead" * 16, "someone")  # 64-char fake SHA

    # ------------------------------------------------------------------
    # Test 9 — READY → APPROVED
    # ------------------------------------------------------------------
    def test_09_ready_to_approved(self):
        """Ingested packet can be approved; state becomes APPROVED."""
        self._ingest()
        result = self._approve()
        self.assertEqual(result["state"], "APPROVED")
        show = self.ledger.show(self._sha)
        self.assertEqual(show["state"], "APPROVED")

    # ------------------------------------------------------------------
    # Test 10 — first claim: CLAIM_STATUS=NEW
    # ------------------------------------------------------------------
    def test_10_first_claim_new(self):
        """First claim on an APPROVED packet returns CLAIM_STATUS=NEW."""
        self._ingest()
        self._approve()
        result = self._claim()
        self.assertEqual(result["claim_status"], "NEW")
        self.assertTrue(result["new_launch"])

    # ------------------------------------------------------------------
    # Test 11 — duplicate claim: CLAIM_STATUS=EXISTING, row count=1
    # ------------------------------------------------------------------
    def test_11_duplicate_claim_existing(self):
        """Second claim with same (packet, stage) returns EXISTING and no new row."""
        self._ingest()
        self._approve()
        self._claim()

        # Second attempt
        result2 = self._claim()
        self.assertEqual(result2["claim_status"], "EXISTING")
        self.assertFalse(result2["new_launch"])

        # Confirm only one row in claims table
        db_path = os.path.join(self._tmpdir, "ledger.db")
        conn = sqlite3.connect(db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM claims WHERE packet_sha256=? AND stage=?",
            (self._sha, "BUILD"),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    # ------------------------------------------------------------------
    # Test 12 — claim before approval → reject
    # ------------------------------------------------------------------
    def test_12_claim_before_approval(self):
        """Claiming a READY (not yet approved) packet raises StateTransitionError."""
        self._ingest()
        with self.assertRaises(StateTransitionError):
            self._claim()

    # ------------------------------------------------------------------
    # Test 13 — receipt sequence monotonic
    # ------------------------------------------------------------------
    def test_13_receipt_sequence_monotonic(self):
        """Receipt sequences increase monotonically starting from 1."""
        self._ingest()
        self._approve()
        self._claim()

        db_path = os.path.join(self._tmpdir, "ledger.db")
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT sequence FROM receipts WHERE packet_sha256=? ORDER BY sequence",
            (self._sha,),
        ).fetchall()
        conn.close()

        sequences = [r[0] for r in rows]
        self.assertEqual(sequences, list(range(1, len(sequences) + 1)))
        self.assertGreaterEqual(len(sequences), 3)  # INGEST, APPROVE, CLAIM

    # ------------------------------------------------------------------
    # Test 14 — receipt chain valid
    # ------------------------------------------------------------------
    def test_14_receipt_chain_valid(self):
        """verify() returns valid=True after a normal lifecycle."""
        self._ingest()
        self._approve()
        self._claim()

        result = self.ledger.verify(self._sha)
        self.assertTrue(result["valid"])
        self.assertGreaterEqual(result["receipts_verified"], 3)

    # ------------------------------------------------------------------
    # Test 15 — receipt tamper → verify failure
    # ------------------------------------------------------------------
    def test_15_receipt_tamper_fails_verify(self):
        """Tampering with a stored receipt SHA256 causes verify() to raise."""
        self._ingest()
        self._approve()

        # Tamper: overwrite the receipt_sha256 of the first receipt
        db_path = os.path.join(self._tmpdir, "ledger.db")
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            UPDATE receipts SET receipt_sha256='0000000000000000000000000000000000000000000000000000000000000000'
            WHERE packet_sha256=? AND sequence=1
            """,
            (self._sha,),
        )
        conn.commit()
        conn.close()

        with self.assertRaises(ReceiptChainError):
            self.ledger.verify(self._sha)

    # ------------------------------------------------------------------
    # Test 16 — HOLD → claim rejected
    # ------------------------------------------------------------------
    def test_16_hold_blocks_claim(self):
        """A packet in HOLD state cannot be claimed."""
        self._ingest()
        self._approve()
        self.ledger.hold(self._sha, "manual review required")

        with self.assertRaises(StateTransitionError):
            self._claim()

    # ------------------------------------------------------------------
    # Additional: claim before approval (READY state)
    # ------------------------------------------------------------------
    def test_12b_claim_from_ready_state(self):
        """Claim on READY packet (not APPROVED) raises StateTransitionError."""
        self._ingest()
        # Packet is READY, not APPROVED
        with self.assertRaises(StateTransitionError):
            self.ledger.claim(self._sha, "BUILD")

    # ------------------------------------------------------------------
    # Additional: receipt chain broken by previous_sha tamper
    # ------------------------------------------------------------------
    def test_14b_receipt_previous_sha_chain(self):
        """previous_receipt_sha256 links form a valid chain — tampering breaks it."""
        self._ingest()
        self._approve()
        self._claim()

        # Tamper: corrupt the previous_receipt_sha256 link on receipt 2
        db_path = os.path.join(self._tmpdir, "ledger.db")
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            UPDATE receipts SET previous_receipt_sha256='ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'
            WHERE packet_sha256=? AND sequence=2
            """,
            (self._sha,),
        )
        conn.commit()
        conn.close()

        with self.assertRaises(ReceiptChainError):
            self.ledger.verify(self._sha)

    # ------------------------------------------------------------------
    # F1 — HOLD must block ALL claims (including existing-claim duplicates)
    # ------------------------------------------------------------------

    def test_f1_hold_blocks_duplicate_claim(self):
        """
        After HOLD, even a duplicate claim on the same stage must be rejected
        with StateTransitionError — the HOLD check precedes idempotency.

        Scenario: ingest → approve → claim BUILD → hold → claim BUILD again
        """
        self._ingest()
        self._approve()
        self._claim("BUILD")
        self.ledger.hold(self._sha, "review")

        # A duplicate claim on the same stage after HOLD must still reject.
        with self.assertRaises(StateTransitionError):
            self._claim("BUILD")

    # ------------------------------------------------------------------
    # F2 — DISPATCHED must not create a new stage claim
    # ------------------------------------------------------------------

    def test_f2_dispatched_rejects_new_stage(self):
        """
        After approve → claim BUILD (→ DISPATCHED), claiming a different
        stage AUDIT must raise StateTransitionError.
        """
        self._ingest()
        self._approve()
        self._claim("BUILD")  # state → DISPATCHED

        with self.assertRaises(StateTransitionError):
            self.ledger.claim(self._sha, "AUDIT")

    def test_f2_dispatched_duplicate_same_stage_is_existing(self):
        """
        After DISPATCHED, a duplicate claim on the *same* stage returns
        EXISTING / NEW_LAUNCH=NO and does not insert a new row.
        """
        self._ingest()
        self._approve()
        self._claim("BUILD")  # state → DISPATCHED

        result = self._claim("BUILD")
        self.assertEqual(result["claim_status"], "EXISTING")
        self.assertFalse(result["new_launch"])

        # Only one claims row must exist.
        db_path = os.path.join(self._tmpdir, "ledger.db")
        conn = sqlite3.connect(db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM claims WHERE packet_sha256=? AND stage=?",
            (self._sha, "BUILD"),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    # ------------------------------------------------------------------
    # F3 — Transaction atomicity: claim failure leaves no partial state
    # ------------------------------------------------------------------

    def test_f3_claim_transaction_atomicity(self):
        """
        If _append_receipt raises during claim(), neither the claims row
        nor the state update must persist (full rollback).
        """
        from unittest.mock import patch

        self._ingest()
        self._approve()

        # Confirm baseline: state APPROVED, 0 claim rows.
        show_before = self.ledger.show(self._sha)
        self.assertEqual(show_before["state"], "APPROVED")
        self.assertEqual(len(show_before["claims"]), 0)

        # Make _append_receipt raise to simulate a mid-transaction failure.
        with patch.object(
            self.ledger,
            "_append_receipt",
            side_effect=RuntimeError("injected receipt failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.ledger.claim(self._sha, "BUILD")

        # State must still be APPROVED and claims table must be empty.
        db_path = os.path.join(self._tmpdir, "ledger.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT state FROM packets WHERE packet_sha256=?", (self._sha,)
        ).fetchone()
        claim_count = conn.execute(
            "SELECT COUNT(*) FROM claims WHERE packet_sha256=?", (self._sha,)
        ).fetchone()[0]
        receipt_count = conn.execute(
            "SELECT COUNT(*) FROM receipts WHERE from_state='APPROVED' AND to_state='DISPATCHED'",
            (),
        ).fetchone()[0]
        conn.close()

        self.assertEqual(row["state"], "APPROVED", "State must not have changed")
        self.assertEqual(claim_count, 0, "No claim row must exist")
        self.assertEqual(receipt_count, 0, "No DISPATCHED receipt must exist")

    def test_f3_approve_transaction_atomicity(self):
        """
        If _append_receipt raises during approve(), neither the approval row
        nor the state update must persist (full rollback).
        """
        from unittest.mock import patch

        self._ingest()

        show_before = self.ledger.show(self._sha)
        self.assertEqual(show_before["state"], "READY")

        with patch.object(
            self.ledger,
            "_append_receipt",
            side_effect=RuntimeError("injected receipt failure"),
        ):
            with self.assertRaises(RuntimeError):
                self._approve()

        db_path = os.path.join(self._tmpdir, "ledger.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT state FROM packets WHERE packet_sha256=?", (self._sha,)
        ).fetchone()
        approval_count = conn.execute(
            "SELECT COUNT(*) FROM approvals WHERE packet_sha256=?", (self._sha,)
        ).fetchone()[0]
        conn.close()

        self.assertEqual(row["state"], "READY", "State must remain READY")
        self.assertEqual(approval_count, 0, "No approval row must exist")



class TestIngestShaBinding(unittest.TestCase):
    """
    Regression tests for PACKET_SHA_BINDING invariant (FIRE-002).

    Tests A–E prove that Ledger.ingest() independently verifies the
    canonical SHA256 of the supplied packet body before any DB mutation.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.ledger = Ledger(self._tmpdir)
        self._pkt = _make_packet()
        self._sha = packet_mod.compute_sha256(self._pkt)

    def tearDown(self):
        self.ledger.close()

    def _row_counts(self):
        """Return (packets_count, receipts_count) from the live DB."""
        db_path = os.path.join(self._tmpdir, "ledger.db")
        conn = sqlite3.connect(db_path)
        pkt_count = conn.execute("SELECT COUNT(*) FROM packets").fetchone()[0]
        rcpt_count = conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
        conn.close()
        return pkt_count, rcpt_count

    # ------------------------------------------------------------------
    # Test A — correct SHA: direct Ledger.ingest succeeds
    # ------------------------------------------------------------------
    def test_a_correct_sha_succeeds(self):
        """Valid packet + exact canonical SHA must succeed (NEW → READY)."""
        result = self.ledger.ingest(self._pkt, self._sha)
        self.assertEqual(result["state"], "READY")
        self.assertEqual(result["packet_sha256"], self._sha)
        # DB must contain exactly one packet row and one receipt row.
        pkt_count, rcpt_count = self._row_counts()
        self.assertEqual(pkt_count, 1)
        self.assertEqual(rcpt_count, 1)

    # ------------------------------------------------------------------
    # Test B — wrong SHA: direct Ledger.ingest fails
    # ------------------------------------------------------------------
    def test_b_wrong_sha_rejected(self):
        """Supplying an incorrect SHA256 must raise LedgerError."""
        wrong_sha = "0" * 64
        with self.assertRaises(LedgerError):
            self.ledger.ingest(self._pkt, wrong_sha)

    # ------------------------------------------------------------------
    # Test C — wrong SHA leaves packets=0, receipts=0
    # ------------------------------------------------------------------
    def test_c_wrong_sha_zero_mutation(self):
        """After a wrong-SHA rejection the DB must be completely empty."""
        wrong_sha = "0" * 64
        try:
            self.ledger.ingest(self._pkt, wrong_sha)
        except LedgerError:
            pass
        pkt_count, rcpt_count = self._row_counts()
        self.assertEqual(pkt_count, 0, "packets table must remain empty")
        self.assertEqual(rcpt_count, 0, "receipts table must remain empty")

    # ------------------------------------------------------------------
    # Test D — invalid packet: direct Ledger.ingest fails closed, zero mutation
    # ------------------------------------------------------------------
    def test_d_invalid_packet_rejected_zero_mutation(self):
        """An invalid packet must be rejected before any DB mutation."""
        # Build an invalid packet (missing required field: wrong action)
        invalid_pkt = {
            "schema_version": 1,
            "mission_id": "TEST-FIRE-002",
            "revision": 1,
            "project_id": "fire-test",
            "objective": "Invalid packet test.",
            "action": "NOT_A_REAL_ACTION",  # invalid
            "profile_id": "builder-v1",
            "target": {
                "repo_root": "/tmp/test",
                "base_commit_oid": "a" * 40,
            },
            "retry_policy": "NEVER",
            "on_failure": "HOLD"
        }
        arbitrary_sha = "a" * 64
        with self.assertRaises(LedgerError):
            self.ledger.ingest(invalid_pkt, arbitrary_sha)
        pkt_count, rcpt_count = self._row_counts()
        self.assertEqual(pkt_count, 0, "packets table must remain empty")
        self.assertEqual(rcpt_count, 0, "receipts table must remain empty")

    # ------------------------------------------------------------------
    # Test E — one-character body change with old SHA must fail
    # ------------------------------------------------------------------
    def test_e_one_char_body_change_old_sha_fails(self):
        """A packet modified by one character but supplied with the original
        SHA must be rejected — the computed SHA will differ."""
        import copy

        # Compute canonical SHA for the original packet.
        original_sha = packet_mod.compute_sha256(self._pkt)

        # Modify one character in a string field (objective).
        modified_pkt = copy.deepcopy(self._pkt)
        modified_pkt["objective"] = self._pkt["objective"] + "X"

        # The modified packet is still schema-valid; only the SHA diverges.
        with self.assertRaises(LedgerError):
            self.ledger.ingest(modified_pkt, original_sha)

        pkt_count, rcpt_count = self._row_counts()
        self.assertEqual(pkt_count, 0, "packets table must remain empty")
        self.assertEqual(rcpt_count, 0, "receipts table must remain empty")




class TestLedgerBuilderResult(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_ledger.db")
        self.ledger = Ledger(self.db_path)
        self._valid_packet = {
            "schema_version": 1,
            "mission_id": "TEST-123",
            "revision": 1,
            "project_id": "test-proj",
            "action": "START_BUILDER",
            "profile_id": "prof-1",
            "objective": "test obj",
            "target": {"base_commit_oid": "a"*40, "repo_root": "repo"},
            "retry_policy": "NEVER",
            "on_failure": "HOLD"

        }
        self._valid_sha = packet_mod.compute_sha256(self._valid_packet)

    def tearDown(self):
        self.ledger.close()
        shutil.rmtree(self.temp_dir)

    def test_z_builder_result_success(self):
        self.ledger.ingest(self._valid_packet, self._valid_sha)
        packet_sha = self._valid_sha
        self.ledger.approve(packet_sha, "test_approver")
        self.ledger.claim(packet_sha, "BUILD")

        res = self.ledger.record_builder_result(
            packet_sha256=packet_sha, launched=True, exit_code=0, timed_out=False,
            started_at="2021", finished_at="2021"
        )
        self.assertEqual(res["exit_code"], 0)

        retrieved = self.ledger.get_builder_result(packet_sha)
        self.assertEqual(retrieved["exit_code"], 0)
        self.assertTrue(retrieved["launched"])

    def test_z_builder_result_duplicate_fails(self):
        self.ledger.ingest(self._valid_packet, self._valid_sha)
        packet_sha = self._valid_sha
        self.ledger.approve(packet_sha, "test_approver")
        self.ledger.claim(packet_sha, "BUILD")

        self.ledger.record_builder_result(
            packet_sha256=packet_sha, launched=True, exit_code=0, timed_out=False,
            started_at="2021", finished_at="2021"
        )

        from telegraph.ledger import StateTransitionError
        with self.assertRaisesRegex(StateTransitionError, "already recorded"):
            self.ledger.record_builder_result(
                packet_sha256=packet_sha, launched=True, exit_code=1, timed_out=False,
                started_at="2021", finished_at="2021"
            )

    def test_z_builder_result_invalid_state(self):
        self.ledger.ingest(self._valid_packet, self._valid_sha)
        packet_sha = self._valid_sha
        self.ledger.approve(packet_sha, "test_approver")

        from telegraph.ledger import StateTransitionError
        with self.assertRaisesRegex(StateTransitionError, "requires DISPATCHED"):
            self.ledger.record_builder_result(
                packet_sha256=packet_sha, launched=True, exit_code=0, timed_out=False,
                started_at="2021", finished_at="2021"
            )
