"""
test_approved_ingress.py — Tests for the single deterministic approved ingress.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from telegraph.ledger import Ledger, LedgerError
from telegraph.packet import compute_sha256, validate, PacketValidationError
from telegraph.approved_ingress import submit_approved_packet


def _make_packet(**overrides):
    pkt = {
        "schema_version": 1,
        "mission_id": "TEST-INGRESS-001",
        "revision": 1,
        "project_id": "ingress-test",
        "objective": "Test the approved ingress.",
        "action": "START_BUILDER",
        "profile_id": "builder-v1",
        "target": {
            "repo_root": "/tmp/test",
            "base_commit_oid": "a" * 40,
        },
        "retry_policy": "NEVER",
        "on_failure": "HOLD",
    }
    pkt.update(overrides)
    return pkt


class TestApprovedIngress(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.ledger = Ledger(self._tmpdir)

    def tearDown(self):
        self.ledger.close()

    def test_A_valid_packet_to_approved(self):
        """A. valid packet -> APPROVED"""
        pkt = validate(_make_packet())
        sha = compute_sha256(pkt)

        result = submit_approved_packet(pkt, sha, "test-human", self.ledger)

        self.assertEqual(result["packet_sha256"], sha)
        self.assertEqual(result["state"], "APPROVED")
        self.assertEqual(result["ingest_status"], "NEW")
        self.assertEqual(result["approval_status"], "NEW")

        # Verify ledger state
        info = self.ledger.show(sha)
        self.assertEqual(info["state"], "APPROVED")
        self.assertEqual(len(info["approvals"]), 1)

    def test_B_exact_duplicate_idempotency(self):
        """B. exact duplicate -> idempotent, no duplicate approval"""
        pkt = validate(_make_packet())
        sha = compute_sha256(pkt)

        # First submission
        res1 = submit_approved_packet(pkt, sha, "test-human", self.ledger)
        self.assertEqual(res1["state"], "APPROVED")
        self.assertEqual(res1["ingest_status"], "NEW")

        # Second submission
        res2 = submit_approved_packet(pkt, sha, "test-human-2", self.ledger)
        self.assertEqual(res2["state"], "APPROVED")
        self.assertEqual(res2["ingest_status"], "EXISTING")
        self.assertEqual(res2["approval_status"], "EXISTING")

        # Ensure only ONE approval exists in the ledger
        info = self.ledger.show(sha)
        self.assertEqual(len(info["approvals"]), 1)
        self.assertEqual(info["approvals"][0]["approver"], "test-human")

    def test_C_malformed_packet_zero_mutation(self):
        """C. malformed packet -> zero mutation"""
        # Missing required fields
        pkt = {"schema_version": 999}
        sha = "fake-sha"

        with self.assertRaises(LedgerError):
            submit_approved_packet(pkt, sha, "test-human", self.ledger)

        # Verify zero mutation in ledger
        with self.assertRaises(LedgerError):
            self.ledger.show(sha)

        cur = self.ledger._conn.cursor()
        count = cur.execute("SELECT COUNT(*) FROM packets").fetchone()[0]
        self.assertEqual(count, 0)

    def test_D_wrong_sha_zero_mutation(self):
        """D. wrong SHA -> zero mutation, if applicable"""
        pkt = validate(_make_packet())
        wrong_sha = "f" * 64

        with self.assertRaises(LedgerError) as ctx:
            submit_approved_packet(pkt, wrong_sha, "test-human", self.ledger)

        self.assertIn("mismatch", str(ctx.exception))

        cur = self.ledger._conn.cursor()
        count = cur.execute("SELECT COUNT(*) FROM packets").fetchone()[0]
        self.assertEqual(count, 0)

    def test_E_forbidden_top_level(self):
        """E. forbidden top-level command/argv/env -> rejected"""
        pkt = _make_packet(shell_command="rm -rf /")
        sha = "fake"
        with self.assertRaises(LedgerError):
            submit_approved_packet(pkt, sha, "test-human", self.ledger)

    def test_F_nested_forbidden(self):
        """F. nested forbidden execution fields -> rejected"""
        pkt = _make_packet(target={
            "repo_root": "/tmp/test",
            "base_commit_oid": "a" * 40,
            "argv": ["--bad"]
        })
        sha = "fake"
        with self.assertRaises(LedgerError):
            submit_approved_packet(pkt, sha, "test-human", self.ledger)

    def test_G_cannot_select_paths(self):
        """G. packet cannot select DB/spool/path"""
        # The API signature does not accept paths from the packet.
        # It takes ledger: Ledger, which is constructed out-of-band.
        # We just assert the signature is what we expect.
        import inspect
        sig = inspect.signature(submit_approved_packet)
        self.assertIn("ledger", sig.parameters)
        self.assertEqual(sig.parameters["ledger"].annotation, Ledger)
        self.assertNotIn("db_path", sig.parameters)
        self.assertNotIn("spool_path", sig.parameters)

    def test_H_ingress_cannot_create_build_claim(self):
        """H. ingress cannot create BUILD claim"""
        pkt = validate(_make_packet())
        sha = compute_sha256(pkt)

        submit_approved_packet(pkt, sha, "test-human", self.ledger)

        info = self.ledger.show(sha)
        self.assertEqual(len(info["claims"]), 0)
        self.assertEqual(info["state"], "APPROVED")

    def test_I_no_subprocess(self):
        """I. ingress cannot launch subprocess"""
        # We enforce this statically/mechanically by not importing subprocess.
        import telegraph.approved_ingress as ingress
        self.assertFalse(hasattr(ingress, "subprocess"))
        self.assertFalse(hasattr(ingress, "os"))

    def test_J_state_never_advances_beyond_approved(self):
        """J. result state never advances beyond APPROVED"""
        pkt = validate(_make_packet())
        sha = compute_sha256(pkt)

        res = submit_approved_packet(pkt, sha, "test", self.ledger)
        self.assertEqual(res["state"], "APPROVED")

        # Submit again
        res2 = submit_approved_packet(pkt, sha, "test", self.ledger)
        self.assertEqual(res2["state"], "APPROVED")

    def test_K_objective_metacharacters(self):
        """K. objective shell metacharacters remain data"""
        pkt = validate(_make_packet(objective="echo 'hello' > /etc/shadow && rm -rf /"))
        sha = compute_sha256(pkt)

        submit_approved_packet(pkt, sha, "test", self.ledger)

        info = self.ledger.show(sha)
        self.assertEqual(info["action"], "START_BUILDER")
        # Ensure it was just stored, not executed
        # (if it executed, the test environment would likely fail or crash, but we also check it's purely DB)

    def test_L_dangerously_skip_permissions(self):
        """L. objective '--dangerously-skip-permissions' is DATA at ingress"""
        pkt = validate(_make_packet(objective="--dangerously-skip-permissions"))
        sha = compute_sha256(pkt)

        submit_approved_packet(pkt, sha, "test", self.ledger)

        info = self.ledger.show(sha)
        # Verify the ledger actually contains it as data
        cur = self.ledger._conn.cursor()
        row = cur.execute("SELECT packet_json FROM packets WHERE packet_sha256=?", (sha,)).fetchone()
        self.assertIn("--dangerously-skip-permissions", row[0])

if __name__ == "__main__":
    unittest.main()
